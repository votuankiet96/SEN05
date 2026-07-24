#!/usr/bin/env bash
set -euo pipefail

WATCHER_SERVICE="sen05-signal-watcher-combo-h4.service"
RESTART_SERVICE="sen05-signal-watcher-combo-h4-periodic-restart.service"
TIMER_NAME="sen05-signal-watcher-combo-h4-periodic-restart.timer"
RESTART_UNIT="/etc/systemd/system/${RESTART_SERVICE}"
TIMER_UNIT="/etc/systemd/system/${TIMER_NAME}"
ON_CALENDAR="${1:-03:10}"

if ! systemctl list-unit-files "$WATCHER_SERVICE" >/dev/null 2>&1; then
  echo "Watcher service is not installed yet: $WATCHER_SERVICE"
  echo "Run: bash ops/run_signal_watcher_og8/install_signal_watcher_service.sh"
  exit 1
fi

sudo tee "$RESTART_UNIT" >/dev/null <<EOF
[Unit]
Description=Periodic restart for SEN05 Signal Watcher - Combo H4 Indice
Requires=${WATCHER_SERVICE}

[Service]
Type=oneshot
ExecStart=/bin/systemctl restart ${WATCHER_SERVICE}
EOF

sudo tee "$TIMER_UNIT" >/dev/null <<EOF
[Unit]
Description=Daily code-refresh restart timer for SEN05 Signal Watcher - Combo H4 Indice

[Timer]
OnCalendar=${ON_CALENDAR}
Persistent=true
RandomizedDelaySec=120
Unit=${RESTART_SERVICE}

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now "$TIMER_NAME"

echo
echo "Installed ${TIMER_NAME}"
echo "Schedule: ${ON_CALENDAR}"
echo
systemctl list-timers "$TIMER_NAME" --no-pager || true
echo
echo "Manual check:"
echo "  sudo systemctl status ${TIMER_NAME} --no-pager"
echo "  sudo journalctl -u ${RESTART_SERVICE} -n 50 --no-pager"
