#!/usr/bin/env bash

# core_engine launcher for Ubuntu/Linux: live worker control
# (mỗi chiến lược và mỗi khối trend một tiến trình, systemd user service) — Redis DB0 candles -> trend + strategy -> Redis DB1.
# Long-running actions open in a new desktop terminal when available.
# On headless/SSH sessions, actions run in the current terminal.

set -u

APP_TITLE="core_engine Launcher"
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

  if [[ "${DE_LAUNCH_MODE:-auto}" != "current" ]] && launch_terminal "$title" "$cmd"; then
    return 0
  fi

  clear 2>/dev/null || printf "\033c"
  echo "==================== $title ===================="
  echo
  bash -lc "cd $(quote "$APP_DIR") && $cmd"
  pause
}

# =============================================================================
# Live worker — MỖI CHIẾN LƯỢC MỘT TIẾN TRÌNH và MỖI KHỐI TREND MỘT TIẾN TRÌNH, điều khiển qua systemd user service
# (unit mẫu de-signal@<chiến lược> và de-trend@<khối trend>). Config quyết định DE chạy gì:
#   chiến lược = live.enabled_strategies; khối trend = các khoá của redis.trend_outputs (chú thích hết = chỉ chạy raw, không có tiến trình trend).
# Mỗi nguồn nến THÊM khai ở redis.input_sources.<nguồn> (vd og8) có một NHÓM tiến trình riêng, tên <chiến lược>.<nguồn> / <khối>.<nguồn> (unit de-signal@combo.og8): nguồn đó mất kết nối
# chỉ làm nhóm của nó dừng. Các tiến trình độc lập nhau, chỉ trao đổi qua Redis: dừng hay restart một tiến trình không kéo theo tiến trình khác.
# =============================================================================

UNIT_SRC_DIR="$APP_DIR/core_engine/deploy"
UNIT_DEST_DIR="$HOME/.config/systemd/user"
LEGACY_SERVICE="de-signal.service"   # bản cũ: MỘT tiến trình chạy mọi chiến lược; không được chạy cùng lúc với các tiến trình mới

# In tên (mỗi dòng một tên) của "strategy" hoặc "trend" theo de_config.yaml: nhóm mặc định trước, rồi từng nhóm của nguồn nến thêm (<tên>.<nguồn>). Chỉ đọc TÊN khoá, không đọc mật khẩu.
de_config_names() {
  "$PY_CMD" -c '
import sys, yaml
cfg = yaml.safe_load(open("core_engine/de_config.yaml", encoding="utf-8"))
redis = cfg.get("redis") or {}
names = cfg["live"]["enabled_strategies"] if sys.argv[1] == "strategy" else list(redis.get("trend_outputs") or {})
sources = [str(source).strip().lower() for source in (redis.get("input_sources") or {})]
print(*names, *(f"{name}.{source}" for source in sources for name in names), sep="\n")
' "$1" 2>/dev/null | sed '/^$/d'
}

de_sync_units() {
  # Idempotent: luôn copy lại + daemon-reload để Start không bao giờ chạy unit cũ nếu
  # core_engine/deploy/de-signal@.service hay de-trend@.service đã được sửa sau lần cài trước.
  local unit
  for unit in de-signal@.service de-trend@.service; do
    install -D -m 0644 "$UNIT_SRC_DIR/$unit" "$UNIT_DEST_DIR/$unit"
  done
  systemctl --user daemon-reload
}

# Mọi tiến trình theo THỨ TỰ KHỞI ĐỘNG: tiến trình trend trước (ghi dòng thời gian trend), tiến trình chiến lược sau (đọc dòng thời gian đó).
all_units() {
  local name
  for name in "${TRENDS[@]}"; do echo "de-trend@${name}.service"; done
  for name in "${LANES[@]}"; do echo "de-signal@${name}.service"; done
}

