#!/usr/bin/env bash
#
# start_motor_driver.sh
#
# Starts the rig host: motor_driver.py, which opens the Uno's serial port,
# serves the control page (dashboard.html), and -- with --mqtt-host -- listens
# for machinery-protection trips.
#
# This replaces the old "serve the folder with python3 -m http.server" step.
# The control page needs somewhere to ask which motors are installed and
# whether one has been tripped, and a static file server has no answer; the
# rig host does, so it serves the page itself.
#
# Usage:
#   ./start_motor_driver.sh
#   ./start_motor_driver.sh --mqtt-host uno-q.local
#   ./start_motor_driver.sh --port /dev/ttyACM0 --http-port 8001 --motors 1,2
#
# The broker defaults to epm-base.local (the base station's LAN hostname),
# port 1883. So the no-argument form is the normal one. --mqtt-host '' disables
# the broker.
#
# By default the CONTROL PAGE holds the Uno's serial port over Web Serial and
# this process never opens it -- which is the only thing that works when the
# rig host is in WSL and Chrome plus the USB device are on Windows. Pass
# --port (or --port auto) to hold it from this process instead.
#
# Every argument is passed straight through to motor_driver.py
# (`--help` for the full list).
#
# Then open http://localhost:<http-port>/ in desktop Chrome or Edge (Web
# Serial isn't supported in Firefox/Safari), click Connect, and pick the Uno's
# port yourself -- this script has no say over which serial port the browser
# talks to.

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# paho-mqtt/pyserial aren't installable system-wide on a PEP 668 host, so a
# local .venv is the normal setup here (see the README). Use it when present.
PYTHON="python3"
if [ -x "${DIR}/.venv/bin/python" ]; then
    PYTHON="${DIR}/.venv/bin/python"
fi

# Peek at --http-port so a leftover process holding that port can be cleared
# first -- the same courtesy the old script did. motor_driver.py still parses
# the flag itself; this only reads it.
PORT=8000
prev=""
for arg in "$@"; do
    case "${prev}" in
        --http-port) PORT="${arg}" ;;
    esac
    case "${arg}" in
        --http-port=*) PORT="${arg#*=}" ;;
    esac
    prev="${arg}"
done

EXISTING_PIDS="$(lsof -ti tcp:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "${EXISTING_PIDS}" ]; then
    echo "Port ${PORT} already in use (pid ${EXISTING_PIDS}) -- killing it."
    kill ${EXISTING_PIDS} 2>/dev/null || true
    for _ in $(seq 1 20); do
        lsof -ti tcp:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1 || break
        sleep 0.2
    done
    if lsof -ti tcp:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "Still in use after SIGTERM -- forcing." >&2
        kill -9 $(lsof -ti tcp:"${PORT}" -sTCP:LISTEN) 2>/dev/null || true
    fi
fi

echo "Press Ctrl+C to stop."
exec "${PYTHON}" "${DIR}/motor_driver.py" "$@"
