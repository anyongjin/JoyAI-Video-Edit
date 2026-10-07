# JoyAI Live Editing API

Base URL: `https://joyai.nuvatech.cn`

## Create a Session

`POST /api/v1/live/sessions`, with `Authorization: Bearer <API_KEY>`.

```json
{
  "prompt": "Put the outfit from the reference image on the person in the video.",
  "refImage": "data:image/jpeg;base64,...",
  "detectPerson": false,
  "outputCodec": "mjpeg",
  "width": 1248,
  "height": 720
}
```

`refImage` is optional base64 image data, up to 8 MiB encoded / 16 megapixels.
`detectPerson` defaults to `false`; `true` enables the existing face/body gates.
The GPU uses its warmed-up landscape or portrait resolution. The requested
width/height selects orientation; use dimensions returned by `started`.
`numInferenceSteps` accepts 1-4 (default 2), and `profileTimings` defaults to false.

Response (HTTP 201):

```json
{
  "sessionId": "...",
  "sessionToken": "...",
  "websocketUrl": "wss://joyai.nuvatech.cn/api/v1/live/sessions/.../ws",
  "expiresAt": 1799999999,
  "detectPerson": false,
  "transport": "websocket",
  "inputCodec": "mjpeg",
  "outputCodec": "mjpeg"
}
```

Sessions last at most five minutes, including queue time. A token opens one
connection; disconnecting consumes the session. A GPU handles one editing
session at a time, and other connections receive `queue_position` events.

## Stream Frames

Browsers connect with `new WebSocket(websocketUrl, sessionToken)`. Server
clients can instead send `Authorization: Bearer <sessionToken>` or their API
Key as a handshake header. Keep the API Key on your backend.

1. Wait for `{"type":"session_granted"}`.
2. Send `{"type":"start"}`. Creation parameters are fixed for this session.
3. Read `started`, especially `width`, `height`, `frames_per_next_chunk` and
   `session_id`. This route reuses the demo's existing frame protocol;
   its stream event fields remain snake_case.
4. Send a `frame_meta` JSON message, then a binary JPEG for each camera frame:

   ```json
   {"type":"frame_meta","seq":1,"t_capture_ms":1799999999000}
   ```

5. Each `output_frame` JSON message is followed by a binary JPEG (or an H.264
   packet when `outputCodec` is `h264`). `chunk_done` reports output counts.
6. Send `{"type":"ack","recv":17}` with the total frames received to apply
   backpressure, and `{"type":"stop"}` when finished. Excess live camera
   input may be dropped by the existing realtime flow control.

Send frames or `ping` at least every ten seconds to retain an idle GPU slot.
With detection enabled, `waiting_face` / `no_person` events mean the gate is
holding inference until the subject is visible.

## End a Session

`DELETE /api/v1/live/sessions/{sessionId}` with the API Key header ends both
queued and active connections. HTTP 404 means it has already been removed.

HTTP errors: 401 invalid Key; 422 invalid parameters/reference; 429 session
capacity; 503 Key not configured. Unauthorized WebSocket handshakes are denied
before entering the GPU queue. Session state is in memory in one process;
restarts invalidate tokens.

## Deploy and Verify

```bash
python3 deploy/enable_api.py --backend-env /path/to/outfit_agent/.env
```

This installs the API into the existing GPU deployment and restarts its
supervised process. Existing dependencies, checkpoints and compile caches
are reused. The Key lives in `/root/autodl-tmp/joyai-api.key` (mode 0600), and
the optional backend env gets `JOYAI_API_URL` / `JOYAI_API_KEY`. No Key is
printed or committed. Wait for `/health` to report `runtime_loaded=true`.

The existing public demo at `/` and `/ws` remains available without a Key.
Key authorization applies to `/api/v1/live/*`. Restrict the demo routes at
the gateway when deploying an exclusively private model service.

Using a Python environment with the existing FastAPI/Pillow/httpx/websockets
dependencies:

```bash
python -m pytest deploy/tests/test_live_api.py -q
python deploy/tests/smoke_live_api.py --env-file /path/to/outfit_agent/.env
```

The live check verifies wrong/missing Keys, real reference-guided GPU editing,
complete/nonblank output chunks, runtime health and enabled detection on a
frame without a person. Images and the report go to `/tmp/joyai-api-smoke`.

Verified on 2026-10-07: 17 JPEG input frames / 17 output frames, three complete
chunks, reference-guided pink T-shirt editing, CUDA graph replay and a final
DiT chunk time of 0.42 seconds. Both detection settings and runtime health
passed. The direct API smoke completed in 6.04 seconds.
