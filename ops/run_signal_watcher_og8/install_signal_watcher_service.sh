#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="sen05-signal-watcher-combo-h4.service"
REPO_DIR="/srv/sen05"
RUN_USER="${SUDO_USER:-$USER}"
RUN_GROUP="$(id -gn "$RUN_USER")"
PYTHON_BIN="/home/${RUN_USER}/.venvs/sen05-og/bin/python"
ENV_FILE="/etc/sen05-signal-watcher.env"
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}"
SYMBOLS="FR40,DE40,HK50,J225,SP35,UK100,US500,US100,US30"

if [[ ! -d "${REPO_DIR}/core_python" ]]; then
  echo "Repo is not mounted at ${REPO_DIR}."
  echo "Run: bash ops/run_signal_watcher_og8/install_mounts.sh"
  exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python venv not found: $PYTHON_BIN"
  echo "Expected the OG8 venv created earlier at ~/.venvs/sen05-og"
  exit 1
fi

sudo tee "$ENV_FILE" >/dev/null <<EOF
PYTHONUNBUFFERED=1
SQL_SERVER=10.11.12.6
SQL_DRIVER=FreeTDS
SQL_PORT=1433
SQL_TDS_VERSION=7.4
EOF

sudo chmod 0644 "$ENV_FILE"

sudo tee "$UNIT_FILE" >/dev/null <<EOF
[Unit]
Description=SEN05 Signal Watcher - Combo H4 Indice
Wants=network-online.target redis-server.service
After=network-online.target redis-server.service
RequiresMountsFor=${REPO_DIR}

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${REPO_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${PYTHON_BIN} -m core_python.notify.signal_watcher --strategy combo --tf H4 --symbols ${SYMBOLS}
Restart=always
RestartSec=15
KillSignal=SIGINT
TimeoutStopSec=90
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo
echo "Installed and restarted ${SERVICE_NAME}"
echo
sudo systemctl status "$SERVICE_NAME" --no-pager --full || true
echo
echo "Follow logs with:"
echo "  sudo journalctl -u ${SERVICE_NAME} -f"
