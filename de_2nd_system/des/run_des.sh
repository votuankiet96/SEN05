#!/usr/bin/env bash

# DES dashboard launcher for Ubuntu/Linux (xrdp desktop): click it and the DES chart opens in the browser.
#
# What it does, in order:
#   1. makes sure the chart server (python -m des.src.chart) is running on 127.0.0.1:8518, starting it in the background if it is not;
#   2. opens the dashboard in Chrome on the desktop you are actually looking at (the xrdp display).
# It needs no terminal, so a double-click in the file manager works. Problems are shown in a dialog (or printed when run from a terminal)
# and the chart server log is des/logs/chart.log.
# It does NOT start or stop the DES processes (systemd user services des-runner@* and des-publisher@*): the chart only reads Redis.
# Environment: DES_CHART_PORT (default 8518).

set -u

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY_CMD="$APP_DIR/.venv/bin/python"
PORT="${DES_CHART_PORT:-8518}"
URL="http://127.0.0.1:${PORT}/"
LOG_FILE="$APP_DIR/des/logs/chart.log"
BROWSER_PROFILE="$HOME/.cache/des-chart-chrome"

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

has_desktop() {
  [[ -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ]]
}

# This machine can have SEVERAL X displays at once: the local display :0 (nobody sits in front of it) and the display of the xrdp session (the one the
# user really sees over RDP). Look for the xrdp display first: if :0 is trusted by mistake, the browser "starts" but nothing appears on the user's screen.
detect_xrdp_display() {
  local line display
  line="$(ps -eo args 2>/dev/null | grep -m1 '[X]org .*xrdp/xorg\.conf')" || return 1
  display="$(printf '%s\n' "$line" | grep -oE ':[0-9]+' | head -1)"
  [[ -n "$display" ]] || return 1
  export DISPLAY="$display"
  [[ -f "$HOME/.Xauthority" ]] && export XAUTHORITY="$HOME/.Xauthority"
  return 0
}

# A terminal started without the desktop environment (VS Code terminal, SSH to this machine) has no DISPLAY. The desktop session publishes it into the
# systemd --user manager environment, so read it from there before giving up.
detect_desktop_env() {
  detect_xrdp_display && return 0
  has_desktop && return 0
  command -v systemctl >/dev/null 2>&1 || return 1

  local env_output display wayland_display xauthority
  env_output="$(systemctl --user show-environment 2>/dev/null)" || return 1
  display="$(printf '%s\n' "$env_output" | sed -n 's/^DISPLAY=//p')"
  wayland_display="$(printf '%s\n' "$env_output" | sed -n 's/^WAYLAND_DISPLAY=//p')"
  xauthority="$(printf '%s\n' "$env_output" | sed -n 's/^XAUTHORITY=//p')"

  [[ -n "$display" || -n "$wayland_display" ]] || return 1
  export DISPLAY="${display:-}"
  export WAYLAND_DISPLAY="${wayland_display:-}"
  [[ -n "$xauthority" ]] && export XAUTHORITY="$xauthority"
  return 0
}

# From a terminal: print the problem. From a click (no terminal): print it AND show a dialog that stays until dismissed, because nobody sees the output.
fail() {
  echo "ERROR: $1" >&2
  if [[ ! -t 2 ]] && has_desktop; then
    if command -v xmessage >/dev/null 2>&1; then
      xmessage -center -buttons OK "DES dashboard: $1" >/dev/null 2>&1 &
    elif command -v notify-send >/dev/null 2>&1; then
      notify-send --urgency=critical "DES dashboard" "$1" >/dev/null 2>&1
    fi
  fi
  exit 1
}

chart_is_up() {
  curl -fsS --max-time 2 "${URL}health" >/dev/null 2>&1
}

# Start the chart server detached from whatever started this script (file manager, terminal), then wait until it answers.
start_chart() {
  mkdir -p "$APP_DIR/des/logs"
  (cd "$APP_DIR" && PYTHONDONTWRITEBYTECODE=1 setsid nohup "$PY_CMD" -m des.src.chart --port "$PORT" >> "$LOG_FILE" 2>&1 &)
  local attempt
  for attempt in $(seq 1 50); do
    chart_is_up && return 0
    sleep 0.2
  done
  return 1
}

# Chrome with its own profile: without --user-data-dir, Chrome hands the URL to a Chrome that is already running on ANOTHER display and nothing appears here.
# --disable-gpu: the xrdp session has no GPU, so Chrome would otherwise waste time on a GPU process that cannot start.
open_dashboard() {
  local browser
  for browser in google-chrome-stable google-chrome chromium chromium-browser; do
    if command -v "$browser" >/dev/null 2>&1; then
      setsid nohup "$browser" --user-data-dir="$BROWSER_PROFILE" --no-first-run --no-default-browser-check --disable-gpu --new-window "$URL" >/dev/null 2>&1 &
      return 0
    fi
  done
  if command -v xdg-open >/dev/null 2>&1; then
    setsid nohup xdg-open "$URL" >/dev/null 2>&1 &
    return 0
  fi
  return 1
}

# Find the display FIRST: the error dialog and the browser both need it.
detect_desktop_env || true

[[ -x "$PY_CMD" ]] || fail "Python virtualenv not found or not executable: $PY_CMD"
command -v curl >/dev/null 2>&1 || fail "curl is required to check that the chart server is up."

if chart_is_up; then
  echo "Chart server already running at $URL"
else
  echo "Starting the chart server at $URL ..."
  start_chart || fail "The chart server did not start on port $PORT. Is the port in use? See $LOG_FILE"
fi

if has_desktop; then
  open_dashboard || fail "No browser found (looked for Chrome, Chromium and xdg-open). Open $URL by hand."
  echo "Dashboard opened: $URL"
else
  echo "No desktop session found, so no browser was opened. Open $URL in a browser."
fi
