#!/usr/bin/env bash

# order_gateway launcher for Ubuntu/Linux: live signal worker control
# (systemd user service) — Redis DB0 candles -> strategy -> Redis DB1.
# Long-running actions open in a new desktop terminal when available.
# On headless/SSH sessions, actions run in the current terminal.

set -u

APP_TITLE="order_gateway Launcher"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_CMD="./.venv/bin/python"

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

  if [[ "${OG_LAUNCH_MODE:-auto}" != "current" ]] && launch_terminal "$title" "$cmd"; then
    return 0
  fi

  clear 2>/dev/null || printf "\033c"
  echo "==================== $title ===================="
  echo
  bash -lc "cd $(quote "$APP_DIR") && $cmd"
  pause
}

# =============================================================================
# Live worker — điều khiển qua systemd user service.
# =============================================================================

SERVICE_NAME="og-signal.service"
UNIT_SRC="$APP_DIR/order_gateway/deploy/og-signal-user.service"
UNIT_DEST="$HOME/.config/systemd/user/og-signal.service"
APP_LOG="order_gateway/logs/og_run.log"

og_signal_sync_unit() {
  # Idempotent: luôn copy lại + daemon-reload để Start không bao giờ chạy
  # unit cũ nếu order_gateway/deploy/og-signal-user.service đã được sửa sau
  # lần cài trước.
  install -D -m 0644 "$UNIT_SRC" "$UNIT_DEST"
  systemctl --user daemon-reload
}

og_signal_status_word() {
  # is-active exits non-zero for every state except "active" (inactive,
  # failed, ... all exit non-zero too), so gate the not-installed fallback
  # on whether the unit is loaded at all, not on is-active's exit code.
  if ! systemctl --user cat "$SERVICE_NAME" >/dev/null 2>&1; then
    echo "not-installed"
    return
  fi
  systemctl --user is-active "$SERVICE_NAME" 2>/dev/null
}

og_signal_start() {
  echo
  echo "CẢNH BÁO: redis.input.process_on_startup=true trong og_config.yaml."
  echo "Lần start này sẽ quét lại toàn bộ snapshot Redis DB0 ngay lập tức và"
  echo "có thể publish signal + gửi Discord NGAY cho bất kỳ signal nào còn"
  echo "hiệu lực tại thời điểm đó."
  echo
  read -r -p "Xác nhận bật live worker service? [y/N]: " confirm
  if [[ "${confirm,,}" != "y" ]]; then
    echo "Đã hủy."
    pause
    return
  fi
  og_signal_sync_unit
  systemctl --user enable --now "$SERVICE_NAME"
  pause
}

og_signal_stop() {
  systemctl --user disable --now "$SERVICE_NAME" 2>&1
  pause
}

og_signal_restart() {
  systemctl --user restart "$SERVICE_NAME" 2>&1
  pause
}

og_signal_status() {
  systemctl --user status "$SERVICE_NAME" --no-pager
  pause
}

# Log ứng dụng CHỈ ra file (order_gateway/src/log.py: đúng 1
# RotatingFileHandler, không có StreamHandler) -> journald gần như trống,
# chỉ còn dòng systemd tự in lúc start/stop và traceback lúc khởi động nếu
# tiến trình chết trước khi logging kịp bật. Vì vậy phải có 2 mục riêng:
# mục dưới đây cho log vận hành thật, mục kế tiếp cho lỗi khởi động.
og_signal_logs() {
  run_task "Application Log ($APP_LOG)" "tail -n 200 -f $(quote "$APP_DIR/$APP_LOG")"
}

og_signal_journal() {
  run_task "systemd/journal ($SERVICE_NAME)" "journalctl --user -u $SERVICE_NAME -n 200 -f"
}

og_signal_foreground() {
  run_task "Live Worker Foreground" "$PY_CMD -m order_gateway.src.live_worker"
}

main_menu() {
  while true; do
    clear 2>/dev/null || printf "\033c"
    cat <<EOF
============================================================
$APP_TITLE — Live worker (Redis DB0 -> strategy -> Redis DB1 -> Discord)
Project: $APP_DIR
Status: $(og_signal_status_word)
============================================================

1. Start Live Worker (enable + auto-restart on crash/reboot)
2. Stop Live Worker (disable, won't auto-start on reboot)
3. Restart Live Worker
4. Show Worker Status
5. Tail Application Log ($APP_LOG)  <-- log vận hành thật
6. Tail systemd/journal (lỗi khởi động, traceback)
7. Run Worker in Foreground (manual, bypasses systemd)
0. Exit

EOF
    if ! read -r -p "Choose: " choice; then
      exit 0
    fi
    case "$choice" in
      1) og_signal_start ;;
      2) og_signal_stop ;;
      3) og_signal_restart ;;
      4) og_signal_status ;;
      5) og_signal_logs ;;
      6) og_signal_journal ;;
      7) og_signal_foreground ;;
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
