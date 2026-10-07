#!/usr/bin/env bash
set -euo pipefail
ROOT=${1:-/root/autodl-tmp/JoyAI-Video-Edit}
COMMIT=ca17e1d1030f454cb98b0ed549b4d31a60139ceb
if [[ -d $ROOT/.git ]]; then
  [[ $(git -C "$ROOT" rev-parse HEAD) == "$COMMIT" ]] || { echo 'Existing source revision differs' >&2; exit 1; }
elif [[ -f $ROOT/.joyai-upstream-commit ]]; then
  [[ $(< "$ROOT/.joyai-upstream-commit") == "$COMMIT" ]] || exit 1
elif [[ -e $ROOT || -L $ROOT ]]; then
  echo "Refusing to overwrite unrecognized directory: $ROOT" >&2
  exit 1
else
  STAGE=$(mktemp -d "${ROOT}.download.XXXXXX")
  trap 'find "$STAGE" -depth -delete' EXIT
  git -C "$STAGE" init -q
  (
    if [[ -f /etc/network_turbo ]]; then
      set +u
      source /etc/network_turbo
      set -u
    fi
    git -C "$STAGE" fetch --depth 1 https://github.com/jd-opensource/JoyAI-Video-Edit.git "$COMMIT"
  )
  git -C "$STAGE" checkout -q --detach "$COMMIT"
  [[ ! -e $ROOT && ! -L $ROOT ]] || { echo 'Project path appeared during download' >&2; exit 1; }
  mv -nT "$STAGE" "$ROOT"
  [[ ! -d $STAGE ]] || { echo 'Project path appeared during download' >&2; exit 1; }
  trap - EXIT
fi
for path in deploy/run_server.sh deploy/requirements.txt deploy/static/index.html \
            deploy/joyomni_ops/setup.py assets/cases/case01_source.gif; do
  [[ -f $ROOT/$path ]] || { echo "Missing upstream file: $path" >&2; exit 1; }
done
