#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$HERE/config.env"
usage() {
  echo 'Usage: bash deploy.sh {gpu|start|restart|status|smoke|tunnel|public|dns|help}'
}
case "${1:-help}" in
  help|-h|--help) usage; exit 0 ;;
  gpu|start|restart|status|smoke|tunnel|public|dns) ACTION=$1 ;;
  *) usage >&2; exit 2 ;;
esac
for value in "$GPU_HOST" "$GATEWAY_HOST" "$DOMAIN"; do
  [[ $value =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]] || { echo 'Invalid hostname' >&2; exit 2; }
done
for value in "$GPU_PORT" "$GATEWAY_PORT" "$LOCAL_PORT"; do
  [[ $value =~ ^[0-9]{1,5}$ ]] && ((10#$value > 0 && 10#$value < 65536)) || exit 2
done
GPU_SSH=(ssh -o StrictHostKeyChecking=yes -p "$GPU_PORT" "root@$GPU_HOST")
GW_SSH=(ssh -o StrictHostKeyChecking=yes -p "$GATEWAY_PORT" "root@$GATEWAY_HOST")
ROOT=/root/autodl-tmp/JoyAI-Video-Edit
case "$ACTION" in
  gpu)
    # Upload only the deployment kit, never the operator's credential file.
    "${GPU_SSH[@]}" 'mkdir -p /root/autodl-tmp/joyai-deploy-kit'
    scp -r -o StrictHostKeyChecking=yes -P "$GPU_PORT" "$HERE/SHA256SUMS" "$HERE/ops" "root@$GPU_HOST:/root/autodl-tmp/joyai-deploy-kit/"
    "${GPU_SSH[@]}" 'cd /root/autodl-tmp/joyai-deploy-kit && bash ops/bootstrap.sh'
    ;;
  start|restart|status)
    "${GPU_SSH[@]}" "bash $ROOT/deploy/ops/manage.sh $ACTION"
    ;;
  smoke)
    "${GPU_SSH[@]}" "bash $ROOT/deploy/ops/manage.sh check && /root/autodl-tmp/joyai-env/bin/python $ROOT/deploy/ops/smoke.py ws://127.0.0.1:8080/ws --output /root/autodl-tmp/joyai-smoke && bash $ROOT/deploy/ops/manage.sh check"
    ;;
  tunnel)
    echo "Open http://localhost:$LOCAL_PORT/ ; keep this terminal running."
    "${GPU_SSH[@]}" -NT -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
      -L "127.0.0.1:$LOCAL_PORT:127.0.0.1:8080"
    ;;
  public)
    "${GPU_SSH[@]}" true
    KNOWN=$(mktemp)
    trap 'rm -- "$KNOWN"' EXIT
    ssh-keygen -F "[$GPU_HOST]:$GPU_PORT" > "$KNOWN"
    scp -o StrictHostKeyChecking=yes -P "$GATEWAY_PORT" "$HERE/ops/gateway.sh" "$KNOWN" "root@$GATEWAY_HOST:/tmp/"
    printf -v PREPARE '%q ' bash /tmp/gateway.sh prepare "$GPU_HOST" "$GPU_PORT" "$DOMAIN" "$TLS_CERT" "$TLS_KEY" "/tmp/$(basename "$KNOWN")"
    "${GW_SSH[@]}" "$PREPARE"
    PUBLIC_KEY=$("${GW_SSH[@]}" 'cat /etc/joyai/id_ed25519.pub')
    [[ $PUBLIC_KEY =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+(\ .*)?$ ]] || exit 2
    printf '%s\n' "command=\"/bin/false\",restrict,port-forwarding,permitopen=\"127.0.0.1:8080\" $PUBLIC_KEY" |
      "${GPU_SSH[@]}" 'umask 077; mkdir -p /root/.ssh; touch /root/.ssh/authorized_keys; IFS= read -r line; grep -Fqx -- "$line" /root/.ssh/authorized_keys || printf "%s\n" "$line" >> /root/.ssh/authorized_keys'
    printf -v APPLY '%q ' bash /tmp/gateway.sh apply "$GPU_HOST" "$GPU_PORT" "$DOMAIN" "$TLS_CERT" "$TLS_KEY"
    "${GW_SSH[@]}" "$APPLY"
    echo "Gateway installed. DNS must point $DOMAIN to $GATEWAY_IP."
    curl --noproxy '*' --fail --max-time 30 "https://$DOMAIN/health" |
      python3 -c 'import json,sys; h=json.load(sys.stdin); assert h.get("ok") is True and h.get("runtime_loaded") is True,h; print(json.dumps(h))'
    ;;
  dns)
    if [[ ! -x $HERE/.dns-env/bin/python ]]; then
      python3 -m venv "$HERE/.dns-env"
      "$HERE/.dns-env/bin/pip" install python-dotenv==1.1.1
    fi
    "$HERE/.dns-env/bin/python" "$HERE/ops/alidns.py" --env-file "$DNS_ENV_FILE" \
      --domain "$DOMAIN" --set-ip "$GATEWAY_IP"
    ;;
esac
