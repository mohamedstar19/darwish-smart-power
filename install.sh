#!/usr/bin/env bash
# Installs Darwish Smart Power as a service on this Linux machine, in one step:
#   sudo bash install.sh
# - makes a random password (token) the first time and keeps it on later runs
# - runs the server on port 8090 (change with: sudo SP_WEB_PORT=9000 bash install.sh)
# - starts it now and after every reboot, opens the firewall ports if ufw is on
# Run it again any time to update or repair the installation.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${SP_WEB_PORT:-8090}"
UNIT=/etc/systemd/system/smartpower.service
RUN_AS="${SUDO_USER:-$(id -un)}"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run it with sudo:  sudo bash $0"
    exit 1
fi
if ! command -v python3 >/dev/null; then
    echo "python3 is missing. Install it with:  sudo apt install -y python3"
    exit 1
fi

echo "1/4 checking the server code ..."
python3 "$DIR/smartpower.py" selftest >/dev/null

echo "2/4 writing the service ..."
TOKEN=""
if [ -f "$UNIT" ]; then
    TOKEN="$(sed -n 's/^Environment=SP_TOKEN=//p' "$UNIT")"
fi
if [ -z "$TOKEN" ] || [ "$TOKEN" = "change-me-to-a-long-random-token" ]; then
    TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"
fi
# the LAN address other devices use to reach this machine
IP="$(python3 -c 'import socket; s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("10.255.255.255", 1)); print(s.getsockname()[0])' 2>/dev/null || true)"
IP="${IP:-127.0.0.1}"

cat > "$UNIT" <<EOF
[Unit]
Description=Darwish Smart Power (MTTL-W01 strip server)
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_AS
WorkingDirectory=$DIR
Environment=SP_TOKEN=$TOKEN
Environment=SP_PUBLIC_IP=$IP
Environment=SP_WEB_PORT=$PORT
ExecStart=$(command -v python3) $DIR/smartpower.py serve
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
chmod 600 "$UNIT"                       # the token is in this file

echo "3/4 starting it ..."
systemctl daemon-reload
systemctl enable smartpower >/dev/null 2>&1
systemctl restart smartpower

if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow "$PORT/tcp" >/dev/null
    ufw allow 10086/tcp >/dev/null      # the power strip connects to this port
    echo "    firewall: opened $PORT and 10086"
fi

echo "4/4 checking ..."
for _ in 1 2 3 4 5 6 7 8 9 10; do
    if python3 -c "import socket; socket.create_connection(('127.0.0.1', $PORT), 1).close()" 2>/dev/null; then
        echo
        echo "=============================================================="
        echo " Darwish Smart Power is running"
        echo
        echo "   Password (token):  $TOKEN"
        echo
        echo "   Control panel:     http://$IP:$PORT/"
        echo "   Intro page:        http://$IP:$PORT/welcome"
        echo "   Android app:       Settings -> address $IP:$PORT + the password"
        echo
        echo " In the browser: any user name, and the password above."
        echo " Run 'sudo bash $DIR/install.sh' again to see it later."
        echo "=============================================================="
        exit 0
    fi
    sleep 1
done

echo
echo "The service did not start. Last log lines:"
journalctl -u smartpower -n 25 --no-pager || true
exit 1
