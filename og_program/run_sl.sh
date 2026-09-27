#!/usr/bin/env bash

# strategy_lab launcher for Ubuntu/Linux: SQL backtest (CSV export, dashboard)
# plus the past-signal worker control (systemd user service) for the
# "backtest cấp 2" Redis DB2 flow (L_PastSignal, triggered by dp:events:backfill).
# Long-running actions open in a new desktop terminal when available.
# On headless/SSH sessions, actions run in the current terminal.

set -u

APP_TITLE="Strategy Lab Launcher"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_CMD="./.venv/bin/python"
DASHBOARD_URL="${DASHBOARD_URL:-http://127.0.0.1:8517}"

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

cd "$APP_DIR" || exit 1

quote() {
  printf "%q" "$1"
}

pause() {
  echo
  read -r -p "Press Enter to continue..."
}

has_desktop() {
  [[ -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ]]
}

# Máy này có thể có NHIỀU display X cùng lúc — vd display :0 cục bộ (không
# ai ngồi trước) và display riêng của phiên xrdp (người dùng thật sự đang
# nhìn thấy qua RDP). Ưu tiên tìm display xrdp trước, vì đó mới là màn hình
# người dùng tương tác — nếu tin nhầm display :0 (như systemd --user hay
# báo về), lệnh mở trình duyệt sẽ "thành công" nhưng vô hình với người dùng.
detect_xrdp_display() {
  local line display
  line="$(ps -eo args 2>/dev/null | grep -m1 '[X]org .*xrdp/xorg\.conf')" || return 1
  display="$(printf '%s\n' "$line" | grep -oE ':[0-9]+' | head -1)"
  [[ -n "$display" ]] || return 1
  export DISPLAY="$display"
  [[ -f "$HOME/.Xauthority" ]] && export XAUTHORITY="$HOME/.Xauthority"
  return 0
}

# Terminals launched without inheriting the desktop session (VS Code's
# integrated terminal, SSH to the same machine, etc.) have no DISPLAY /
# WAYLAND_DISPLAY, so has_desktop() would wrongly report "no desktop" even
# though one is actively logged in. The desktop session publishes these into
# the systemd --user manager's environment (via dbus-update-activation-
# environment) precisely so on-demand processes can recover them; read them
# from there before falling back to the text-only path.
detect_desktop_env() {
  detect_xrdp_display && return 0
  has_desktop && return 0
  command -v systemctl >/dev/null 2>&1 || return 1

  local env_output
  env_output="$(systemctl --user show-environment 2>/dev/null)" || return 1

  local display wayland_display xauthority runtime_dir
  display="$(printf '%s\n' "$env_output" | sed -n 's/^DISPLAY=//p')"
  wayland_display="$(printf '%s\n' "$env_output" | sed -n 's/^WAYLAND_DISPLAY=//p')"
  xauthority="$(printf '%s\n' "$env_output" | sed -n 's/^XAUTHORITY=//p')"
  runtime_dir="$(printf '%s\n' "$env_output" | sed -n 's/^XDG_RUNTIME_DIR=//p')"

  [[ -n "$display" || -n "$wayland_display" ]] || return 1
  export DISPLAY="${display:-}"
  export WAYLAND_DISPLAY="${wayland_display:-}"
  [[ -n "$xauthority" ]] && export XAUTHORITY="$xauthority"
  [[ -n "$runtime_dir" ]] && export XDG_RUNTIME_DIR="$runtime_dir"
  return 0
}

detect_desktop_env || true

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

  if [[ "${SL_LAUNCH_MODE:-auto}" != "current" ]] && launch_terminal "$title" "$cmd"; then
    return 0
  fi

  clear 2>/dev/null || printf "\033c"
  echo "==================== $title ===================="
  echo
  bash -lc "cd $(quote "$APP_DIR") && $cmd"
  pause
}

