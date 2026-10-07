"""Exercise public API authorization and three real reference-guided chunks."""

import argparse
import asyncio
import base64
import io
import json
import os
from pathlib import Path
import time

import httpx
from PIL import Image, ImageOps, ImageStat
from websockets.asyncio.client import connect


async def smoke(args):
    key = os.environ["JOYAI_API_KEY"]
    headers = {"Authorization": "Bearer " + key}
    started_at = time.monotonic()
    async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
        for authorization in ({}, {"Authorization": "Bearer invalid"}):
            denied = await client.post(
                args.url + "/api/v1/live/sessions",
                headers=authorization,
                json={"prompt": args.prompt},
            )
            assert denied.status_code == 401
        response = await client.post(
            args.url + "/api/v1/live/sessions",
            headers=headers,
            json={
                "prompt": args.prompt,
                "detectPerson": False,
                "profileTimings": True,
                "refImage": base64.b64encode(args.reference.read_bytes()).decode(),
            },
        )
        response.raise_for_status()
        session = response.json()
        assert session["websocketUrl"].startswith("wss://")
        args.output.mkdir(parents=True, exist_ok=True)
        frames_in = frames_out = 0
        profiles = []
        try:
            async with connect(
                session["websocketUrl"],
                subprotocols=[session["sessionToken"]],
                max_size=16 * 1024 * 1024,
                open_timeout=30,
                proxy=None,
            ) as ws:

                async def receive():
                    message = await asyncio.wait_for(ws.recv(), 120)
                    if isinstance(message, bytes):
                        return message
                    message = json.loads(message)
                    assert message["type"] not in (
                        "error",
                        "session_timeout",
                        "no_person",
                    ), message
                    return message

                async def wait_type(kind):
                    while True:
                        message = await receive()
                        if isinstance(message, dict) and message["type"] == kind:
                            return message

                await wait_type("session_granted")
                await ws.send(json.dumps({"type": "start"}))
                started = await wait_type("started")
                assert started["ref_image"] is True
                next_count = started["frames_per_next_chunk"]
                with Image.open(args.input) as source:
                    for chunk in range(3):
                        for _ in range(next_count):
                            source.seek(frames_in % getattr(source, "n_frames", 1))
                            frame = ImageOps.fit(
                                source.convert("RGB"),
                                (started["width"], started["height"]),
                            )
                            encoded = io.BytesIO()
                            frame.save(encoded, "JPEG", quality=90)
                            frames_in += 1
                            await ws.send(
                                json.dumps(
                                    {
                                        "type": "frame_meta",
                                        "seq": frames_in,
                                        "t_capture_ms": time.time() * 1000,
                                    }
                                )
                            )
                            await ws.send(encoded.getvalue())
                            if frames_in == 1:
                                frame.save(args.output / "source.jpg")
                        metadata = None
                        chunk_frames = 0
                        while True:
                            message = await receive()
                            if isinstance(message, bytes):
                                assert metadata is not None
                                with Image.open(io.BytesIO(message)) as output:
                                    assert output.format == "JPEG"
                                    assert output.size == (
                                        started["width"],
                                        started["height"],
                                    )
                                    assert max(ImageStat.Stat(output).stddev) > 1
                                    if chunk == 2 and chunk_frames == 0:
                                        output.save(args.output / "result.jpg")
                                frames_out += 1
                                chunk_frames += 1
                                profiles.append(metadata["profile"])
                                metadata = None
                            elif message["type"] == "output_frame":
                                metadata = message
                            elif message["type"] == "chunk_done":
                                assert chunk_frames == message["count"] == next_count
                                next_count = message["next_chunk_needs"]
                                await ws.send(
                                    json.dumps({"type": "ack", "recv": frames_out})
                                )
                                break
                assert profiles[-1]["dit_denoise_s"] > 0
                assert profiles[-1]["graph_path"] == 1
                await ws.send(json.dumps({"type": "stop"}))
        finally:
            ended = await client.delete(
                args.url + "/api/v1/live/sessions/" + session["sessionId"],
                headers=headers,
            )
            assert ended.status_code in (200, 404)
        health = (await client.get(args.url + "/health")).json()
        assert health["ok"] and health["runtime_loaded"]
        detection = await client.post(
            args.url + "/api/v1/live/sessions",
            headers=headers,
            json={"prompt": args.prompt, "detectPerson": True},
        )
        detection.raise_for_status()
        gated = detection.json()
        try:
            async with connect(
                gated["websocketUrl"], subprotocols=[gated["sessionToken"]], proxy=None
            ) as ws:
                while True:
                    grant = json.loads(await asyncio.wait_for(ws.recv(), 30))
                    if grant["type"] == "session_granted":
                        break
                    assert grant["type"] == "queue_position", grant
                await ws.send(json.dumps({"type": "start"}))
                started = json.loads(await asyncio.wait_for(ws.recv(), 30))
                assert started["type"] == "started"
                blank = io.BytesIO()
                Image.new("RGB", (started["width"], started["height"])).save(
                    blank, "JPEG"
                )
                await ws.send(blank.getvalue())
                waiting = json.loads(await asyncio.wait_for(ws.recv(), 30))
                assert (
                    waiting["type"] == "waiting_face" and waiting["reason"] == "no_face"
                )
                await ws.send(json.dumps({"type": "stop"}))
        finally:
            await client.delete(
                args.url + "/api/v1/live/sessions/" + gated["sessionId"],
                headers=headers,
            )
    report = {
        "ok": True,
        "authRejected": True,
        "reference": True,
        "detectPerson": False,
        "detectionEnabledCheck": True,
        "framesIn": frames_in,
        "framesOut": frames_out,
        "chunks": 3,
        "elapsedSeconds": round(time.monotonic() - started_at, 2),
        "lastChunkProfile": profiles[-1],
    }
    (args.output / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://joyai.nuvatech.cn")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "assets/cases/case01_source.gif",
    )
    parser.add_argument(
        "--reference",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "rv2v_reference/1c182f2f-32cf-4825-904e-64c69aed2e31.png",
    )
    parser.add_argument("--output", type=Path, default=Path("/tmp/joyai-api-smoke"))
    parser.add_argument(
        "--prompt",
        default="Put the pink T-shirt from the reference image on the people in the video. Preserve their faces and the background.",
    )
    arguments = parser.parse_args()
    if arguments.env_file:
        from dotenv import load_dotenv

        load_dotenv(arguments.env_file)
    asyncio.run(asyncio.wait_for(smoke(arguments), timeout=300))
