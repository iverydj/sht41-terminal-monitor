#!/usr/bin/env bash
set -euo pipefail

APP_DIRECTORY=/opt/sht41-terminal-monitor
STATE_DIRECTORY=/var/lib/sht41-terminal-monitor
SERVICE_NAME=sht41-logger.service
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"
LAUNCHER_PATH=/usr/local/bin/sht41-monitor

if [[ $EUID -ne 0 ]]; then
    echo "Error: run this script as: sudo ./install.sh" >&2
    exit 1
fi

if [[ -z ${SUDO_USER:-} || $SUDO_USER == root ]]; then
    echo "Error: do not run this from a root login; use sudo from a regular user account." >&2
    exit 1
fi

SCRIPT_DIRECTORY=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
INSTALL_USER=$SUDO_USER
INSTALL_GROUP=$(id -gn "$INSTALL_USER")

for required_file in sht41_monitor.py sht41-logger.service.in sht41-monitor; do
    if [[ ! -f "$SCRIPT_DIRECTORY/$required_file" ]]; then
        echo "Error: missing distribution file: $required_file" >&2
        exit 1
    fi
done

echo "[1/5] Installing Ubuntu packages"
apt-get update
apt-get install -y python3 python3-serial

echo "[2/5] Installing the program"
systemctl stop "$SERVICE_NAME" 2>/dev/null || true
install -d -m 0755 "$APP_DIRECTORY"
install -m 0644 "$SCRIPT_DIRECTORY/sht41_monitor.py" "$APP_DIRECTORY/sht41_monitor.py"

echo "[3/5] Preparing the data folder and USB permissions"
install -d -o "$INSTALL_USER" -g "$INSTALL_GROUP" -m 0750 "$STATE_DIRECTORY"

echo "[4/5] Registering the systemd logger"
sed \
    -e "s/@INSTALL_USER@/$INSTALL_USER/g" \
    -e "s/@INSTALL_GROUP@/$INSTALL_GROUP/g" \
    "$SCRIPT_DIRECTORY/sht41-logger.service.in" > "$SERVICE_PATH"
chmod 0644 "$SERVICE_PATH"
install -m 0755 "$SCRIPT_DIRECTORY/sht41-monitor" "$LAUNCHER_PATH"
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"

echo "[5/5] Checking the installation"
systemctl --no-pager --full status "$SERVICE_NAME" || true
echo
echo "Installation complete"
echo "  Start the monitor: sht41-monitor"
echo "  Logger status:     systemctl status $SERVICE_NAME"
echo "  Data folder:       $STATE_DIRECTORY/data"
