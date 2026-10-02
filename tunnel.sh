#!/usr/bin/env bash
# Puts Darwish Smart Power on the internet at https://<hostname> through a Cloudflare Tunnel:
#   sudo bash tunnel.sh power.darwish-tech.com
# No port forwarding or public IP needed. The only manual step is opening one Cloudflare
# login link and choosing the domain. Safe to run again.
#
# It uses its own tunnel ("darwish-smart-power") and its own service
# ("darwish-tunnel"), so any tunnel already on this machine (n8n, ...) is left alone.
set -euo pipefail

HOST="${1:-power.darwish-tech.com}"
PORT="${SP_WEB_PORT:-8090}"
NAME="darwish-smart-power"
CONF_DIR=/etc/cloudflared
CONF="$CONF_DIR/$NAME.yml"
UNIT=/etc/systemd/system/darwish-tunnel.service
export HOME=/root                         # cloudflared keeps its login in ~/.cloudflared

if [ "$(id -u)" -ne 0 ]; then
    echo "Run it with sudo:  sudo bash $0 $HOST"
    exit 1
fi
if ! python3 -c "import socket; socket.create_connection(('127.0.0.1', $PORT), 1).close()" 2>/dev/null; then
    echo "Darwish Smart Power is not answering on port $PORT. Run install.sh first."
    exit 1
fi

echo "1/5 cloudflared ..."
if ! command -v cloudflared >/dev/null; then
    case "$(dpkg --print-architecture)" in
        amd64) ARCH=amd64 ;; arm64) ARCH=arm64 ;; armhf) ARCH=armhf ;; i386) ARCH=386 ;;
        *) echo "Unsupported CPU: $(dpkg --print-architecture)"; exit 1 ;;
    esac
    curl -fsSL -o /tmp/cloudflared.deb \
        "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$ARCH.deb"
    dpkg -i /tmp/cloudflared.deb >/dev/null
    rm -f /tmp/cloudflared.deb
fi
cloudflared --version

echo "2/5 Cloudflare login ..."
if [ ! -f "$HOME/.cloudflared/cert.pem" ]; then
    echo
    echo "  >>> Open the link below in your browser, sign in to Cloudflare,"
    echo "  >>> click the domain ${HOST#*.} and press Authorize. Then come back here."
    echo
    cloudflared tunnel login
fi

echo "3/5 tunnel '$NAME' ..."
if ! cloudflared tunnel info "$NAME" >/dev/null 2>&1; then
    cloudflared tunnel create "$NAME" >/dev/null
fi
ID="$(cloudflared tunnel list -o json | python3 -c "
import json, sys
print(next(t['id'] for t in json.load(sys.stdin) if t['name'] == '$NAME'))")"
mkdir -p "$CONF_DIR"
CREDS="$CONF_DIR/$ID.json"
if [ ! -f "$CREDS" ]; then
    if [ -f "$HOME/.cloudflared/$ID.json" ]; then
        cp "$HOME/.cloudflared/$ID.json" "$CREDS"
    else
        cloudflared tunnel token --cred-file "$CREDS" "$NAME" >/dev/null
    fi
fi
chmod 600 "$CREDS"

echo "4/5 linking $HOST ..."
cloudflared tunnel route dns --overwrite-dns "$NAME" "$HOST"

cat > "$CONF" <<EOF
tunnel: $ID
credentials-file: $CREDS
ingress:
  - hostname: $HOST
    service: http://localhost:$PORT
  - service: http_status:404
EOF

echo "5/5 starting the tunnel service ..."
cat > "$UNIT" <<EOF
[Unit]
Description=Cloudflare Tunnel for Darwish Smart Power ($HOST)
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=$(command -v cloudflared) --no-autoupdate --config $CONF tunnel run
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable darwish-tunnel >/dev/null 2>&1
systemctl restart darwish-tunnel

for _ in $(seq 1 20); do
    if journalctl -u darwish-tunnel --since "-2min" --no-pager 2>/dev/null | grep -q "Registered tunnel connection"; then
        echo
        echo "=============================================================="
        echo " The tunnel is connected."
        echo
        echo "   https://$HOST/welcome     intro page"
        echo "   https://$HOST/            control panel (the server password)"
        echo
        echo " The first time it can take a minute or two before the name works."
        echo "=============================================================="
        exit 0
    fi
    sleep 2
done

echo
echo "The tunnel did not connect yet. Last log lines:"
journalctl -u darwish-tunnel -n 25 --no-pager || true
exit 1
