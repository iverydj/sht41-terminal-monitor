#!/usr/bin/env bash
set -euo pipefail

APP_DIRECTORY=/opt/sht41-terminal-monitor
STATE_DIRECTORY=/var/lib/sht41-terminal-monitor
SERVICE_NAME=sht41-logger.service
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"
LAUNCHER_PATH=/usr/local/bin/sht41-monitor

if [[ $EUID -ne 0 ]]; then
    echo "오류: sudo ./install.sh 로 실행하세요." >&2
    exit 1
fi

if [[ -z ${SUDO_USER:-} || $SUDO_USER == root ]]; then
    echo "오류: root 로그인에서 직접 실행하지 말고 일반 사용자 계정에서 sudo로 실행하세요." >&2
    exit 1
fi

SCRIPT_DIRECTORY=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
INSTALL_USER=$SUDO_USER
INSTALL_GROUP=$(id -gn "$INSTALL_USER")

for required_file in sht41_monitor.py sht41-logger.service.in sht41-monitor; do
    if [[ ! -f "$SCRIPT_DIRECTORY/$required_file" ]]; then
        echo "오류: 배포 파일이 빠졌습니다: $required_file" >&2
        exit 1
    fi
done

echo "[1/5] Ubuntu 패키지 확인"
apt-get update
apt-get install -y python3 python3-serial

echo "[2/5] 프로그램 설치"
systemctl stop "$SERVICE_NAME" 2>/dev/null || true
install -d -m 0755 "$APP_DIRECTORY"
install -m 0644 "$SCRIPT_DIRECTORY/sht41_monitor.py" "$APP_DIRECTORY/sht41_monitor.py"

echo "[3/5] 데이터 폴더와 USB 권한 준비"
install -d -o "$INSTALL_USER" -g "$INSTALL_GROUP" -m 0750 "$STATE_DIRECTORY"

echo "[4/5] systemd 로거 등록"
sed \
    -e "s/@INSTALL_USER@/$INSTALL_USER/g" \
    -e "s/@INSTALL_GROUP@/$INSTALL_GROUP/g" \
    "$SCRIPT_DIRECTORY/sht41-logger.service.in" > "$SERVICE_PATH"
chmod 0644 "$SERVICE_PATH"
install -m 0755 "$SCRIPT_DIRECTORY/sht41-monitor" "$LAUNCHER_PATH"
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"

echo "[5/5] 설치 확인"
systemctl --no-pager --full status "$SERVICE_NAME" || true
echo
echo "설치 완료"
echo "  모니터 실행: sht41-monitor"
echo "  기록 상태:   systemctl status $SERVICE_NAME"
echo "  데이터 위치: $STATE_DIRECTORY/data"
