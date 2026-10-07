#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if command -v sha256sum >/dev/null; then
  sha256sum -c SHA256SUMS
else
  shasum -a 256 -c SHA256SUMS
fi
for script in deploy.sh verify.sh ops/*.sh; do bash -n "$script"; done
PYTHONDONTWRITEBYTECODE=1 python3 selftest.py
echo 'Local kit checks passed. No SSH, DNS or deployment action was performed.'
