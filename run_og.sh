#!/usr/bin/env bash

# OG Program launcher for Ubuntu/Linux.
# Long-running actions open in a new desktop terminal when available.
# On headless/SSH sessions, actions run in the current terminal.

set -u

APP_TITLE="OG Program Launcher"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD_URL="${DASHBOARD_URL:-http://127.0.0.1:8516}"
PY_CMD="./.venv/bin/python"
SYSCTL="systemctl --user"

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

cd "$APP_DIR" || exit 1

quote() {
  printf "%q" "$1"
}

clear_screen() {
  clear 2>/dev/null || printf "\033c"
}

pause() {
  echo
  read -r -p "Press Enter to continue..."
}

has_desktop() {
  [[ -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ]]
}

launch_terminal() {
  local title="$1"
  local cmd="$2"
  local script
  script="cd $(quote "$APP_DIR") && $cmd; rc=\$?; echo; read -r -p 'Press Enter to close...'; exit \$rc"

  if ! has_desktop; then
    return 1
  fi

  if command -v gnome-terminal >/dev/null 2>&1; then
    gnome-terminal --title="$title" -- bash -lc "$script" >/dev/null 2>&1 &
    return 0
  fi
  if command -v x-terminal-emulator >/dev/null 2>&1; then
    x-terminal-emulator -T "$title" -e bash -lc "$script" >/dev/null 2>&1 &
    return 0
  fi
  if command -v xterm >/dev/null 2>&1; then
    xterm -T "$title" -e bash -lc "$script" >/dev/null 2>&1 &
    return 0
  fi
  return 1
}

run_task() {
  local title="$1"
  local cmd="$2"

  if [[ "${OG_LAUNCH_MODE:-auto}" != "current" ]] && launch_terminal "$title" "$cmd"; then
    return 0
  fi

  clear_screen
  echo "==================== $title ===================="
  echo
  bash -lc "cd $(quote "$APP_DIR") && $cmd"
  pause
}

open_project_shell() {
  local script="cd $(quote "$APP_DIR") && exec bash -l"
  if [[ "${OG_LAUNCH_MODE:-auto}" != "current" ]] && has_desktop; then
    if command -v gnome-terminal >/dev/null 2>&1; then
      gnome-terminal --title="OG Shell" -- bash -lc "$script" >/dev/null 2>&1 &
      return
    fi
    if command -v x-terminal-emulator >/dev/null 2>&1; then
      x-terminal-emulator -T "OG Shell" -e bash -lc "$script" >/dev/null 2>&1 &
      return
    fi
  fi
  bash -l
}

open_dashboard() {
  run_task "OG Past - Dashboard Health" "$PY_CMD -m og_past.ops health --base-url $(quote "$DASHBOARD_URL")"
  if has_desktop && command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$DASHBOARD_URL" >/dev/null 2>&1 &
  else
    echo
    echo "Open dashboard in a browser:"
    echo "  $DASHBOARD_URL"
    pause
  fi
}

prompt_default() {
  local var_name="$1"
  local label="$2"
  local current="${!var_name}"
  local value
  read -r -p "$label [$current]: " value
  if [[ -n "$value" ]]; then
    printf -v "$var_name" "%s" "$value"
  fi
}

collect_params() {
  EXTRA_PARAMS=""
  local one_param
  while true; do
    read -r -p "Extra strategy param NAME=VALUE, blank when done: " one_param
    [[ -z "$one_param" ]] && break
    EXTRA_PARAMS+=" --param $(quote "$one_param")"
  done
}

main_menu() {
  while true; do
    clear_screen
    cat <<EOF
============================================================
$APP_TITLE
Backend: Ubuntu/Linux local
Project: $APP_DIR
============================================================

1. OG Engine  - config, services, strategies, tests
2. OG Past    - dashboard and CSV export
3. OG Live    - Stream and Pub/Sub live mechanisms
4. Quick status for all services
0. Exit

EOF
    read -r -p "Choose: " choice
    case "$choice" in
      1) engine_menu ;;
      2) past_menu ;;
      3) live_menu ;;
      4) quick_status ;;
      0) exit 0 ;;
    esac
  done
}

