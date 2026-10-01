#!/usr/bin/env bash
# Quiz 2026 installer for serverlab (Ubuntu) — run with: sudo bash install.sh
# Idempotent: re-run after copying a new app.py to ~/ to upgrade (data is kept).
set -euo pipefail

APP_DIR=/opt/quiz2026
DATA_DIR=$APP_DIR/data
PORT=${QUIZ_PORT:-5052}
HOSTNAME_FQDN=${QUIZ_HOST:-quiz.alcosta-cse.net}
SRC_DIR=$(cd "$(dirname "$0")" && pwd)
RUN_USER=quiz

[[ $EUID -eq 0 ]] || { echo "Run with sudo"; exit 1; }
[[ -f $SRC_DIR/app.py ]] || { echo "app.py not found next to install.sh"; exit 1; }

echo "==> Packages"
apt-get install -y -q python3-venv dos2unix >/dev/null

echo "==> Normalising line endings (CRLF -> LF)"
dos2unix -q "$SRC_DIR/app.py" 2>/dev/null || true

echo "==> Service user and folders"
id $RUN_USER >/dev/null 2>&1 || useradd --system --home $APP_DIR --shell /usr/sbin/nologin $RUN_USER
mkdir -p $DATA_DIR/images
install -m 0644 "$SRC_DIR/app.py" $APP_DIR/app.py

echo "==> Python venv"
[[ -d $APP_DIR/venv ]] || python3 -m venv $APP_DIR/venv
$APP_DIR/venv/bin/pip install -q --upgrade pip
$APP_DIR/venv/bin/pip install -q flask gunicorn

echo "==> Environment file (/etc/quiz2026.env)"
if [[ ! -f /etc/quiz2026.env ]]; then
  PIN=$(tr -dc '0-9' </dev/urandom | head -c 6)
  cat >/etc/quiz2026.env <<EOF
QUIZ_DATA_DIR=$DATA_DIR
QUIZ_ADMIN_PIN=$PIN
EOF
  chmod 600 /etc/quiz2026.env
  echo "    Generated admin PIN: $PIN   (change it in /etc/quiz2026.env)"
else
  echo "    Keeping existing /etc/quiz2026.env"
fi
chown -R $RUN_USER:$RUN_USER $APP_DIR

echo "==> systemd unit"
cat >/etc/systemd/system/quiz2026.service <<EOF
[Unit]
Description=Quiz 2026 (Flask/gunicorn)
After=network.target

[Service]
User=$RUN_USER
Group=$RUN_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=/etc/quiz2026.env
# 1 worker on purpose: JSON storage is guarded by an in-process lock
ExecStart=$APP_DIR/venv/bin/gunicorn --workers 1 --threads 8 --bind 127.0.0.1:$PORT app:app
Restart=always
RestartSec=3
NoNewPrivileges=true
ProtectSystem=full
ReadWritePaths=$DATA_DIR

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now quiz2026 >/dev/null
systemctl restart quiz2026

if command -v nginx >/dev/null; then
  echo "==> nginx site ($HOSTNAME_FQDN -> 127.0.0.1:$PORT)"
  cat >/etc/nginx/sites-available/quiz2026 <<EOF
server {
    listen 80;
    server_name $HOSTNAME_FQDN;
    client_max_body_size 6m;
    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
  ln -sf /etc/nginx/sites-available/quiz2026 /etc/nginx/sites-enabled/quiz2026
  nginx -t && systemctl reload nginx
fi

sleep 2
if curl -fsS http://127.0.0.1:$PORT/healthz >/dev/null; then
  echo "==> OK: quiz2026 is running on 127.0.0.1:$PORT"
else
  echo "!! Service not answering — check: journalctl -u quiz2026 -n 50"; exit 1
fi
cat <<EOF

Next step (Cloudflare Tunnel): add a public hostname
   $HOSTNAME_FQDN  ->  http://localhost:80   (via nginx)
   or                  http://localhost:$PORT (direct)
Player page : https://$HOSTNAME_FQDN/
Admin page  : https://$HOSTNAME_FQDN/admin   (PIN in /etc/quiz2026.env)
EOF
