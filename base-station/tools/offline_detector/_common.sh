# Shared setup for the offline detector's two entry points. Sourced, not run.
#
# Everything here is discovered rather than hardcoded, with one exception:
# PORT. The dashboard listens on 8080 and a closed port answers in ~35ms,
# which reads exactly like a healthy fast response if you only look at
# elapsed time -- so a wrong port here produces a confident wrong answer.
BOARD_IP=${BOARD_IP:-192.168.1.10}
PORT=${PORT:-8080}
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT=${OUT:-$(mktemp -d)}

# The LAN lane has to run on Windows, not in WSL2: WSL2's route to the LAN
# is not the browser's route, and it reports failures the browser never sees
# (measured 6/6 WSL timeouts while Windows got 2/6 through).
win_temp() {
  local t
  t=$(cd /mnt/c && powershell.exe -NoProfile -Command '$env:TEMP' 2>/dev/null | tr -d '\r')
  [ -n "$t" ] && wslpath -u "$t" 2>/dev/null
}

# Copies the LAN probe somewhere Windows can execute it and echoes the
# Windows-style path. Exits non-zero if there's no Windows side to use.
stage_lan_probe() {
  local wt; wt=$(win_temp)
  if [ -z "$wt" ] || [ ! -d "$wt" ]; then
    echo "no Windows host reachable -- the LAN lane needs powershell.exe" >&2
    return 1
  fi
  cp "$HERE/lan_probe.ps1" "$wt/lan_probe.ps1" || return 1
  wslpath -w "$wt/lan_probe.ps1"
}
