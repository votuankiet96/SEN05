#!/usr/bin/env bash

# core_engine launcher for Ubuntu/Linux: live control
# (runner: nến -> cấu phần db0; publisher: db0 -> tín hiệu db1; systemd user service) — candles -> Redis 6380 db0 components -> Redis 6380 db1 signals.
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
# Live — HAI loại tiến trình, mỗi loại chia thành nhiều LÀN, điều khiển qua systemd user service:
#   runner    (de-runner@<làn>.<nguồn nến>)  nến Core AEN / OG8 -> SETUP (db0) và TRIGGER + CONTEXT + TREND (db1) của MỌI symbol x khung của nguồn đó, vào Redis cổng 6380. Làn = tên một chiến lược
#             (chỉ TRIGGER + SETUP của chiến lược đó, vd combo.og8) hoặc 'trend' (chỉ CONTEXT + TREND, vd trend.default); nguồn 'default' = redis.input
#   publisher (de-publisher@<chiến lược>)    db0/db1 -> ghép theo luật nến đóng -> lọc trend/dời, entry -> tín hiệu của mọi ô (symbol, khung) của chiến lược đó vào Redis cổng 6380 db2
# Config quyết định DE chạy gì: mỗi nguồn nến CÓ symbol (redis.input / redis.input_sources) có (1 + số chiến lược) làn runner riêng và mỗi chiến lược (mục strategies:) có một publisher; không có khoá nào
# khai riêng số tiến trình. Các làn độc lập nhau, chỉ trao đổi qua Redis: dừng hay restart một làn không kéo theo làn khác (publisher chờ cấu phần chưa về rồi tính lại khi nó về).
# Một Redis nguồn chết chỉ làm các làn của nguồn đó dừng. Redis đầu ra (cổng 6380) PHẢI bật notify-keyspace-events 'Kh' (publisher nghe db0 và db1), nếu thiếu publisher báo lỗi rõ ràng ngay lúc khởi động.
# Chọn tiến trình (restart, log, foreground): để trống = tất cả; 'runner' / 'publisher' = cả loại; 'trend' = các làn trend; tên chiến lược (vd combo) = các làn runner của nó VÀ publisher của nó (đổi
# tham số chiến lược nào thì restart đúng tên đó); hoặc đúng một làn: combo.og8, trend.default, publisher.combo.
# =============================================================================

UNIT_SRC_DIR="$APP_DIR/core_engine/deploy"
UNIT_DEST_DIR="$HOME/.config/systemd/user"
RUNNER_TEMPLATE="de-runner@.service"
PUBLISHER_TEMPLATE="de-publisher@.service"

# In tên (mỗi dòng một tên) của các instance runner cần chạy theo de_config.yaml: 'default' nếu nguồn mặc định có symbol, rồi từng nguồn nến thêm có symbol. Chỉ đọc TÊN khoá và danh sách symbol, không
de_sources() {
  "$PY_CMD" -c '
import yaml
cfg = yaml.safe_load(open("core_engine/de_config.yaml", encoding="utf-8"))
redis = cfg.get("redis") or {}
if (redis.get("input") or {}).get("symbols"):
    print("default")
for name, block in (redis.get("input_sources") or {}).items():
    if (block or {}).get("symbols"):
        print(str(name).strip().lower())
' 2>/dev/null | sed '/^$/d'
}

# In tên (mỗi dòng một tên) các chiến lược trong de_config.yaml (mục strategies:, cùng khoá với configuration.STRATEGIES): mỗi chiến lược một làn runner mỗi nguồn và một publisher.
de_strategies() {
  "$PY_CMD" -c '
import yaml
cfg = yaml.safe_load(open("core_engine/de_config.yaml", encoding="utf-8"))
for name in (cfg.get("strategies") or {}):
    print(str(name).strip().lower())
' 2>/dev/null | sed '/^$/d'
}

de_sync_units() {
  # Idempotent: luôn copy lại + daemon-reload để Start không bao giờ chạy unit cũ nếu
  # core_engine/deploy/de-runner@.service hay de-publisher@.service đã được sửa sau lần cài trước.
  local unit
  for unit in "$RUNNER_TEMPLATE" "$PUBLISHER_TEMPLATE"; do
    install -D -m 0644 "$UNIT_SRC_DIR/$unit" "$UNIT_DEST_DIR/$unit"
  done
  systemctl --user daemon-reload
}

# Mọi nhãn tiến trình theo THỨ TỰ KHỞI ĐỘNG: runner trước (mỗi nguồn: trend, rồi từng chiến lược; ghi cấu phần), publisher sau (đọc cấu phần). Nhãn runner = <làn>.<nguồn>, publisher = publisher.<chiến lược>.
all_labels() {
  local source strategy
  for source in "${SOURCES[@]}"; do
    echo "trend.${source}"
    for strategy in "${STRATEGIES[@]}"; do echo "${strategy}.${source}"; done
  done
  for strategy in "${STRATEGIES[@]}"; do echo "publisher.${strategy}"; done
}

