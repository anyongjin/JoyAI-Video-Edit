import base64
import io
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xvideo.serving.live_api import install_live_api, model_dimensions


def test_key_sessions_and_optional_detection(monkeypatch):
    monkeypatch.setenv("JOYAI_API_KEY", "test-key")
    monkeypatch.delenv("JOYAI_API_KEY_FILE", raising=False)
    app = FastAPI()

    async def echo(ws):
        await ws.accept(subprotocol=ws.state.api_subprotocol)
        await ws.send_json(ws.state.api_start)
        await ws.send_bytes(await ws.receive_bytes())

    install_live_api(app, echo)
    headers = {"Authorization": "Bearer test-key"}
    with TestClient(app) as client:
        assert (
            client.post("/api/v1/live/sessions", json={"prompt": "edit"}).status_code
            == 401
        )
        assert (
            client.post(
                "/api/v1/live/sessions",
                headers=headers,
                json={"prompt": "edit", "refImage": "broken"},
            ).status_code
            == 422
        )
        image = io.BytesIO()
        Image.new("RGB", (64, 64), "red").save(image, "PNG")
        for detection in (False, True):
            body = {
                "prompt": "edit",
                "refImage": base64.b64encode(image.getvalue()).decode(),
            }
            if detection:
                body["detectPerson"] = True
            response = client.post("/api/v1/live/sessions", headers=headers, json=body)
            assert response.status_code == 201
            session = response.json()
            assert session["detectPerson"] is detection
            path = f"/api/v1/live/sessions/{session['sessionId']}/ws"
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(path, subprotocols=["wrong"]):
                    pass
            with client.websocket_connect(
                path, subprotocols=[session["sessionToken"]]
            ) as ws:
                assert ws.receive_json()["gate_enabled"] is detection
                ws.send_bytes(b"frame")
                assert ws.receive_bytes() == b"frame"
            assert session["sessionId"] not in app.state.api_sessions
        session = client.post(
            "/api/v1/live/sessions", headers=headers, json={"prompt": "edit"}
        ).json()
        app.state.api_sessions[session["sessionId"]]["expiresAt"] = time.time() - 1
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                session["websocketUrl"], subprotocols=[session["sessionToken"]]
            ):
                pass
        assert (
            client.delete(
                f"/api/v1/live/sessions/{session['sessionId']}", headers=headers
            ).status_code
            == 200
        )


def test_portrait_settings_and_authorized_reference_updates(monkeypatch):
    monkeypatch.setenv("JOYAI_API_KEY", "test-key")
    monkeypatch.delenv("JOYAI_API_KEY_FILE", raising=False)
    app = FastAPI()

    async def echo(ws):
        await ws.accept(subprotocol=ws.state.api_subprotocol)
        for _ in range(2):
            await ws.receive_json()
            await ws.send_json(ws.state.api_start)

    install_live_api(app, echo)
    headers = {"Authorization": "Bearer test-key"}
    image = io.BytesIO()
    Image.new("RGB", (64, 64), "blue").save(image, "PNG")
    reference = base64.b64encode(image.getvalue()).decode()
    with TestClient(app) as client:
        created = client.post("/api/v1/live/sessions", headers=headers, json={
            "prompt": "edit", "fps": 15, "numInferenceSteps": 3, "outputQuality": 80,
        })
        assert created.status_code == 201
        session = created.json()
        assert (session["width"], session["height"], session["fps"]) == (768, 1024, 15)
        path = f"/api/v1/live/sessions/{session['sessionId']}"
        with client.websocket_connect(path + "/ws", subprotocols=[session["sessionToken"]]) as ws:
            ws.send_json({"type": "start", "width": 2048, "ref_image": "untrusted"})
            started = ws.receive_json()
            assert (started["width"], started["height"], started["fps"]) == (768, 1024, 15)
            assert started["num_inference_steps"] == 3 and started["output_quality"] == 80
            assert client.patch(path + "/reference", json={"refImage": reference}).status_code == 401
            assert client.patch(path + "/reference", headers=headers, json={"refImage": "broken"}).status_code == 422
            updated = client.patch(path + "/reference", headers=headers, json={"refImage": reference})
            assert updated.status_code == 200
            ws.send_json({"type": "start"})
            restarted = ws.receive_json()
            assert restarted["ref_image"] == reference
            assert restarted["session_id"] == session["sessionId"]
            assert restarted["fps"] == 15
        for settings in [{"fps": 0}, {"fps": 61}, {"width": 2048, "height": 2048}, {"outputQuality": 101}]:
            assert client.post("/api/v1/live/sessions", headers=headers, json={"prompt": "edit", **settings}).status_code == 422


def test_model_dimensions_preserve_requested_orientation_and_only_add_padding():
    assert model_dimensions(768, 1024, 24) == (768, 1032)
    assert model_dimensions(360, 480, 24) == (360, 480)
    assert model_dimensions(480, 640, 24) == (480, 648)
    assert model_dimensions(1248, 720, 24) == (1248, 720)
    with pytest.raises(ValueError):
        model_dimensions(2048, 2048, 24)


def test_end_session_tolerates_socket_already_closed(monkeypatch):
    monkeypatch.setenv("JOYAI_API_KEY", "test-key")
    monkeypatch.delenv("JOYAI_API_KEY_FILE", raising=False)
    app = FastAPI()
    install_live_api(app, None)

    async def close(code):
        raise RuntimeError("Unexpected ASGI message after WebSocket closed")

    headers = {"Authorization": "Bearer test-key"}
    with TestClient(app) as client:
        session = client.post("/api/v1/live/sessions", headers=headers, json={"prompt": "edit"}).json()
        app.state.api_sessions[session["sessionId"]]["socket"] = SimpleNamespace(close=close)
        ended = client.delete("/api/v1/live/sessions/" + session["sessionId"], headers=headers)
        assert ended.status_code == 200
        assert session["sessionId"] not in app.state.api_sessions
