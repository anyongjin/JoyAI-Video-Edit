#!/usr/bin/env bash
set -euo pipefail
ROOT=/root/autodl-tmp/JoyAI-Video-Edit
PREFIX=/root/autodl-tmp/joyai-env
export PATH="$PREFIX/bin:/root/miniconda3/bin:$PATH"
export PIP_CACHE_DIR=/root/autodl-tmp/joyai-pip-cache
export TMPDIR=/root/autodl-tmp/joyai-tmp
export CUDA_HOME="$PREFIX"
mkdir -p "$PIP_CACHE_DIR" "$TMPDIR"
cd "$ROOT"

conda install -p "$PREFIX" --override-channels \
  -c https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge \
  cuda-nvcc=12.8.93 cuda-cudart-dev=12.8.90 \
  libcublas-dev=12.8.5.5 libcusparse-dev=12.5.8.93 libcusolver-dev=11.7.3.90 -y
nvcc --version
python -m pip install --upgrade pip setuptools wheel
python -m pip install --no-deps /root/autodl-tmp/joyai-wheels/*.whl
# Torch CUDA wheels are staged separately from the official PyTorch index.
sed '/^--extra-index-url /d' deploy/requirements.txt > "$TMPDIR/requirements.txt"
python -m pip install -i https://mirrors.aliyun.com/pypi/simple \
  --find-links /root/autodl-tmp/joyai-wheels -r "$TMPDIR/requirements.txt" \
  -c deploy/ops/requirements-lock.txt supervisor==4.3.0 huggingface-hub==0.36.0

mkdir -p deploy/tmp
if [[ ! -d deploy/tmp/cutlass/.git ]]; then
  git init deploy/tmp/cutlass
  git -C deploy/tmp/cutlass remote add origin https://github.com/NVIDIA/cutlass.git
fi
git -C deploy/tmp/cutlass fetch --depth 1 origin dcf215af68a2d08d305076c152a06f201728cd53
git -C deploy/tmp/cutlass checkout dcf215af68a2d08d305076c152a06f201728cd53
export JOYOMNI_OPS_CUDA_ARCHS=120a
export JOYOMNI_OPS_CUTLASS_DIR="$ROOT/deploy/tmp/cutlass"
export MAX_JOBS=12
python -m pip install --no-build-isolation ./deploy/joyomni_ops
python -c 'import torch, joyomni_ops; print(torch.__version__, torch.version.cuda, torch.cuda.get_device_name(0)); assert torch.cuda.is_available(); assert joyomni_ops.has_fp8()'
python -m pip check