prompt_run_args() {
  # Trả kết quả qua biến toàn cục PROMPT_STRATEGY/PROMPT_SYMBOL/PROMPT_TF/
  # PROMPT_FROM/PROMPT_TO để tránh subshell (không dùng command substitution)
  # mất tác dụng của `read`.
  # Symbol để trống = không truyền cờ, export_cli.py tự lấy DEFAULT_SYMBOL
  # thật từ sl_config.yaml — không hardcode mã ở đây để tránh lệch config.
  # From/To để trống = xuất toàn bộ lịch sử có trong DP6 (từ bar đầu tiên
  # tới bar gần nhất).
  read -r -p "Strategy (combo/ma_cross/ema_cross) [combo]: " PROMPT_STRATEGY
  PROMPT_STRATEGY="${PROMPT_STRATEGY:-combo}"
  read -r -p "Symbol (blank = default in sl_config.yaml): " PROMPT_SYMBOL
  read -r -p "Timeframe (blank = strategy default): " PROMPT_TF
  read -r -p "From date (blank = earliest bar, e.g. 2024-01-01): " PROMPT_FROM
  read -r -p "To date (blank = latest bar): " PROMPT_TO
}

export_signal_csv() {
  local cmd
  prompt_run_args
  # Không truyền --output-dir: để export_cli.py tự dùng default của chính
  # nó (strategy_lab/runtime/exports) — 1 giá trị chỉ tồn tại ở đúng 1 nơi,
  # launcher không giữ bản sao riêng dễ lệch.
  cmd="$PY_CMD -m strategy_lab.src.og_signal.export_cli --strategy $(quote "$PROMPT_STRATEGY")"
  if [[ -n "$PROMPT_SYMBOL" ]]; then
    cmd="$cmd --symbol $(quote "$PROMPT_SYMBOL")"
  fi
  if [[ -n "$PROMPT_TF" ]]; then
    cmd="$cmd --tf $(quote "$PROMPT_TF")"
  fi
  if [[ -n "$PROMPT_FROM" ]]; then
    cmd="$cmd --from $(quote "$PROMPT_FROM")"
  fi
  if [[ -n "$PROMPT_TO" ]]; then
    cmd="$cmd --to $(quote "$PROMPT_TO")"
  fi
  run_task "Export Signal CSV" "$cmd"
}

dashboard_running() {
  command -v curl >/dev/null 2>&1 && curl -s -o /dev/null -m 1 "$DASHBOARD_URL/health"
}

open_dashboard() {
  if dashboard_running; then
    echo "Dashboard đã chạy sẵn tại $DASHBOARD_URL"
  else
    echo "Đang khởi động dashboard tại $DASHBOARD_URL ..."
    local cmd="$PY_CMD -m strategy_lab.src.signal_display.server"
    if [[ "${SL_LAUNCH_MODE:-auto}" != "current" ]] && launch_terminal "strategy_lab Dashboard" "$cmd"; then
      :
    else
      nohup bash -lc "cd $(quote "$APP_DIR") && $cmd" >/tmp/strategy_lab_dashboard.log 2>&1 &
      disown
    fi
    sleep 2
  fi
  # Dùng profile Chrome riêng cho dashboard, KHÔNG dùng xdg-open/profile mặc
  # định — nếu máy đã có sẵn 1 Chrome khác đang chạy (vd order_gateway
  # dashboard trên cổng 8516), cơ chế single-instance của Chrome sẽ âm thầm
  # chuyển tab mới vào cửa sổ đó thay vì mở đúng display hiện tại, dù DISPLAY
  # đã set đúng. Profile riêng theo từng hệ thống để 2 dashboard mở song song
  # được, không tranh cửa sổ nhau.
  if has_desktop && command -v google-chrome >/dev/null 2>&1; then
    google-chrome --user-data-dir="$HOME/.cache/strategy-lab-dashboard-chrome" "$DASHBOARD_URL" \
      >/tmp/strategy_lab_dashboard_browser.log 2>&1 &
    disown
  elif has_desktop && command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$DASHBOARD_URL" >/tmp/sl_launcher_xdg-open.log 2>&1 &
    disown
  else
    echo "Không phát hiện desktop — mở tay: $DASHBOARD_URL"
  fi
  pause
}