engine_menu() {
  while true; do
    clear_screen
    cat <<EOF
==================== OG Engine ====================

1. Show OG operation config
2. Show selected strategy signal rules
3. Show production service status
4. Start all production services
5. Stop all production services
6. Restart all production services
7. Run lint/tests/static audit
8. Open project shell
0. Back

EOF
    read -r -p "Choose: " choice
    case "$choice" in
      1) run_task "OG Engine - Config" "$PY_CMD -m og_core.ops config" ;;
      2) show_strategy_rules ;;
      3) run_task "OG Engine - Service Status" "$PY_CMD -m og_core.ops services" ;;
      4) run_task "OG Engine - Start Services" "$SYSCTL start og-live-stream.service og-live-pubsub.service og-dashboard.service og-live-stream-healthcheck.timer og-live-pubsub-healthcheck.timer; $PY_CMD -m og_core.ops services" ;;
      5) run_task "OG Engine - Stop Services" "$SYSCTL stop og-live-stream.service og-live-pubsub.service og-dashboard.service og-live-stream-healthcheck.timer og-live-pubsub-healthcheck.timer; $PY_CMD -m og_core.ops services" ;;
      6) run_task "OG Engine - Restart Services" "$SYSCTL restart og-live-stream.service og-live-pubsub.service og-dashboard.service; $SYSCTL restart og-live-stream-healthcheck.timer og-live-pubsub-healthcheck.timer; $PY_CMD -m og_core.ops services" ;;
      7) run_task "OG Engine - Validate" "$PY_CMD -m og_core.ops validate" ;;
      8) open_project_shell ;;
      0) return ;;
    esac
  done
}

show_strategy_rules() {
  local strategy="combo"
  echo
  echo "Strategy signal rules"
  echo "Available: combo, ma_cross, ai_trend, knn_combo"
  read -r -p "Strategy key [combo]: " strategy_input
  if [[ -n "${strategy_input:-}" ]]; then
    strategy="$strategy_input"
  fi
  run_task "OG Engine - Strategy $strategy" "$PY_CMD -m og_core.ops strategies --strategy $(quote "$strategy")"
}

past_menu() {
  while true; do
    clear_screen
    cat <<EOF
==================== OG Past ====================

1. Open dashboard
2. Dashboard health and service
3. Export single-symbol signal CSV
4. Export bulk signal CSV
5. Diagnostics and logs
0. Back

EOF
    read -r -p "Choose: " choice
    case "$choice" in
      1) open_dashboard ;;
      2) past_service_menu ;;
      3) export_single ;;
      4) export_bulk ;;
      5) past_diagnostics_menu ;;
      0) return ;;
    esac
  done
}

past_service_menu() {
  while true; do
    clear_screen
    cat <<EOF
============== OG Past - Dashboard Service ==============

1. Show dashboard status and health
2. Start dashboard service
3. Restart dashboard service
4. Stop dashboard service
0. Back

EOF
    read -r -p "Choose: " choice
    case "$choice" in
      1) run_task "OG Past - Dashboard Status" "$SYSCTL status og-dashboard.service --no-pager; $PY_CMD -m og_past.ops health --base-url $(quote "$DASHBOARD_URL")" ;;
      2) run_task "OG Past - Start Dashboard" "$SYSCTL start og-dashboard.service; $SYSCTL status og-dashboard.service --no-pager; $PY_CMD -m og_past.ops health --base-url $(quote "$DASHBOARD_URL")" ;;
      3) run_task "OG Past - Restart Dashboard" "$SYSCTL restart og-dashboard.service; $SYSCTL status og-dashboard.service --no-pager; $PY_CMD -m og_past.ops health --base-url $(quote "$DASHBOARD_URL")" ;;
      4) run_task "OG Past - Stop Dashboard" "$SYSCTL stop og-dashboard.service; $SYSCTL status og-dashboard.service --no-pager" ;;
      0) return ;;
    esac
  done
}

