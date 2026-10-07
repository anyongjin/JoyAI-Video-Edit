"""Key-authorized sessions over the existing JoyAI frame protocol."""

import asyncio
import base64
import hashlib
import hmac
import io
import os
import secrets
import time
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, WebSocket
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator


def model_dimensions(width: int, height: int, align: int) -> tuple[int, int]:
    if not (64 <= width <= 2048 and 64 <= height <= 2048) or width * height > 1024 * 1024:
        raise ValueError("Stream dimensions exceed the supported one-megapixel budget")
    return ((width + align - 1) // align * align, (height + align - 1) // align * align)


class CreateSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=4000)
    refImage: str | None = Field(default=None, max_length=8 * 1024 * 1024)
    detectPerson: bool = False
    outputCodec: Literal["mjpeg", "h264"] = "mjpeg"
    width: int = Field(default=768, ge=64, le=2048)
    height: int = Field(default=1024, ge=64, le=2048)
    fps: int = Field(default=25, ge=1, le=60)
    numInferenceSteps: int = Field(default=2, ge=1, le=4)
    outputQuality: int = Field(default=85, ge=1, le=100)
    profileTimings: bool = False

    @model_validator(mode="after")
    def check_dimensions(self):
        model_dimensions(self.width, self.height, 1)
        return self


class UpdateReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refImage: str = Field(min_length=1, max_length=8 * 1024 * 1024)


def api_key() -> str:
    path = os.getenv("JOYAI_API_KEY_FILE")
    return (Path(path).read_text() if path else os.getenv("JOYAI_API_KEY", "")).strip()


def authorize(request: Request) -> str:
    key = api_key()
    if not key:
        raise HTTPException(503, "API key is not configured")
    supplied = request.headers.get("authorization", "")
    if not hmac.compare_digest(supplied.encode(), ("Bearer " + key).encode()):
        raise HTTPException(
            401, "Invalid API key", headers={"WWW-Authenticate": "Bearer"}
        )
    return hashlib.sha256(key.encode()).hexdigest()


def validate_reference(value: str | None) -> None:
    if value is None:
        return
    try:
        raw = value.split(",", 1)[1] if value.startswith("data:") else value
        data = base64.b64decode(raw, validate=True)
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 16_000_000:
                raise ValueError("Reference exceeds 16 megapixels")
            image.verify()
    except Exception as error:
        raise HTTPException(422, "Invalid reference image") from error


def install_live_api(app: FastAPI, stream_handler) -> None:
    # ponytail: session state is per process; use shared storage before adding replicas.
    sessions: dict[str, dict] = {}
    app.state.api_sessions = sessions

    @app.post("/api/v1/live/sessions", status_code=201)
    async def create(body: CreateSession, request: Request):
        owner = authorize(request)
        await asyncio.to_thread(validate_reference, body.refImage)
        now = time.time()
        for sid, row in list(sessions.items()):
            if row["expiresAt"] <= now and not row["active"]:
                sessions.pop(sid)
        if len(sessions) >= 64:
            raise HTTPException(429, "Session capacity reached")
        sid, token = secrets.token_hex(16), secrets.token_urlsafe(32)
        row = {
            "owner": owner,
            "tokenHash": hashlib.sha256(token.encode()).hexdigest(),
            "expiresAt": now + 300,
            "active": False,
            "socket": None,
            "start": {
                "prompt": body.prompt,
                "ref_image": body.refImage,
                "gate_enabled": body.detectPerson,
                "use_pe": False,
                "width": body.width,
                "height": body.height,
                "fps": body.fps,
                "num_inference_steps": body.numInferenceSteps,
                "output_quality": body.outputQuality,
                "profile_timings": body.profileTimings,
                "input_codec": "mjpeg",
                "output_codec": body.outputCodec,
                "source": "camera",
                "session_id": sid,
            },
        }
        sessions[sid] = row
        scheme = (
            "wss"
            if request.url.scheme == "https"
            or request.headers.get("x-forwarded-proto") == "https"
            else "ws"
        )
        url = str(
            request.url_for("joyai_api_socket", session_id=sid).replace(scheme=scheme)
        )
        return {
            "sessionId": sid,
            "sessionToken": token,
            "websocketUrl": url,
            "expiresAt": row["expiresAt"],
            "detectPerson": body.detectPerson,
            "width": body.width,
            "height": body.height,
            "fps": body.fps,
            "transport": "websocket",
            "inputCodec": "mjpeg",
            "outputCodec": body.outputCodec,
        }

    @app.patch("/api/v1/live/sessions/{session_id}/reference")
    async def update_reference(session_id: str, body: UpdateReference, request: Request):
        owner = authorize(request)
        row = sessions.get(session_id)
        if not row or row["owner"] != owner or row["expiresAt"] <= time.time():
            raise HTTPException(404, "Session not found")
        await asyncio.to_thread(validate_reference, body.refImage)
        if sessions.get(session_id) is not row:
            raise HTTPException(404, "Session not found")
        # The active socket shares this dict; its next start resets model history.
        row["start"]["ref_image"] = body.refImage
        return {"status": "updated"}

    @app.delete("/api/v1/live/sessions/{session_id}")
    async def end(session_id: str, request: Request):
        owner = authorize(request)
        row = sessions.get(session_id)
        if not row or row["owner"] != owner:
            raise HTTPException(404, "Session not found")
        sessions.pop(session_id)
        if row["socket"] is not None:
            try:
                await row["socket"].close(code=1000)
            except RuntimeError:
                pass  # The browser may already have closed the ASGI socket.
        return {"status": "ended"}

    @app.websocket("/api/v1/live/sessions/{session_id}/ws", name="joyai_api_socket")
    async def socket(websocket: WebSocket, session_id: str):
        row = sessions.get(session_id)
        token = websocket.headers.get("sec-websocket-protocol", "")
        key = api_key()
        key_authorized = bool(key) and hmac.compare_digest(
            websocket.headers.get("authorization", "").encode(),
            ("Bearer " + key).encode(),
        )
        if not token:
            token = websocket.headers.get("authorization", "").removeprefix("Bearer ")
        if (
            not row
            or row["expiresAt"] <= time.time()
            or row["active"]
            or row["owner"] != hashlib.sha256(key.encode()).hexdigest()
            or not (
                key_authorized
                or hmac.compare_digest(
                    hashlib.sha256(token.encode()).hexdigest(), row["tokenHash"]
                )
            )
        ):
            await websocket.close(code=1008)
            return
        row["active"] = True
        row["socket"] = websocket
        websocket.state.api_start = row["start"]
        websocket.state.api_subprotocol = (
            token if websocket.headers.get("sec-websocket-protocol") else None
        )
        task = asyncio.create_task(stream_handler(websocket))
        try:
            await asyncio.wait_for(
                task, timeout=max(0.01, row["expiresAt"] - time.time())
            )
        except asyncio.TimeoutError:
            try:
                await websocket.close(code=1000)
            except RuntimeError:
                pass
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            sessions.pop(session_id, None)
