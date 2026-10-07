#!/usr/bin/env bash
set -euo pipefail
KIT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
ROOT=/root/autodl-tmp/JoyAI-Video-Edit
PREFIX=/root/autodl-tmp/joyai-env
[[ $EUID == 0 && $(uname -m) == x86_64 ]] || { echo 'Requires root on Linux x86_64' >&2; exit 1; }
nvidia-smi
[[ $(nvidia-smi --query-gpu=name --format=csv,noheader | head -n 1) == *"RTX PRO 6000"* ]] || { echo 'This kit targets RTX PRO 6000 Blackwell' >&2; exit 1; }
# Reinstallation must not change packages underneath a running GPU process.
if curl -sf --max-time 3 http://127.0.0.1:8080/health >/dev/null || pgrep -f '^python .*serve_joyomni_streaming.py' >/dev/null; then
  echo 'An existing demo is running. Use deploy.sh status/start; installation was not changed.' >&2
  exit 1
fi
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y curl git aria2 build-essential ca-certificates libgl1 libglib2.0-0
cd "$KIT"
sha256sum -c SHA256SUMS
mkdir -p /root/autodl-tmp/joyai-logs /root/autodl-tmp/joyai-wheels /root/autodl-tmp/joyai-tmp
bash "$KIT/ops/source.sh" "$ROOT"
mkdir -p "$ROOT/deploy/ops"
cp -a "$KIT/ops/." "$ROOT/deploy/ops/"
CONDA=/root/miniconda3/bin/conda
if [[ ! -x $CONDA ]]; then
  [[ ! -e /root/miniconda3 ]] || { echo 'Existing /root/miniconda3 is incomplete' >&2; exit 1; }
  INSTALLER=/root/autodl-tmp/joyai-tmp/miniconda.sh
  curl -fL --retry 3 -o "$INSTALLER" https://repo.anaconda.com/miniconda/Miniconda3-py310_24.5.0-0-Linux-x86_64.sh
  echo "b3d73db6a05069fbdf20dc33fc9b6a29fa7198578f0d090c639f5ca0e84102bd  $INSTALLER" | sha256sum -c -
  bash "$INSTALLER" -b -p /root/miniconda3
fi
if [[ ! -x $PREFIX/bin/python ]]; then
  "$CONDA" create -p "$PREFIX" --override-channels \
    -c https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main python=3.10.22 -y
fi
"$PREFIX/bin/python" -c 'import sys; assert sys.version_info[:2] == (3, 10)'
for manifest in torch-downloads cuda-downloads runtime-downloads; do
  aria2c -x 8 -s 8 -j 3 --continue=true --file-allocation=none \
    --auto-file-renaming=false --check-integrity=true --user-agent=pip/26.2.1 \
    --dir=/root/autodl-tmp/joyai-wheels --input-file="$KIT/ops/$manifest.txt"
done
bash "$ROOT/deploy/ops/install.sh"
bash "$ROOT/deploy/ops/download.sh"
bash "$ROOT/deploy/ops/export-detector.sh"
"$PREFIX/bin/python" - <<'PY'
import torch
assert torch.cuda.get_device_capability(0) == (12, 0), 'This kit targets sm_120a PRO 6000'
assert 'RTX PRO 6000' in torch.cuda.get_device_name(0), 'Use the upstream guide for other GPUs'
PY
bash "$ROOT/deploy/ops/manage.sh" start
bash "$ROOT/deploy/ops/manage.sh" wait
"$PREFIX/bin/python" "$ROOT/deploy/ops/smoke.py" ws://127.0.0.1:8080/ws \
  --output /root/autodl-tmp/joyai-smoke
bash "$ROOT/deploy/ops/manage.sh" check
echo 'GPU deployment and real inference passed. Use deploy.sh tunnel or public.'
