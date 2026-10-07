#!/usr/bin/env python3
"""Verify a JoyAI demo produces a nonblank, complete edited JPEG chunk."""

import argparse
import asyncio
import base64
import io
import json
from pathlib import Path
import sys
import time
import uuid

from PIL import Image, ImageOps, ImageStat
import websockets


async def smoke(args):
    session_id = uuid.uuid4().hex
    args.output.mkdir(parents=True, exist_ok=True)
    started_at = time.monotonic()
    async with websockets.connect(
        args.url, open_timeout=30, close_timeout=10, max_size=16 * 1024 * 1024,
    ) as ws:
        async def send(payload):
            await asyncio.wait_for(
                ws.send(json.dumps({**payload, "session_id": session_id})), 30,
            )

        async def receive():
            data = await asyncio.wait_for(ws.recv(), args.timeout)
            if isinstance(data, bytes):
                return data
            message = json.loads(data)
            if message.get("type") in ("error", "session_timeout"):
                raise RuntimeError(message.get("message", message["type"]))
            return message

        async def wait_type(kind):
            while True:
                message = await receive()
                if isinstance(message, dict) and message.get("type") == kind:
                    return message

        await wait_type("session_granted")
        await send({
            "type": "start", "prompt": args.prompt,
            "width": args.width, "height": args.height,
            "num_inference_steps": args.steps, "seed": 42, "use_pe": False,
            "gate_enabled": False, "source": "camera", "kv_reset_frames": 0,
            "max_inflight_chunks": 0,
            "freeze_kv_on_static": False, "profile_timings": True,
            "input_codec": "mjpeg", "output_codec": "mjpeg", "output_quality": 90,
            "ref_image": base64.b64encode(args.reference.read_bytes()).decode("ascii")
            if args.reference else None,
        })
        started = await wait_type("started")
        # CUDA graph replay starts after the anchor and first eight-frame chunk.
        frame_count = int(started["frames_per_next_chunk"]) + 16
        with Image.open(args.input) as source:
            for index in range(frame_count):
                source.seek(index % getattr(source, "n_frames", 1))
                frame = ImageOps.fit(
                    source.convert("RGB"), (started["width"], started["height"]),
                )
                if index == 0:
                    frame.save(args.output / "source.jpg")
                encoded = io.BytesIO()
                frame.save(encoded, "JPEG", quality=90)
                await send({"type": "frame_meta", "seq": index + 1,
                            "t_capture_ms": time.time() * 1000})
                await asyncio.wait_for(ws.send(encoded.getvalue()), 30)

        metadata = None
        chunk_profile = None
        output_frames = chunk_frames = chunks = 0
        largest_stddev = 0.0
        saved = False
        while True:
            message = await receive()
            if isinstance(message, bytes):
                if metadata is None:
                    raise RuntimeError("output JPEG has no output_frame metadata")
                with Image.open(io.BytesIO(message)) as image:
                    if image.format != "JPEG":
                        raise RuntimeError("output is not JPEG")
                    if image.size != (started["width"], started["height"]):
                        raise RuntimeError("output dimensions differ from started session")
                    rgb = image.convert("RGB")
                    stddev = max(ImageStat.Stat(rgb).stddev)
                    if stddev <= 1:
                        raise RuntimeError("blank or nearly uniform output frame")
                    largest_stddev = max(largest_stddev, stddev)
                    if metadata["count"] >= 8 and not saved:
                        rgb.save(args.output / "result.jpg")
                        saved = True
                output_frames += 1
                chunk_frames += 1
                metadata = None
            elif message.get("type") == "output_frame":
                if metadata is not None:
                    raise RuntimeError("output_frame metadata missing its JPEG")
                metadata = message
                chunk_profile = message.get("profile")
            elif message.get("type") == "chunk_done":
                chunks += 1
                if chunk_frames != int(message["count"]):
                    raise RuntimeError("chunk_done frame count differs from decoded JPEGs")
                await send({"type": "ack", "recv": output_frames})
                if chunk_frames >= 8 and chunks >= 3:
                    if not chunk_profile or float(chunk_profile.get("dit_denoise_s", 0)) <= 0:
                        raise RuntimeError("edited chunk has no positive DiT inference timing")
                    if chunk_profile.get("graph_path") != 1:
                        raise RuntimeError("edited chunk did not execute CUDA graph replay")
                    await send({"type": "stop"})
                    result = {
                        "ok": True, "input_frames": frame_count,
                        "output_frames": output_frames, "chunks": chunks,
                        "edited_chunk_frames": chunk_frames,
                        "width": started["width"], "height": started["height"],
                        "elapsed_s": round(time.monotonic() - started_at, 2),
                        "chunk_profile": chunk_profile,
                        "reference": str(args.reference) if args.reference else None,
                        "pixel_stddev": round(largest_stddev, 2),
                        "images": str(args.output.resolve()),
                    }
                    break
                chunk_frames = 0

    # Reacquire the server gate after cleanup, then exercise its runtime guard.
    async with websockets.connect(args.url, open_timeout=30, close_timeout=10) as reuse:
        while True:
            message = json.loads(await asyncio.wait_for(reuse.recv(), 30))
            if message.get("type") in ("error", "session_timeout"):
                raise RuntimeError(message.get("message", message["type"]))
            if message.get("type") == "session_granted":
                break
        await reuse.send(json.dumps({"type": "ping", "t": time.time() * 1000}))
        message = json.loads(await asyncio.wait_for(reuse.recv(), 30))
        if message.get("type") != "pong":
            raise RuntimeError(f"runtime not reusable after cleanup: {message}")
    result["session_reuse"] = True
    print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="WebSocket endpoint, e.g. ws://127.0.0.1:8080/ws")
    parser.add_argument("--width", type=int, default=1248)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--input", type=Path, default=Path(__file__).resolve().parents[2]
                        / "assets/cases/case01_source.gif")
    parser.add_argument("--output", type=Path, default=Path("/tmp/joyai-smoke"))
    parser.add_argument("--prompt", default="Transform the video into a colorful watercolor painting.")
    parser.add_argument("--steps", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=900,
                        help="Maximum total test duration in seconds")
    args = parser.parse_args()
    if min(args.steps, args.width, args.height, args.timeout) <= 0:
        parser.error("steps, dimensions and timeout must be positive")
    try:
        asyncio.run(asyncio.wait_for(smoke(args), args.timeout))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__,
                          "message": str(exc)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