# Nhãn -> tên unit systemd.
unit_of_label() {
  local label="$1"
  if [[ "$label" == publisher.* ]]; then
    echo "de-publisher@${label#publisher.}.service"
  else
    echo "de-runner@${label}.service"
  fi
}

# Mọi unit theo THỨ TỰ KHỞI ĐỘNG.
all_units() {
  local label
  while IFS= read -r label; do unit_of_label "$label"; done < <(all_labels)
}

# Nhãn -> file log ứng dụng (xem core_engine/src/log.py và configuration._lane_name): runner de_run_runner_<làn>[_<nguồn>].log (nguồn mặc định bị bỏ), publisher de_run_publisher_<chiến lược>.log.
log_of_label() {
  local label="$1" lane source
  if [[ "$label" == publisher.* ]]; then
    echo "$APP_DIR/core_engine/logs/de_run_publisher_${label#publisher.}.log"
    return
  fi
  lane="${label%%.*}"
  source="${label#*.}"
  if [[ "$source" == "default" ]]; then
    echo "$APP_DIR/core_engine/logs/de_run_runner_${lane}.log"
  else
    echo "$APP_DIR/core_engine/logs/de_run_runner_${lane}_${source}.log"
  fi
}

# Hỏi tên (xem chú thích đầu mục) rồi in các NHÃN khớp, mỗi dòng một nhãn, theo thứ tự khởi động. Trả về 1 nếu tên không khớp tiến trình nào.
labels_matching() {
  local answer="$1" label found=1
  while IFS= read -r label; do
    if [[ -z "$answer" || "$label" == "$answer" || "$label" == "$answer".* || "$label" == publisher."$answer" \
          || ( "$answer" == runner && "$label" != publisher.* ) || ( "$answer" == publisher && "$label" == publisher.* ) ]]; then
      echo "$label"
      found=0
    fi
  done < <(all_labels)
  return $found
}

legacy_active_units() {
  # Bản cũ cùng ghi các danh sách db0/db1/db2 nên KHÔNG được chạy cùng lúc với bản mới: de-signal@* / de-trend@* (mỗi chiến lược / khối trend một tiến trình), de-publisher.service (một publisher chung)
  # và de-runner@<nguồn>.service (một runner làm trọn mọi vai trò cho cả nguồn).
  local source
  local -a legacy=('de-signal@*' 'de-trend@*' 'de-publisher.service')
  for source in "${SOURCES[@]}"; do legacy+=("de-runner@${source}.service"); done
  systemctl --user list-units "${legacy[@]}" --state=active --no-legend --plain 2>/dev/null | awk '{print $1}'
}

de_status_word() {
  local active=0 total=0 unit legacy
  while IFS= read -r unit; do
    total=$((total + 1))
    [[ "$(systemctl --user is-active "$unit" 2>/dev/null)" == "active" ]] && active=$((active + 1))
  done < <(all_units)
  echo "${active}/${total} tiến trình đang chạy (${#SOURCES[@]} nguồn nến x (trend + ${#STRATEGIES[@]} chiến lược) runner; ${#STRATEGIES[@]} publisher)"
  legacy="$(legacy_active_units | wc -l)"
  if [[ "$legacy" -gt 0 ]]; then
    echo "(!) ${legacy} tiến trình BẢN CŨ cũng đang chạy (de-signal@*, de-trend@*, de-publisher.service hoặc de-runner@<nguồn>.service)"
  fi
}

# Hỏi tên một tiến trình (xem chú thích đầu mục; để trống = tất cả); in ra danh sách NHÃN được chọn, mỗi dòng một nhãn. Trả về 1 nếu tên không hợp lệ.
choose_labels() {
  local answer
  read -r -p "Tiến trình (trống = tất cả; runner | publisher | trend | ${STRATEGIES[*]} | <làn>.<nguồn> vd ${STRATEGIES[0]}.${SOURCES[0]} | publisher.<chiến lược>): " answer >&2
  answer="${answer,,}"
  if labels_matching "$answer"; then
    return 0
  fi
  echo "Không có tiến trình '$answer'." >&2
  return 1
}