past_diagnostics_menu() {
  while true; do
    clear_screen
    cat <<EOF
================ OG Past - Diagnostics ================

1. Follow dashboard logs
2. Run dashboard API smoke test
3. Show latest CSV exports
4. Run dashboard foreground for debugging
0. Back

EOF
    read -r -p "Choose: " choice
    case "$choice" in
      1) run_task "OG Past - Dashboard Logs" "journalctl --user -u og-dashboard.service -f" ;;
      2) run_task "OG Past - API Smoke Test" "$PY_CMD -m og_past.ops smoke --base-url $(quote "$DASHBOARD_URL")" ;;
      3) run_task "OG Past - Latest CSV Exports" "$PY_CMD -m og_past.ops exports --dir runtime/exports --limit 20" ;;
      4) run_task "OG Past - Dashboard Foreground Debug" "$PY_CMD -m og_past.main --host 127.0.0.1 --port 8516" ;;
      0) return ;;
    esac
  done
}

live_menu() {
  while true; do
    clear_screen
    cat <<EOF
==================== OG Live ====================

1. Stream mechanism
2. Pub/Sub mechanism
3. Health for both mechanisms
4. Follow both logs
5. Audit logs and compare
0. Back

EOF
    read -r -p "Choose: " choice
    case "$choice" in
      1) live_stream_menu ;;
      2) live_pubsub_menu ;;
      3) run_task "OG Live - Both Health" "$PY_CMD -m og_live.stream_mechanism.ops health; $PY_CMD -m og_live.pubsub_mechanism.ops health" ;;
      4) run_task "OG Live - Both Logs" "journalctl --user -u og-live-stream.service -u og-live-pubsub.service -f" ;;
      5) live_audit_menu ;;
      0) return ;;
    esac
  done
}

live_audit_menu() {
  while true; do
    clear_screen
    cat <<EOF
================= OG Live Audit Logs =================

1. Show latest audit events for both mechanisms
2. Compare Stream vs Pub/Sub by snapshot
3. Compare one strategy / symbol / timeframe
4. Show signal-publish events only
0. Back

EOF
    read -r -p "Choose: " choice
    case "$choice" in
      1) run_task "OG Live - Audit Events" "$PY_CMD -m og_live.ops audit --mechanism both --limit 60" ;;
      2) run_task "OG Live - Audit Compare" "$PY_CMD -m og_live.ops compare --limit 40" ;;
      3) live_audit_pair_compare ;;
      4) run_task "OG Live - Signal Publish Audit" "$PY_CMD -m og_live.ops audit --mechanism both --limit 80 --stage signal_published --stage signal_queued --stage signal_skipped" ;;
      0) return ;;
    esac
  done
}

live_audit_pair_compare() {
  STRATEGY="combo"
  SYMBOL="HK50"
  TF="H4"

  echo
  echo "Audit compare filter"
  prompt_default STRATEGY "Strategy"
  prompt_default SYMBOL "Symbol"
  prompt_default TF "Timeframe"

  run_task "OG Live - Audit Compare $STRATEGY $SYMBOL $TF" "$PY_CMD -m og_live.ops compare --strategy $(quote "$STRATEGY") --symbol $(quote "$SYMBOL") --timeframe $(quote "$TF") --limit 40"
}

