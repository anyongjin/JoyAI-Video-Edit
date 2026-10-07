import base64
import io
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xvideo.serving.live_api import install_live_api


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
