#!/usr/bin/env bash
# Installs Darwish Smart Power as a service on this Linux machine, in one step:
#   sudo bash install.sh
# - makes a random password (token) the first time and keeps it on later runs
# - runs the server on port 8095, or the next free port if another program uses it
#   (choose one with: sudo SP_WEB_PORT=9000 bash install.sh)
# - starts it now and after every reboot, opens the firewall ports if ufw is on
# - strips in other homes: sudo SP_PUBLIC_IP=<fixed internet IP> bash install.sh
#   and forward TCP port 10086 on the router to this machine
# Run it again any time to update or repair the installation.
#   --show-password   print the current password again
#   --new-password    replace the password with a new random one
set -euo pipefail

NEW_PASSWORD=0
SHOW_PASSWORD=0
for arg in "$@"; do
    case "$arg" in
        --new-password) NEW_PASSWORD=1 ;;
        --show-password) SHOW_PASSWORD=1 ;;
        *) echo "Unknown option: $arg  (use --show-password or --new-password)"; exit 1 ;;
    esac
done

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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

echo "2/4 choosing a free port ..."
systemctl stop smartpower 2>/dev/null || true   # so our own old copy does not count as "busy"
sleep 1
port_free() {
    # SO_REUSEADDR like the server itself: connections still closing (TIME_WAIT) must not count as "busy",
    # only another program listening on the port does
    python3 -c "
import socket, sys
for host in ('0.0.0.0', '127.0.0.1'):
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, $1))
    except OSError:
        sys.exit(1)
    finally:
        s.close()"
}
if ! port_free 10086; then
    echo "Port 10086 (the power strip's port) is used by another program:"
    ss -lntp 2>/dev/null | grep ':10086 ' || true
    systemctl start smartpower 2>/dev/null || true
    exit 1
fi
PORT="${SP_WEB_PORT:-8095}"
FIRST="$PORT"
while ! port_free "$PORT"; do
    PORT=$((PORT + 1))
    if [ "$PORT" -gt $((FIRST + 50)) ]; then
        echo "No free port found between $FIRST and $((FIRST + 50))."
        exit 1
    fi
done
if [ "$PORT" != "$FIRST" ]; then
    echo "    port $FIRST is used by another program, using $PORT"
fi

echo "    writing the service ..."
TOKEN=""
if [ -f "$UNIT" ] && [ "$NEW_PASSWORD" = 0 ]; then
    TOKEN="$(sed -n 's/^Environment=SP_TOKEN=//p' "$UNIT")"
fi
FRESH_TOKEN=0
if [ -z "$TOKEN" ] || [ "$TOKEN" = "change-me-to-a-long-random-token" ]; then
    # letters and digits only: easy to type on a phone
    TOKEN="$(python3 -c 'import secrets, string; a = string.ascii_letters + string.digits; print("".join(secrets.choice(a) for _ in range(16)))')"
    FRESH_TOKEN=1
fi
# the LAN address other devices use to reach this machine
IP="$(python3 -c 'import socket; s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("10.255.255.255", 1)); print(s.getsockname()[0])' 2>/dev/null || true)"
IP="${IP:-127.0.0.1}"
# the address new strips are told to connect to (port 10086). For strips in other homes give the
# fixed internet IP once:  sudo SP_PUBLIC_IP=203.0.113.5 bash install.sh   (later runs keep it)
STRIP_IP="${SP_PUBLIC_IP:-}"
if [ -z "$STRIP_IP" ] && [ -f "$UNIT" ]; then
    STRIP_IP="$(sed -n 's/^Environment=SP_PUBLIC_IP=//p' "$UNIT")"
fi
STRIP_IP="${STRIP_IP:-$IP}"

cat > "$UNIT" <<EOF
[Unit]
Description=Darwish Smart Power (MTTL-W01 strip server)
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_AS
WorkingDirectory=$DIR
Environment=SP_TOKEN=$TOKEN
Environment=SP_PUBLIC_IP=$STRIP_IP
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
    # the power strips connect to this port; "limit" also drops an address that opens 6+
    # connections in 30 seconds (a real strip connects once and stays connected)
    ufw delete allow 10086/tcp >/dev/null 2>&1 || true
    ufw limit 10086/tcp >/dev/null
    ufw allow 1900/udp >/dev/null       # Alexa: Echo devices look for the outlets here
    ufw allow 52100:52199/tcp >/dev/null  # Alexa: one small endpoint per outlet
    echo "    firewall: opened $PORT, 10086 (rate-limited) and the Alexa ports"
fi

echo "4/4 checking ..."
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    # it must be our server answering, not just anything on that port
    if python3 -c "
import json, sys, urllib.request
req = urllib.request.Request('http://127.0.0.1:$PORT/api/health', headers={'X-Token': '$TOKEN'})
sys.exit(0 if json.load(urllib.request.urlopen(req, timeout=2)).get('app') == 'darwish-smart-power' else 1)
" 2>/dev/null; then
        echo
        echo "=============================================================="
        echo " Darwish Smart Power is running"
        echo
        if [ "$FRESH_TOKEN" = 1 ] || [ "$SHOW_PASSWORD" = 1 ]; then
            echo "   Password (token):  $TOKEN"
            echo "   Keep it private: don't paste it in chats or screenshots."
        else
            echo "   Password (token):  unchanged (see it: sudo bash $DIR/install.sh --show-password)"
        fi
        echo
        echo "   Website:           http://$IP:$PORT/"
        echo "   Control panel:     http://$IP:$PORT/panel"
        echo "   Android app:       http://$IP:$PORT/app.apk"
        echo "   App settings:      address $IP:$PORT (or your domain) + the password"
        echo "   New strips connect to: $STRIP_IP port 10086"
        echo
        if [ "$FRESH_TOKEN" = 1 ]; then
            echo " New password: enter it in the app (Settings > Connection) and the control panel."
            echo " Family members' invite codes keep working."
        fi
        TUNNEL_CONF=/etc/cloudflared/darwish-smart-power.yml
        if [ -f "$TUNNEL_CONF" ] && ! grep -q "localhost:$PORT\$" "$TUNNEL_CONF"; then
            echo
            echo " Domain: run 'sudo bash $DIR/tunnel.sh' so it points to port $PORT."
        fi
        echo "=============================================================="
        exit 0
    fi
    sleep 1
done

echo
echo "The service did not start. Last log lines:"
journalctl -u smartpower -n 25 --no-pager || true
exit 1
