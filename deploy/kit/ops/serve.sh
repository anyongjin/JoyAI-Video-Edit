#!/usr/bin/env bash
set -euo pipefail
ROOT=/root/autodl-tmp/JoyAI-Video-Edit
export PATH="/root/autodl-tmp/joyai-env/bin:$PATH"
export CUDA_HOME=/root/autodl-tmp/joyai-env
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export JOYOMNI_CACHE_ROOT="$ROOT/deploy/deps/cache_pro6000"
export JOYOMNI_HOST=127.0.0.1
export JOYOMNI_PORT=8080
export JOYOMNI_WIDTH=768
export JOYOMNI_HEIGHT=1024
export JOYOMNI_FPS=25
export JOYOMNI_RECORD_DIR=/root/autodl-tmp/joyai-recordings
export JOYAI_API_KEY_FILE=/root/autodl-tmp/joyai-api.key
cd "$ROOT"
exec bash deploy/run_server.sh