live_stream_menu() {
  while true; do
    clear_screen
    cat <<EOF
================= OG Live Stream =================

1. Health
2. Redis stream/state and latest signal
3. Service status
4. Start service
5. Restart service
6. Stop service
7. Follow logs
8. Debug tools
0. Back

EOF
    read -r -p "Choose: " choice
    case "$choice" in
      1) run_task "OG Live Stream - Health" "$PY_CMD -m og_live.stream_mechanism.ops health" ;;
      2) run_task "OG Live Stream - Inspect" "$PY_CMD -m og_live.stream_mechanism.ops inspect" ;;
      3) run_task "OG Live Stream - Service Status" "$SYSCTL status og-live-stream.service --no-pager; $PY_CMD -m og_live.stream_mechanism.ops health" ;;
      4) run_task "OG Live Stream - Start" "$SYSCTL start og-live-stream.service; $SYSCTL status og-live-stream.service --no-pager; $PY_CMD -m og_live.stream_mechanism.ops health" ;;
      5) run_task "OG Live Stream - Restart" "$SYSCTL restart og-live-stream.service; $SYSCTL status og-live-stream.service --no-pager; $PY_CMD -m og_live.stream_mechanism.ops health" ;;
      6) run_task "OG Live Stream - Stop" "$SYSCTL stop og-live-stream.service; $SYSCTL status og-live-stream.service --no-pager || true" ;;
      7) run_task "OG Live Stream - Logs" "journalctl --user -u og-live-stream.service -f" ;;
      8) live_stream_debug_menu ;;
      0) return ;;
    esac
  done
}

live_stream_debug_menu() {
  while true; do
    clear_screen
    cat <<EOF
================= OG Live Stream - Debug Tools =================

1. Run stream once smoke test
2. Run stream foreground
3. Run strict healthcheck
0. Back

Warning: once/foreground debug commands can consume real Redis event entries.
Use them only when you intentionally debug the Stream mechanism.

EOF
    read -r -p "Choose: " choice
    case "$choice" in
      1) run_task "OG Live Stream - Once Debug" "$PY_CMD -m og_live.stream_mechanism.main --once" ;;
      2) run_task "OG Live Stream - Foreground Debug" "$PY_CMD -m og_live.stream_mechanism.main" ;;
      3) run_task "OG Live Stream - Strict Healthcheck" "$PY_CMD -m og_live.stream_mechanism.ops health --fail-on-warn" ;;
      0) return ;;
    esac
  done
}

live_pubsub_menu() {
  while true; do
    clear_screen
    cat <<EOF
================= OG Live Pub/Sub =================

1. Health
2. Pub/Sub channel/state and latest signal
3. Service status
4. Start service
5. Restart service
6. Stop service
7. Follow logs
8. Debug tools
0. Back

EOF
    read -r -p "Choose: " choice
    case "$choice" in
      1) run_task "OG Live Pub/Sub - Health" "$PY_CMD -m og_live.pubsub_mechanism.ops health" ;;
      2) run_task "OG Live Pub/Sub - Inspect" "$PY_CMD -m og_live.pubsub_mechanism.ops inspect" ;;
      3) run_task "OG Live Pub/Sub - Service Status" "$SYSCTL status og-live-pubsub.service --no-pager; $PY_CMD -m og_live.pubsub_mechanism.ops health" ;;
      4) run_task "OG Live Pub/Sub - Start" "$SYSCTL start og-live-pubsub.service; $SYSCTL status og-live-pubsub.service --no-pager; $PY_CMD -m og_live.pubsub_mechanism.ops health" ;;
      5) run_task "OG Live Pub/Sub - Restart" "$SYSCTL restart og-live-pubsub.service; $SYSCTL status og-live-pubsub.service --no-pager; $PY_CMD -m og_live.pubsub_mechanism.ops health" ;;
      6) run_task "OG Live Pub/Sub - Stop" "$SYSCTL stop og-live-pubsub.service; $SYSCTL status og-live-pubsub.service --no-pager || true" ;;
      7) run_task "OG Live Pub/Sub - Logs" "journalctl --user -u og-live-pubsub.service -f" ;;
      8) live_pubsub_debug_menu ;;
      0) return ;;
    esac
  done
}