# Tên (chiến lược hoặc khối trend) -> tên unit. Trả về 1 nếu tên không có trong config.
unit_of() {
  local name="$1" item
  for item in "${TRENDS[@]}"; do
    [[ "$item" == "$name" ]] && { echo "de-trend@${name}.service"; return 0; }
  done
  for item in "${LANES[@]}"; do
    [[ "$item" == "$name" ]] && { echo "de-signal@${name}.service"; return 0; }
  done
  return 1
}

# Tên unit -> file log ứng dụng (xem core_engine/src/log.py): chiến lược de_run_<tên>.log, khối trend de_run_trend_<tên>.log; nhóm nguồn nến thêm đổi dấu chấm thành gạch dưới (combo.og8 -> de_run_combo_og8.log).
log_of() {
  local unit="$1" name
  name="${unit#*@}"
  name="${name%.service}"
  name="${name//./_}"
  if [[ "$unit" == de-trend@* ]]; then
    echo "$APP_DIR/core_engine/logs/de_run_trend_${name}.log"
  else
    echo "$APP_DIR/core_engine/logs/de_run_${name}.log"
  fi
}

legacy_active() {
  systemctl --user is-active --quiet "$LEGACY_SERVICE" 2>/dev/null
}

de_status_word() {
  local active=0 total=0 unit
  while IFS= read -r unit; do
    total=$((total + 1))
    [[ "$(systemctl --user is-active "$unit" 2>/dev/null)" == "active" ]] && active=$((active + 1))
  done < <(all_units)
  echo "${active}/${total} tiến trình đang chạy (trend: ${TRENDS[*]:-không có}; chiến lược: ${LANES[*]})"
  if legacy_active; then
    echo "(!) $LEGACY_SERVICE (bản cũ, một tiến trình) cũng đang chạy"
  fi
}

# Hỏi tên một tiến trình (chiến lược hoặc khối trend; để trống = tất cả); in ra danh sách unit được chọn, mỗi dòng một unit. Trả về 1 nếu tên không hợp lệ.
choose_units() {
  local answer unit
  read -r -p "Tiến trình (${TRENDS[*]} ${LANES[*]}; để trống = tất cả): " answer >&2
  answer="${answer,,}"
  if [[ -z "$answer" ]]; then
    all_units
    return 0
  fi
  if unit="$(unit_of "$answer")"; then
    echo "$unit"
    return 0
  fi
  echo "Không có tiến trình '$answer' (chiến lược trong live.enabled_strategies, khối trend trong redis.trend_outputs)." >&2
  return 1
}

