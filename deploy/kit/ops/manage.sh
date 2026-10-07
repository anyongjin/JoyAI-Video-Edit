#!/usr/bin/env bash
set -euo pipefail
ROOT=/root/autodl-tmp/JoyAI-Video-Edit
PREFIX=/root/autodl-tmp/joyai-env
CONFIG="$ROOT/deploy/ops/supervisord.conf"
if [[ -x /usr/bin/supervisord ]] && /usr/bin/supervisord --help 2>&1 | grep -q 'Control a running daemon'; then
  START=(/usr/bin/supervisord -c "$CONFIG" -d)
  CTL=(/usr/bin/supervisord ctl -s http://127.0.0.1:19090)
else
  START=("$PREFIX/bin/supervisord" -c "$CONFIG")
  CTL=("$PREFIX/bin/supervisorctl" -c "$CONFIG")
fi
check() {
  "$PREFIX/bin/python" - <<'PY'
import json
import urllib.request
with urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=5) as response:
    health = json.load(response)
assert health.get('ok') is True and health.get('runtime_loaded') is True, health
print(json.dumps(health))
PY
}
case "${1:-status}" in
  start)
    mkdir -p /root/autodl-tmp/joyai-logs
    if curl -sf --max-time 2 http://127.0.0.1:19090 >/dev/null; then
      STATUS=$("${CTL[@]}" status joyai-demo)
      if [[ ! $STATUS =~ [Rr][Uu][Nn][Nn][Ii][Nn][Gg] && ! $STATUS =~ [Ss][Tt][Aa][Rr][Tt][Ii][Nn][Gg] ]]; then
        "${CTL[@]}" start joyai-demo
      fi
    else
      "${START[@]}"
    fi
    ;;
  restart|stop) "${CTL[@]}" "$1" joyai-demo ;;
  status) "${CTL[@]}" status; check ;;
  check) check ;;
  wait)
    DEADLINE=$((SECONDS + ${JOYAI_START_TIMEOUT:-3600}))
    until check 2>/dev/null; do
      ((SECONDS < DEADLINE)) || { tail -n 60 /root/autodl-tmp/joyai-logs/demo.log; exit 1; }
      echo 'Waiting for model load/compile/warmup (see joyai-logs/demo.log)...'
      sleep 15
    done
    ;;
  *) echo 'Usage: manage.sh {start|restart|stop|status|check|wait}' >&2; exit 2 ;;
esac
