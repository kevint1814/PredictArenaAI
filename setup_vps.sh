#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# PredictArena VPS Setup
# Run ONCE on a fresh Ubuntu 22.04 server as root.
# Usage: bash setup_vps.sh
# ─────────────────────────────────────────────────────────────────────────────
set -e

# ── Edit this before running ──────────────────────────────────────────────────
GITHUB_REPO="https://github.com/YOUR_USERNAME/YOUR_REPO.git"
# ─────────────────────────────────────────────────────────────────────────────

APP_DIR="/opt/predicarena"

echo ""
echo "=== [1/5] Installing system packages ==="
apt-get update -y -q
apt-get install -y -q python3 python3-venv python3-pip git

echo ""
echo "=== [2/5] Cloning repo ==="
git clone "$GITHUB_REPO" "$APP_DIR"
cd "$APP_DIR"

echo ""
echo "=== [3/5] Creating virtual environment & installing dependencies ==="
python3 -m venv venv
venv/bin/pip install --upgrade pip --quiet
venv/bin/pip install -r requirements.txt --quiet
echo "Dependencies installed."

echo ""
echo "=== [4/5] Setting up .env ==="
cp .env.example .env
echo ""
echo "─────────────────────────────────────────────────────────────────────────"
echo "  Open a new terminal tab and fill in your secrets:"
echo ""
echo "      nano $APP_DIR/.env"
echo ""
echo "  Key things to set:"
echo "    TELEGRAM_BOT_TOKEN, TELEGRAM_GROUP_ID, ADMIN_TELEGRAM_ID, USER_2_TELEGRAM_ID"
echo "    At least one AI key (OPENAI_API_KEY / GROK_API_KEY / GEMINI_API_KEY)"
echo ""
echo "  Leave WEBHOOK_URL blank — polling mode is used on VPS (no domain needed)."
echo "─────────────────────────────────────────────────────────────────────────"
echo ""
read -rp "Press Enter once you've saved the .env file..."

echo ""
echo "=== [5/5] Installing systemd service ==="
cat > /etc/systemd/system/predicarena.service << 'UNIT'
[Unit]
Description=PredictArena AI — Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/predicarena
ExecStart=/opt/predicarena/venv/bin/python main.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable predicarena
systemctl start predicarena

sleep 3
echo ""
if systemctl is-active --quiet predicarena; then
    echo "✅ predicarena is running!"
    echo ""
    echo "  Useful commands:"
    echo "    systemctl status predicarena       — quick status"
    echo "    journalctl -u predicarena -f       — live logs"
    echo "    systemctl restart predicarena      — manual restart"
else
    echo "❌ Service failed to start. Recent logs:"
    journalctl -u predicarena -n 40 --no-pager
    echo ""
    echo "Fix the issue above, then run: systemctl start predicarena"
fi