live_pubsub_debug_menu() {
  while true; do
    clear_screen
    cat <<EOF
================= OG Live Pub/Sub - Debug Tools =================

1. Run Pub/Sub once smoke test
2. Run Pub/Sub foreground
3. Run strict healthcheck
0. Back

Warning: once/foreground debug commands subscribe to live Pub/Sub messages.
Use them only when you intentionally debug the Pub/Sub mechanism.

EOF
    read -r -p "Choose: " choice
    case "$choice" in
      1) run_task "OG Live Pub/Sub - Once Debug" "$PY_CMD -m og_live.pubsub_mechanism.main --once --timeout-seconds 60" ;;
      2) run_task "OG Live Pub/Sub - Foreground Debug" "$PY_CMD -m og_live.pubsub_mechanism.main" ;;
      3) run_task "OG Live Pub/Sub - Strict Healthcheck" "$PY_CMD -m og_live.pubsub_mechanism.ops health --fail-on-warn" ;;
      0) return ;;
    esac
  done
}

export_single() {
  echo
  echo "Single-symbol CSV export"
  STRATEGY="combo"
  SYMBOL="US30"
  TF="H1"
  BARS="500"
  COLS="bartime,side,signal,entry_price,sl_price,tp_price,risk_reward,signal_reason"
  START_DATE=""
  END_DATE=""

  prompt_default STRATEGY "Strategy"
  prompt_default SYMBOL "Symbol"
  prompt_default TF "Timeframe"
  prompt_default BARS "Bars"
  prompt_default COLS "Columns"
  read -r -p "Start date optional (YYYY-MM-DD or dd/mm/yyyy): " START_DATE
  read -r -p "End date optional   (YYYY-MM-DD or dd/mm/yyyy): " END_DATE
  collect_params

  local cmd="$PY_CMD -m og_past.export_cli single --strategy $(quote "$STRATEGY") --symbol $(quote "$SYMBOL") --tf $(quote "$TF") --bars $(quote "$BARS") --cols $(quote "$COLS") --output-dir runtime/exports"
  [[ -n "$START_DATE" ]] && cmd+=" --start-date $(quote "$START_DATE")"
  [[ -n "$END_DATE" ]] && cmd+=" --end-date $(quote "$END_DATE")"
  cmd+="$EXTRA_PARAMS"
  run_task "OG Past - Export Single CSV" "$cmd"
}

export_bulk() {
  echo
  echo "Bulk CSV export"
  STRATEGY="combo"
  SYMBOLS="BTCUSD,DE40,FR40,GOLD,HK50,J225,SP35,UK100,US100,US30,US500"
  TF="H1"
  BARS="500"
  COLS="bartime,symbol,signal,entry_price,sl_price,tp_price,risk_reward,signal_reason"
  START_DATE=""
  END_DATE=""

  prompt_default STRATEGY "Strategy"
  prompt_default SYMBOLS "Symbols csv"
  prompt_default TF "Timeframe"
  prompt_default BARS "Bars"
  prompt_default COLS "Columns"
  read -r -p "Start date optional (YYYY-MM-DD or dd/mm/yyyy): " START_DATE
  read -r -p "End date optional   (YYYY-MM-DD or dd/mm/yyyy): " END_DATE
  collect_params

  local cmd="$PY_CMD -m og_past.export_cli bulk --strategy $(quote "$STRATEGY") --symbols $(quote "$SYMBOLS") --tf $(quote "$TF") --bars $(quote "$BARS") --cols $(quote "$COLS") --output-dir runtime/exports"
  [[ -n "$START_DATE" ]] && cmd+=" --start-date $(quote "$START_DATE")"
  [[ -n "$END_DATE" ]] && cmd+=" --end-date $(quote "$END_DATE")"
  cmd+="$EXTRA_PARAMS"
  run_task "OG Past - Export Bulk CSV" "$cmd"
}

quick_status() {
  run_task "OG - Quick Status" "$PY_CMD -m og_core.ops services; $PY_CMD -m og_live.stream_mechanism.ops health; $PY_CMD -m og_live.pubsub_mechanism.ops health; $PY_CMD -m og_past.ops health --base-url $(quote "$DASHBOARD_URL")"
}

if [[ ! -x "$PY_CMD" ]]; then
  echo "Python virtualenv not found or not executable: $APP_DIR/$PY_CMD"
  echo "Create/install the virtualenv before running this launcher."
  exit 1
fi

main_menu
