#!/usr/bin/env bash

# core_python launcher for Ubuntu/Linux.
# Long-running actions open in a new desktop terminal when available.
# On headless/SSH sessions, actions run in the current terminal.

set -u

APP_TITLE="core_python Launcher"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD_URL="${DASHBOARD_URL:-http://127.0.0.1:8516}"
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

# Terminals launched without inheriting the desktop session (VS Code's
# integrated terminal, SSH to the same machine, etc.) have no DISPLAY /
# WAYLAND_DISPLAY, so has_desktop() would wrongly report "no desktop" even
# though one is actively logged in. The desktop session publishes these into
# the systemd --user manager's environment (via dbus-update-activation-
# environment) precisely so on-demand processes can recover them; read them
# from there before falling back to the text-only path.
detect_desktop_env() {
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

open_dashboard() {
  echo
  echo "Dashboard: $DASHBOARD_URL"
  if has_desktop && command -v xdg-open >/dev/null 2>&1; then
    echo "Opening in the default browser (see /tmp/og_launcher_xdg-open.log if nothing appears)..."
    xdg-open "$DASHBOARD_URL" >/tmp/og_launcher_xdg-open.log 2>&1 &
    disown
  else
    echo "No desktop session detected from this terminal — open the URL above manually."
  fi
  pause
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

1. Open Dashboard
2. Export Signal CSV
0. Exit

EOF
    if ! read -r -p "Choose: " choice; then
      exit 0
    fi
    case "$choice" in
      1) open_dashboard ;;
      2) run_task "Export Signal CSV" "$PY_CMD -m core_python.export_cli wizard --output-dir runtime/exports" ;;
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
