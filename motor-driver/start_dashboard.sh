#!/usr/bin/env bash
#
# start_dashboard.sh
#
# Serves dashboard.html locally so the Web Serial API works (some browsers
# refuse it on file:// -- see README's "Browser dashboard" section).
#
# Usage:
#   ./start_dashboard.sh [port]     (default 8000)
#
# If something is already listening on that port (e.g. a previous run of this
# script left running), it's killed first so this always ends up serving.
#
# Then open http://localhost:<port>/dashboard.html in desktop Chrome or Edge
# (Web Serial isn't supported in Firefox/Safari), click Connect, and pick the
# Uno's port yourself -- this script has no say over which serial port the
# browser talks to.

set -euo pipefail

PORT="${1:-8000}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

EXISTING_PIDS="$(lsof -ti tcp:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "${EXISTING_PIDS}" ]; then
    echo "Port ${PORT} already in use (pid ${EXISTING_PIDS}) -- killing it."
    kill ${EXISTING_PIDS} 2>/dev/null || true
    for i in $(seq 1 20); do
        lsof -ti tcp:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1 || break
        sleep 0.2
    done
    if lsof -ti tcp:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "Still in use after SIGTERM -- forcing." >&2
        kill -9 $(lsof -ti tcp:"${PORT}" -sTCP:LISTEN) 2>/dev/null || true
    fi
fi

echo "Serving ${DIR} at http://localhost:${PORT}/dashboard.html"
echo "Press Ctrl+C to stop."
exec python3 -m http.server -d "${DIR}" "${PORT}"