de_start() {
  if [[ -n "$(legacy_active_units)" ]]; then
    echo
    echo "Các tiến trình BẢN CŨ (de-signal@*, de-trend@*, de-publisher.service, de-runner@<nguồn>.service) đang chạy."
    echo "Chọn 2 (Stop) hoặc dừng chúng trước: bản cũ và bản mới ghi cùng các danh sách db0/db1/db2 và cùng tên kênh L_CHANNEL_*."
    pause
    return
  fi
  echo
  echo "CẢNH BÁO: redis.input.process_on_startup=true trong de_config.yaml."
  echo "Mỗi tiến trình quét lại toàn bộ phần việc của nó ngay lập tức: runner ghi lại cấu phần từ snapshot nến;"
  echo "publisher có thể publish NGAY cho bất kỳ signal nào còn hiệu lực tại thời điểm đó"
  echo "(kể cả các kênh mà OE đang nghe). Redis cổng 6380 phải bật notify-keyspace-events 'Kh'."
  echo
  read -r -p "Xác nhận bật $(all_units | wc -l) tiến trình (runner trước, publisher sau)? [y/N]: " confirm
  if [[ "${confirm,,}" != "y" ]]; then
    echo "Đã hủy."
    pause
    return
  fi
  de_sync_units
  local label
  while IFS= read -r label; do
    [[ "$label" == publisher.* ]] && continue
    systemctl --user enable --now "$(unit_of_label "$label")"
  done < <(all_labels)
  sleep 5   # để các làn runner kịp ghi cấu phần đầu tiên
  while IFS= read -r label; do
    [[ "$label" == publisher.* ]] && systemctl --user enable --now "$(unit_of_label "$label")"
  done < <(all_labels)
  pause
}

de_stop() {
  local unit
  while IFS= read -r unit; do   # ngược thứ tự khởi động: publisher trước, runner sau
    systemctl --user disable --now "$unit" 2>&1
  done < <(all_units | tac)
  pause
}

de_restart() {
  local labels label
  labels="$(choose_labels)" || { pause; return; }
  while IFS= read -r label; do
    systemctl --user restart "$(unit_of_label "$label")" 2>&1
  done <<< "$labels"
  pause
}

de_status() {
  systemctl --user status 'de-runner@*' 'de-publisher@*' --no-pager
  pause
}

# Log ứng dụng CHỈ ra file (core_engine/src/log.py: mỗi tiến trình đúng 1 RotatingFileHandler, không có StreamHandler) ->
# journald gần như trống, chỉ còn dòng systemd tự in lúc start/stop và traceback lúc khởi động nếu tiến trình chết trước khi
# logging kịp bật. Vì vậy phải có 2 mục riêng: mục dưới đây cho log vận hành thật, mục kế tiếp cho lỗi khởi động.
de_logs() {
  local labels files="" label
  labels="$(choose_labels)" || { pause; return; }
  while IFS= read -r label; do
    files+=" $(quote "$(log_of_label "$label")")"
  done <<< "$labels"
  run_task "Application Log" "tail -n 100 -f${files}"
}

de_journal() {
  run_task "systemd/journal (de-runner@*, de-publisher@*)" "journalctl --user -u 'de-runner@*' -u 'de-publisher@*' -n 200 -f"
}

# Chạy ĐÚNG MỘT tiến trình ở foreground (không có chế độ "một tiến trình chạy cả runner lẫn publisher").
de_foreground() {
  local labels label
  labels="$(choose_labels)" || { pause; return; }
  if [[ "$(wc -l <<< "$labels")" -gt 1 ]]; then
    echo "Foreground chỉ chạy MỘT tiến trình: nhập đúng một làn (vd ${STRATEGIES[0]}.${SOURCES[0]} hoặc publisher.${STRATEGIES[0]})."
    pause
    return
  fi
  label="$labels"
  if [[ "$label" == publisher.* ]]; then
    run_task "Publisher Foreground (${label#publisher.})" "$PY_CMD -m core_engine.src.main --publisher --lane ${label#publisher.}"
    return
  fi
  run_task "Runner Foreground ($label)" "$PY_CMD -m core_engine.src.main --runner --lane $label"
}

main_menu() {
  while true; do
    clear 2>/dev/null || printf "\033c"
    cat <<MENU
============================================================
$APP_TITLE — Live (nến -> runner theo làn -> Redis db0/db1 cấu phần -> publisher theo chiến lược -> Redis db2 tín hiệu)
Project: $APP_DIR
Status: $(de_status_word)
============================================================

1. Start (runner trước, publisher sau; enable + auto-restart)
2. Stop (disable, won't auto-start on reboot)
3. Restart tiến trình (theo chiến lược, làn, loại hoặc tất cả)
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

mapfile -t SOURCES < <(de_sources)
if [[ ${#SOURCES[@]} -eq 0 ]]; then
  echo "Không có nguồn nến nào có symbol trong redis.input / redis.input_sources của core_engine/de_config.yaml."
  exit 1
fi

mapfile -t STRATEGIES < <(de_strategies)
if [[ ${#STRATEGIES[@]} -eq 0 ]]; then
  echo "Không có chiến lược nào trong mục strategies: của core_engine/de_config.yaml."
  exit 1
fi

main_menu