de_start() {
  if legacy_active; then
    echo
    echo "$LEGACY_SERVICE (bản cũ, một tiến trình chạy mọi chiến lược) đang chạy."
    echo "Chọn 2 (Stop) để dừng nó trước, kẻo hai chế độ cùng xử lý một nến."
    pause
    return
  fi
  echo
  echo "CẢNH BÁO: redis.input.process_on_startup=true trong de_config.yaml."
  echo "Mỗi tiến trình sẽ quét lại toàn bộ snapshot Redis DB0 ngay lập tức:"
  echo "tiến trình chiến lược có thể publish signal NGAY cho bất kỳ signal nào còn"
  echo "hiệu lực tại thời điểm đó (kể cả các kênh trend mà OE đang nghe)."
  echo
  read -r -p "Xác nhận bật các tiến trình (trend: ${TRENDS[*]:-không có}; chiến lược: ${LANES[*]})? [y/N]: " confirm
  if [[ "${confirm,,}" != "y" ]]; then
    echo "Đã hủy."
    pause
    return
  fi
  de_sync_units
  local name
  for name in "${TRENDS[@]}"; do
    systemctl --user enable --now "de-trend@${name}.service"
  done
  [[ ${#TRENDS[@]} -gt 0 ]] && sleep 5   # để các tiến trình trend kịp ghi dòng thời gian đầu tiên
  for name in "${LANES[@]}"; do
    systemctl --user enable --now "de-signal@${name}.service"
  done
  pause
}

de_stop() {
  local unit
  while IFS= read -r unit; do   # ngược thứ tự khởi động: chiến lược trước, trend sau
    systemctl --user disable --now "$unit" 2>&1
  done < <(all_units | tac)
  if legacy_active; then
    systemctl --user disable --now "$LEGACY_SERVICE" 2>&1
  fi
  pause
}

de_restart() {
  local units unit
  units="$(choose_units)" || { pause; return; }
  while IFS= read -r unit; do
    systemctl --user restart "$unit" 2>&1
  done <<< "$units"
  pause
}

de_status() {
  systemctl --user status 'de-signal@*' 'de-trend@*' --no-pager
  pause
}

# Log ứng dụng CHỈ ra file (core_engine/src/log.py: mỗi tiến trình đúng 1 RotatingFileHandler, không có StreamHandler) ->
# journald gần như trống, chỉ còn dòng systemd tự in lúc start/stop và traceback lúc khởi động nếu tiến trình chết trước khi
# logging kịp bật. Vì vậy phải có 2 mục riêng: mục dưới đây cho log vận hành thật, mục kế tiếp cho lỗi khởi động.
de_logs() {
  local units files="" unit
  units="$(choose_units)" || { pause; return; }
  while IFS= read -r unit; do
    files+=" $(quote "$(log_of "$unit")")"
  done <<< "$units"
  run_task "Application Log" "tail -n 100 -f${files}"
}

de_journal() {
  run_task "systemd/journal (de-signal@*, de-trend@*)" "journalctl --user -u 'de-signal@*' -u 'de-trend@*' -n 200 -f"
}

# Chạy ĐÚNG MỘT tiến trình ở foreground (không có chế độ "một tiến trình chạy mọi thứ").
de_foreground() {
  local units unit name
  units="$(choose_units)" || { pause; return; }
  if [[ "$(wc -l <<< "$units")" -gt 1 ]]; then
    echo "Foreground chỉ chạy MỘT tiến trình: nhập đúng một tên."
    pause
    return
  fi
  unit="$units"
  name="${unit#*@}"
  name="${name%.service}"
  if [[ "$unit" == de-trend@* ]]; then
    run_task "Trend Foreground ($name)" "$PY_CMD -m core_engine.src.main --trend $name"
  else
    run_task "Live Worker Foreground ($name)" "$PY_CMD -m core_engine.src.main --strategy $name"
  fi
}

main_menu() {
  while true; do
    clear 2>/dev/null || printf "\033c"
    cat <<MENU
============================================================
$APP_TITLE — Live worker (Redis DB0 -> trend + strategy -> Redis DB1)
Project: $APP_DIR
Status: $(de_status_word)
============================================================

1. Start (trend trước, chiến lược sau; enable + auto-restart)
2. Stop (disable, won't auto-start on reboot)
3. Restart một tiến trình (chiến lược hoặc khối trend; trống = tất cả)
4. Show Status
5. Tail Application Log (core_engine/logs/de_run_*.log)  <-- log vận hành thật
6. Tail systemd/journal (lỗi khởi động, traceback)
7. Run ONE process in Foreground (manual, bypasses systemd)
0. Exit

MENU
    if ! read -r -p "Choose: " choice; then
      exit 0
    fi
    case "$choice" in
      1) de_start ;;
      2) de_stop ;;
      3) de_restart ;;
      4) de_status ;;
      5) de_logs ;;
      6) de_journal ;;
      7) de_foreground ;;
      0) exit 0 ;;
    esac
  done
}

if [[ ! -x "$PY_CMD" ]]; then
  echo "Python virtualenv not found or not executable: $APP_DIR/$PY_CMD"
  echo "Create/install the virtualenv before running this launcher."
  exit 1
fi

mapfile -t LANES < <(de_config_names strategy)
mapfile -t TRENDS < <(de_config_names trend)
if [[ ${#LANES[@]} -eq 0 ]]; then
  echo "Không đọc được live.enabled_strategies từ core_engine/de_config.yaml."
  exit 1
fi
for name in "${TRENDS[@]}"; do
  for lane in "${LANES[@]}"; do
    if [[ "$name" == "$lane" ]]; then
      echo "Tên '$name' vừa là chiến lược vừa là khối trend: đổi tên để không nhầm tiến trình."
      exit 1
    fi
  done
done

main_menu
