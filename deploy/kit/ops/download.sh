#!/usr/bin/env bash
set -euo pipefail
ROOT=/root/autodl-tmp/JoyAI-Video-Edit
export PATH="/root/autodl-tmp/joyai-env/bin:$PATH"
export HF_HOME=/root/autodl-tmp/joyai-hf-cache
export HF_ENDPOINT=https://huggingface.co
CKPT="$ROOT/deploy/deps/checkpoints"
mkdir -p "$CKPT/JoyAI-Video-Edit/dit" "$CKPT/JoyAI-Video-Edit/vae"

# Only small configuration/tokenizer files use Hugging Face metadata.
(
  if [[ -f /etc/network_turbo ]]; then
    set +u
    source /etc/network_turbo
    set -u
  fi
  hf download jdopensource/JoyAI-Video-Edit \
    --local-dir "$CKPT/JoyAI-Video-Edit" --include 'vae/config.json'
  hf download XiaomiMiMo/MiMo-VL-7B-RL-2508 \
    --local-dir "$CKPT/MiMo-VL-7B-RL-2508" --include '*.json' '*.txt'
  curl -fL --retry 3 -o "$CKPT/face_detection_yunet_2023mar.onnx" \
    https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
)

ARIA=(aria2c -x 16 -s 16 --continue=true --check-integrity=true --file-allocation=none --auto-file-renaming=false)
"${ARIA[@]}" --dir="$CKPT/JoyAI-Video-Edit/dit" \
  --out=joyai_video_edit_dit_0811.pth \
  --checksum=sha-256=b3904b6fda53d13b230918bb616f322d12cfb2337b0e8d9dc203cdabc36605ba \
  'https://modelscope.cn/api/v1/models/jd-opensource/JoyAI-Video-Edit/repo?Revision=master&FilePath=dit%2Fjoyai_video_edit_dit_0811.pth'
"${ARIA[@]}" --dir="$CKPT/JoyAI-Video-Edit/vae" \
  --out=diffusion_pytorch_model.safetensors \
  --checksum=sha-256=150315748d7c3307cdae2819ee651b32d58385668ca0c4db3d3dcd6e63b77e86 \
  'https://modelscope.cn/models/jd-opensource/JoyAI-Video-Edit/resolve/master/vae/diffusion_pytorch_model.safetensors'
"${ARIA[@]}" -j 4 --dir="$CKPT/MiMo-VL-7B-RL-2508" \
  --input-file="$ROOT/deploy/ops/mimo-downloads.txt"
