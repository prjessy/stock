#!/usr/bin/env bash
# stock watchdog · VPS 1회 배포 / 코드 갱신 스크립트 (Ubuntu/Debian)
#
# 사용법 (VPS 터미널에서):
#   git clone https://github.com/prjessy/stock.git ~/stock-watchdog
#   cd ~/stock-watchdog
#   cp .env.example .env && nano .env     # KIS_APP_KEY / KIS_APP_SECRET 입력 후 저장
#   bash deploy/deploy.sh
#
# 코드 갱신 시(이후): cd ~/stock-watchdog && git pull && bash deploy/deploy.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SVC_USER="$(id -un)"
PY="$APP_DIR/.venv/bin/python"
SERVICE=/etc/systemd/system/stock-watchdog.service

echo "▶ APP_DIR=$APP_DIR  USER=$SVC_USER"

# 1) 시스템 의존성
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip git

# 2) 파이썬 가상환경 + 패키지
[ -d "$APP_DIR/.venv" ] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# 3) .env 확인 — git 에서 절대 안 오므로 직접 만들어야 함
if [ ! -f "$APP_DIR/.env" ]; then
  echo ""
  echo "⚠️  $APP_DIR/.env 가 없습니다. 키를 채운 .env 를 먼저 만드세요:"
  echo "      cp $APP_DIR/.env.example $APP_DIR/.env"
  echo "      nano $APP_DIR/.env        # KIS_APP_KEY / KIS_APP_SECRET 입력"
  echo "   그런 다음 이 스크립트를 다시 실행하세요."
  exit 1
fi
chmod 600 "$APP_DIR/.env"   # 키 파일 권한 잠그기(소유자만 읽기)

# 4) systemd 서비스 생성 (현재 사용자/경로로 자동 작성)
sudo tee "$SERVICE" >/dev/null <<UNIT
[Unit]
Description=stock watchdog realtime dashboard (KIS)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SVC_USER
WorkingDirectory=$APP_DIR
ExecStart=$PY -m app.web
Restart=always
RestartSec=3
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
UNIT

# 5) 방화벽(ufw 활성 시 8000 개방). 클라우드 제공사 보안그룹도 따로 열어야 할 수 있음.
if command -v ufw >/dev/null && sudo ufw status | grep -q active; then
  sudo ufw allow 8000/tcp || true
fi

# 6) 서비스 시작/자동기동 등록
sudo systemctl daemon-reload
sudo systemctl enable stock-watchdog
# restart(=enable --now 와 달리 이미 떠 있어도 프로세스 교체) — 파이썬 코드 변경 반영 보장.
sudo systemctl restart stock-watchdog
sleep 2
sudo systemctl --no-pager status stock-watchdog || true

echo ""
echo "✅ 배포 완료."
echo "   로컬 확인:  curl -s http://localhost:8000/api/session"
echo "   외부 접속:  http://<이_VPS_IP>:8000"
echo "   로그:       sudo journalctl -u stock-watchdog -f"
