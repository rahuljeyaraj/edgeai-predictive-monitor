# Offline detector

Tells you whether an "everything went Offline" event on the dashboard was the
**app** or the **link**. Those look identical on screen and need opposite
fixes, which is why this exists.

## Why the ambiguity is real

`OFFLINE` is computed entirely in the browser — `frontend/app.js`'s
`OFFLINE_AFTER_S = 10`, applied to `last_seen` staleness. Nothing server-side
ever sets it. So a node reads Offline for either of two reasons:

- the app genuinely stopped seeing that node, **or**
- the browser could not fetch fresh data within 10s.

The second one paints the *entire* fleet Offline at once, `base_station`
included — and `base_station` arrives over SPI and never touches MQTT, so a
whole-fleet flip is itself a hint that the transport to the browser is at
fault rather than any per-node path.

## Use it

```sh
./offline_probe.sh 120          # 120s, three lanes, one verdict
BOARD_IP=192.168.1.42 ./offline_probe.sh
```

Three lanes run in the same window:

| lane | what it asks | crosses WiFi? |
|---|---|---|
| BOARD | `GET /nodes` from inside the UNO Q via `adb shell` | no |
| LAN | the same GET from the **Windows** host | yes |
| PING | PC→board vs PC→router | yes |

Board clean + LAN dirty ⇒ the link. Board dirty ⇒ the app — debug ingest, and
check the `frames_ok` / `arm_errors` / `on_frame_errors` deltas it prints.

## Two traps that cost a session

- **The dashboard is on port 8080, not 8000.** `curl` against a closed port
  returns in ~35ms with rc=7 — which reads exactly like a healthy fast
  response if you only look at elapsed time. Always check the status code.
- **Run the LAN lane from Windows, not WSL2.** WSL2 does not share the
  browser's route to the board; measured 6/6 WSL timeouts in a window where
  Windows got 2/6 through. Same reason `adb forward` must never be used to
  judge reachability.

## `load_amplifier.sh` — read the caveat

Raises the failure rate on demand (N concurrent pollers) so you can exercise a
suspected fix without waiting. **It does not reproduce the reported bug**: the
real complaint occurs with a single dashboard tab, while this makes an
*overloaded* link fail, possibly by a different mechanism. `offline_probe.sh`
is the instrument to trust. Calibration numbers are in the script header.

## Files

| file | runs on |
|---|---|
| `offline_probe.sh` | WSL — orchestrates all three lanes |
| `board_probe.py` | pushed to the UNO Q, run via `adb shell python3` |
| `lan_probe.ps1` | Windows host, via `powershell.exe` |
| `summarize.py` | WSL — merges the lanes into one verdict |
| `load_amplifier.sh` | WSL — optional, see caveat above |
