#!/usr/bin/env bash
set -euo pipefail
ROOT=${1:-/root/autodl-tmp/JoyAI-Video-Edit}
PYTHON=${2:-/root/autodl-tmp/joyai-env/bin/python}
EXPORT_ENV="$(dirname -- "$ROOT")/joyai-detector-export"
CKPT="$ROOT/deploy/deps/checkpoints"
mkdir -p "$CKPT" "$EXPORT_ENV"
check() {
  "$PYTHON" - "$CKPT/yolov8n.onnx" <<'PY'
import sys
import cv2
import numpy as np
net = cv2.dnn.readNetFromONNX(sys.argv[1])
net.setInput(np.zeros((1, 3, 320, 320), dtype=np.float32))
output = net.forward()
assert output.shape == (1, 84, 2100), output.shape
assert np.isfinite(output).all()
print('YOLO ONNX passed main-environment OpenCV inference.')
PY
}
if [[ -f $CKPT/yolov8n.onnx ]] && check; then
  exit 0
fi
PT="$EXPORT_ENV/yolov8n.pt"
PT_SHA=f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36
if [[ ! -f $PT ]] || ! printf '%s  %s\n' "$PT_SHA" "$PT" | sha256sum -c -; then
  (
    if [[ -f /etc/network_turbo ]]; then
      set +u
      source /etc/network_turbo
      set -u
    fi
    curl -fL --connect-timeout 20 --max-time 300 --retry 3 -o "$PT.part" \
      https://github.com/ultralytics/assets/releases/download/v8.4.0/yolov8n.pt
  )
  printf '%s  %s\n' "$PT_SHA" "$PT.part" | sha256sum -c -
  mv "$PT.part" "$PT"
fi
if [[ ! -x $EXPORT_ENV/bin/python ]]; then
  "$PYTHON" -m venv --system-site-packages "$EXPORT_ENV"
fi
"$EXPORT_ENV/bin/python" -m pip install -i https://mirrors.aliyun.com/pypi/simple \
  -c "$ROOT/deploy/ops/requirements-lock.txt" \
  ultralytics==8.4.39 onnx==1.19.1 onnxslim==0.1.71 onnxruntime==1.23.2 \
  opencv-python==4.13.0.92
CUDA_VISIBLE_DEVICES='' YOLO_AUTOINSTALL=false YOLO_CONFIG_DIR="$EXPORT_ENV/config" \
  OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 "$EXPORT_ENV/bin/python" - "$PT" <<'PY'
import sys
import onnx
from ultralytics import YOLO
path = YOLO(sys.argv[1]).export(format='onnx', imgsz=320, opset=12,
                              dynamic=False, simplify=True, device='cpu')
model = onnx.load(path)
onnx.checker.check_model(model)
shape = [dim.dim_value for dim in model.graph.input[0].type.tensor_type.shape.dim]
assert shape == [1, 3, 320, 320], shape
PY
install -m 644 "$EXPORT_ENV/yolov8n.onnx" "$CKPT/yolov8n.onnx"
check
