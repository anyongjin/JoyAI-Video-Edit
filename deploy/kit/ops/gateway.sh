#!/usr/bin/env bash
set -euo pipefail
ACTION=${1:?prepare or apply}
GPU_HOST=${2:?GPU hostname}
GPU_PORT=${3:?GPU SSH port}
DOMAIN=${4:?domain}
TLS_CERT=${5:?TLS certificate path}
TLS_KEY=${6:?TLS key path}
[[ $EUID == 0 ]] || exit 1
[[ $GPU_HOST =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ && $DOMAIN =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]] || exit 2
[[ $GPU_PORT =~ ^[0-9]{1,5}$ ]] && ((10#$GPU_PORT > 0 && 10#$GPU_PORT < 65536)) || exit 2
for value in "$TLS_CERT" "$TLS_KEY"; do
  [[ $value =~ ^/[a-zA-Z0-9_./-]+$ ]] || exit 2
done
if [[ $ACTION == prepare ]]; then
  command -v nginx
  command -v systemctl
  command -v python3
  [[ -f $TLS_CERT && -f $TLS_KEY ]] || { echo 'TLS certificate/key must already exist' >&2; exit 1; }
  install -d -m 700 /etc/joyai
  if [[ ! -f /etc/joyai/id_ed25519 ]]; then
    ssh-keygen -t ed25519 -N '' -C joyai-tunnel -f /etc/joyai/id_ed25519
  fi
  chmod 600 /etc/joyai/id_ed25519
  install -m 600 "${7:?verified GPU known_hosts file}" /etc/joyai/known_hosts
  exit 0
fi
[[ $ACTION == apply ]] || exit 2
ssh -p "$GPU_PORT" -i /etc/joyai/id_ed25519 -o BatchMode=yes \
  -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/etc/joyai/known_hosts \
  -o ExitOnForwardFailure=yes -NT -L 127.0.0.1:18181:127.0.0.1:8080 "root@$GPU_HOST" &
PROBE=$!
trap 'kill "$PROBE" 2>/dev/null || true' EXIT
sleep 2
kill -0 "$PROBE"
curl --noproxy '*' -sf --retry 5 --retry-delay 2 --retry-connrefused \
  --retry-max-time 30 --max-time 5 http://127.0.0.1:18181/health |
  python3 -c 'import json,sys; h=json.load(sys.stdin); assert h.get("ok") is True and h.get("runtime_loaded") is True,h'
cat > /etc/systemd/system/joyai-tunnel.service <<EOF
[Unit]
Description=JoyAI GPU demo SSH tunnel
Wants=network-online.target
After=network-online.target
[Service]
Type=simple
ExecStart=/usr/bin/ssh -NT -p $GPU_PORT -i /etc/joyai/id_ed25519 -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/etc/joyai/known_hosts -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 127.0.0.1:18180:127.0.0.1:8080 root@$GPU_HOST
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF
SITE=/etc/nginx/sites-available/$DOMAIN
BACKUP=$(mktemp)
HAD_SITE=0
if [[ -f $SITE ]]; then cp -a "$SITE" "$BACKUP"; HAD_SITE=1; fi
cat > "$SITE" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    location / { return 301 https://\$host\$request_uri; }
}
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name $DOMAIN;
    ssl_certificate $TLS_CERT;
    ssl_certificate_key $TLS_KEY;
    client_max_body_size 100m;
    location / {
        proxy_pass http://127.0.0.1:18180;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 3600;
        proxy_send_timeout 3600;
        proxy_buffering off;
    }
}
EOF
LINK=/etc/nginx/sites-enabled/$DOMAIN
HAD_LINK=0
[[ ! -e $LINK && ! -L $LINK ]] || HAD_LINK=1
if ((HAD_LINK)); then
  [[ $(readlink -f "$LINK") == "$SITE" ]] || { cp -a "$BACKUP" "$SITE"; echo 'Existing enabled site points elsewhere' >&2; exit 1; }
else
  ln -s "$SITE" "$LINK"
fi
if ! nginx -t; then
  if ((HAD_SITE)); then cp -a "$BACKUP" "$SITE"; else rm -- "$SITE"; fi
  if ((!HAD_LINK)); then rm -- "$LINK"; fi
  rm -- "$BACKUP"
  exit 1
fi
rm -- "$BACKUP"
systemctl daemon-reload
systemctl enable joyai-tunnel.service
systemctl restart joyai-tunnel.service
systemctl reload nginx
systemctl is-active joyai-tunnel.service
curl --noproxy '*' --fail --retry 10 --retry-delay 2 --retry-all-errors \
  --retry-max-time 45 --max-time 5 --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/health" |
  python3 -c 'import json,sys; h=json.load(sys.stdin); assert h.get("ok") is True and h.get("runtime_loaded") is True,h; print(json.dumps(h))'
