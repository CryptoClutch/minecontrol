#!/bin/bash
# MineControl agent installer for a new rig.
# Review this script before running it - it installs a systemd service that
# runs as root and can restart your miner.
#
# Usage:
#   git clone <YOUR_REPO_URL> minecontrol
#   cd minecontrol
#   sudo bash agent/install.sh

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_PATH="/etc/systemd/system/minecontrol-agent.service"

echo "MineControl agent installer"
echo "Repo path: ${REPO_DIR}"
echo ""

read -p "Head node URL (e.g. http://192.168.1.10:8000): " HEAD_URL
read -p "Rig name (must match what you'll register, e.g. superserver): " RIG_NAME
read -p "Rig ID (leave blank if not registered yet - you can set this later): " RIG_ID
read -p "Board type - 'octominer', 'none', or leave blank to auto-detect: " BOARD_TYPE
read -p "Allow the watchdog to reboot this rig if it stays unhealthy? [Y/n]: " ALLOW_REBOOT_INPUT
ALLOW_REBOOT="true"
[[ "$ALLOW_REBOOT_INPUT" =~ ^[Nn] ]] && ALLOW_REBOOT="false"

pip3 install --break-system-packages -q requests 2>/dev/null || pip3 install -q requests

cat > "${SERVICE_PATH}" << EOF
[Unit]
Description=MineControl Rig Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${REPO_DIR}/agent
Environment="MINECONTROL_HEAD_URL=${HEAD_URL}"
Environment="MINECONTROL_RIG_NAME=${RIG_NAME}"
Environment="MINECONTROL_RIG_ID=${RIG_ID}"
Environment="PEAKMINER_API_URL=http://127.0.0.1:4068/summary"
Environment="MINECONTROL_ALLOW_REBOOT=${ALLOW_REBOOT}"
$( [ -n "$BOARD_TYPE" ] && echo "Environment=\"MINECONTROL_BOARD_TYPE=${BOARD_TYPE}\"" )
ExecStart=/usr/bin/python3 ${REPO_DIR}/agent/agent.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload

echo ""
echo "Service file written to ${SERVICE_PATH}"
if [ -z "${RIG_ID}" ]; then
  echo "No rig ID set yet - register this rig with the head node first:"
  echo "  curl -X POST ${HEAD_URL}/api/rigs -H 'Content-Type: application/json' \\"
  echo "    -d '{\"name\":\"${RIG_NAME}\",\"lan_ip\":\"<this-rigs-lan-ip>\",\"agent_type\":\"native\"}'"
  echo "Then edit ${SERVICE_PATH} to set MINECONTROL_RIG_ID to the returned id, and:"
  echo "  systemctl daemon-reload"
fi
echo "Start the agent with:"
echo "  systemctl enable --now minecontrol-agent"