# =============================================================================
# strategy_lab — điều khiển past-signal worker (redis_io/worker.py) chạy qua
# systemd user service. Worker subscribe dp:events:backfill, mỗi lần DP báo
# backfill SQL xong 1 (symbol, timeframe) thì tính lại toàn bộ lịch sử signal
# và ghi đè lên Redis DB2 (L_PastSignal) — xem CLAUDE.md +
# memory project_strategy_lab_redesign.
# =============================================================================

SERVICE_NAME="strategy-lab-worker.service"
UNIT_SRC="$APP_DIR/strategy_lab/deploy/strategy-lab-worker.service"
UNIT_DEST="$HOME/.config/systemd/user/strategy-lab-worker.service"

sl_worker_sync_unit() {
  # Idempotent: luôn copy lại + daemon-reload để Start không bao giờ chạy
  # unit cũ nếu strategy_lab/deploy/strategy-lab-worker.service đã được sửa
  # sau lần cài trước.
  install -D -m 0644 "$UNIT_SRC" "$UNIT_DEST"
  systemctl --user daemon-reload
}

sl_worker_status_word() {
  # is-active exits non-zero for every state except "active" (inactive,
  # failed, ... all exit non-zero too), so gate the not-installed fallback
  # on whether the unit is loaded at all, not on is-active's exit code.
  if ! systemctl --user cat "$SERVICE_NAME" >/dev/null 2>&1; then
    echo "not-installed"
    return
  fi
  systemctl --user is-active "$SERVICE_NAME" 2>/dev/null
}

sl_worker_start() {
  # Khác og_signal (order_gateway) cũ: worker này KHÔNG tự quét gì lúc khởi
  # động (không có process_on_startup) — chỉ subscribe dp:events:backfill rồi
  # ngồi chờ trigger, nên start an toàn, không cần xác nhận thêm.
  sl_worker_sync_unit
  systemctl --user enable --now "$SERVICE_NAME"
  pause
}

sl_worker_stop() {
  systemctl --user disable --now "$SERVICE_NAME" 2>&1
  pause
}

sl_worker_restart() {
  systemctl --user restart "$SERVICE_NAME" 2>&1
  pause
}

sl_worker_status() {
  systemctl --user status "$SERVICE_NAME" --no-pager
  pause
}

sl_worker_logs() {
  run_task "strategy_lab Worker Live Logs" "journalctl --user -u $SERVICE_NAME -f"
}

sl_worker_foreground() {
  run_task "strategy_lab Worker Foreground" "$PY_CMD -m strategy_lab.src.og_signal.redis_io.worker"
}

sl_worker_menu() {
  while true; do
    clear 2>/dev/null || printf "\033c"
    cat <<EOF
============================================================
strategy_lab — Past-signal worker (SQL backfill trigger -> strategy -> Redis DB2 L_PastSignal)
Status: $(sl_worker_status_word)
============================================================

1. Start Worker (enable + auto-restart on crash/reboot)
2. Stop Worker (disable, won't auto-start on reboot)
3. Restart Worker
4. Show Worker Status
5. Tail Live Logs (journalctl -f)
6. Run Worker in Foreground (manual, bypasses systemd)
0. Back

EOF
    if ! read -r -p "Choose: " choice; then
      return
    fi
    case "$choice" in
      1) sl_worker_start ;;
      2) sl_worker_stop ;;
      3) sl_worker_restart ;;
      4) sl_worker_status ;;
      5) sl_worker_logs ;;
      6) sl_worker_foreground ;;
      0) return ;;
    esac
  done
}

main_menu() {
  while true; do
    clear 2>/dev/null || printf "\033c"
    cat <<EOF
============================================================
$APP_TITLE
Backend: Ubuntu/Linux local
Project: $APP_DIR
============================================================

1. Export Signal CSV
2. Open Dashboard
3. Manage Past-Signal Worker
0. Exit

EOF
    if ! read -r -p "Choose: " choice; then
      exit 0
    fi
    case "$choice" in
      1) export_signal_csv ;;
      2) open_dashboard ;;
      3) sl_worker_menu ;;
      0) exit 0 ;;
    esac
  done
}

if [[ ! -x "$PY_CMD" ]]; then
  echo "Python virtualenv not found or not executable: $APP_DIR/$PY_CMD"
  echo "Create/install the virtualenv before running this launcher."
  exit 1
fi

main_menu
