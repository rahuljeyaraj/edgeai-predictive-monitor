# Raw Capture & Offline Test Workflow

A full walkthrough, start to finish, for recording labeled raw sensor data
off the rig, pulling it to a laptop, and testing feature/model configs
against it — no prior experience with this build assumed. Every command
below is copy-paste ready. Background reading (optional):
`docs/SENSOR_TELEMETRY_FRAME_PLAN.md`.

**Run all commands from a terminal on your laptop**, with the board plugged
in over USB and `base-station/` as your working directory unless a step
says otherwise.

Three tools, three places they run:

| Tool | Runs on | Purpose |
|---|---|---|
| `raw_capture_server.py` | device (in container) | browser UI, live preview, **recommended** |
| `raw_capture.py` | device (in container) | headless terminal capture |
| `offline_experiment.py` | laptop | test feature/model configs against captures |

---

## 0. Check the board is connected

```bash
adb devices
```

You should see one line with your board's serial number and the word
`device` (not `unauthorized` or `offline`). If nothing shows up, check the
USB cable and try again — nothing past this point will work without it.

Also check `base-station/app.yaml` has:
```yaml
ports: [8080, 8081]
```
Docker only forwards ports listed here from the container to the board's
own network — port 8081 (used by the browser capture UI in step 4) is
unreachable from outside the container without this, no matter what you do
with `adb forward` or a LAN IP later. If it currently only says `[8080]`,
add `8081` and redeploy (`cd base-station && ./deploy.sh`) before
continuing — this is a one-time fix, already done as of this writing.

---

## 1. Put the firmware into raw-capture mode (build + flash)

Normal firmware streams processed spectra to the dashboard. Raw-capture
mode instead streams the raw, un-processed sensor windows, which is what
lets you try different analysis configs later without re-flashing each
time.

1. Open `base-station/sketch/app_config.h`.
2. Find this line (around line 146):
   ```c
   #define FUSER_RAW_CAPTURE_MODE 1
   ```
   Make sure the value is `1`. If it currently says `0`, change it to `1`
   and save the file.
   - **Note:** while this flag is `1`, the normal dashboard's live spectrum
     charts go blank — that's expected, not a bug. You'll flip it back to
     `0` in step 6 when you're done capturing.
3. Flash + deploy from the `base-station/` directory:
   ```bash
   cd base-station
   ./deploy.sh
   ```
4. This takes a few minutes (3-6 min for an incremental build, up to 15 min
   from scratch) — a quiet terminal doesn't mean it's stuck. The script
   ends by streaming the app's logs; press `Ctrl+C` once you see it running
   (the app itself keeps running after you stop watching logs).
5. Skip this step if the flag was already `1` — nothing to rebuild.

---

## 2. Reach the capture page in a browser

You'll open a page served by the board in step 4 below. There are two ways
to reach it — pick based on your setup.

### Option A — `adb forward` (recommended, works over USB alone)

If your laptop is only connected to the board over USB (no shared
WiFi/Ethernet network with it), the board's own IP address won't be
reachable from your laptop's browser even though `adb` itself works fine —
`adb` talks over USB, a browser needs an actual network route. `adb
forward` tunnels a port through the USB connection instead:

```bash
adb forward tcp:8081 tcp:8081
```

Run this once (it's a one-shot setup command, not something that needs to
stay running). Afterwards, `http://localhost:8081/...` on your laptop reaches
port 8081 on the board. Use `localhost:8081` as `<device-ip>:8081` in step 4.

### Option B — LAN IP address (if your laptop shares a network with the board)

If your laptop and the board are both on the same WiFi/Ethernet network
(not USB-only), find the board's IP instead:

```bash
adb shell ip route get 1.1.1.1
```

The output looks like:

```
1.1.1.1 via 192.168.1.1 dev wlan0 src 192.168.1.42 uid 0
```

The number after `src` (`192.168.1.42` above) is the board's IP address on
your network. Use it as `<device-ip>` in step 4.

If you're not sure which applies to you, try Option A first — it works
regardless of network setup as long as USB is connected.

---

## 3. Find the app's Python interpreter

The container's plain `python3` is the bare system interpreter — it does
**not** have the app's packages (`torch`, `statemachine`, etc.) installed.
Those live in a separate `uv`-managed virtual environment under `.cache`.
Find its exact path once:

```bash
adb shell "docker exec edgeai-predictive-monitor-base-station-main-1 find /app/.cache -maxdepth 4 -name python3"
```

This prints a path like `/app/.cache/.venv/bin/python3`. Use **that** full
path (not plain `python3`) in every command below — substitute it in place
of `python3` wherever you see it.

---

## 4. Start a capture

Two ways to do this — pick one.

### Option A — browser UI (recommended)

Live spectra + scalar trends while the rig runs, so you can see the capture
is real before you stop it.

```bash
adb shell "docker exec edgeai-predictive-monitor-base-station-main-1 \
  /app/.cache/.venv/bin/python3 /app/python/tools/raw_capture_server.py --port 8081"
```

(Replace `/app/.cache/.venv/bin/python3` with whatever path step 3 printed,
if different.)

Leave this command running in its terminal window — it's a live server.
Open a **second** terminal for anything else you need to do.

- `--port 8081` (not 8080) — the normal dashboard already owns 8080 in the
  same container. You do **not** need to stop the main app first: it keeps
  running, its dashboard/Telegram features stay up, and its own SPI
  ingestion automatically steps aside for the raw-capture tool (a
  cross-process lock in `ingestion/spi_reader.py`, 2026-07-22) instead of
  contending with it over the shared Bridge/SPI link — it just sees no new
  spectrum data (dashboard charts go blank, as noted in step 1) rather than
  erroring or corrupting the raw capture. Only one raw-capture tool
  (`raw_capture.py` / `raw_capture_server.py`) can hold that lock at a
  time — starting a second one fails fast with a clear error instead of
  silently producing empty plots.
- In a browser, go to:
  ```
  http://<device-ip>:8081/raw_capture.html
  ```
  Using step 2's Option A: `http://localhost:8081/raw_capture.html`.
  Using step 2's Option B: replace `<device-ip>` with the LAN address you
  found, e.g. `http://192.168.1.42:8081/raw_capture.html`.
- You should see live accel/mic spectra and scalar readouts updating.
- Type a label describing the rig's current state (e.g. `healthy`,
  `unbalanced`) into the label field, hit **Start**, hold the rig in that
  state, then hit **Stop** when done.
- Each Start→Stop cycle saves one `.npz` file to `/tmp/captures` inside the
  container. Repeat with a new label for each rig state you want to
  capture (e.g. do one run for `healthy`, stop, change the rig, do another
  run for `unbalanced`).
- When you're completely done capturing, go back to the first terminal and
  press `Ctrl+C` to stop the server.

### Option B — headless terminal

No live preview, just a fixed-duration capture. Useful if you don't need to
watch it happen.

```bash
adb shell "docker exec edgeai-predictive-monitor-base-station-main-1 \
  /app/.cache/.venv/bin/python3 /app/python/tools/raw_capture.py --label healthy --duration 180 --out /tmp/captures"
```

- Change `--label healthy` to describe whatever state the rig is in for
  this run, and put the rig in that state *before* running the command.
- **Always keep `--out /tmp/captures`** — the script's own documented
  default (`/data/captures`) fails with a permission error inside the
  container. `/tmp/captures` is also what the pull script in step 5 expects.
- `--duration` is in seconds (default 180 = 3 min). The command blocks
  until the duration elapses, printing progress every second.
- One label per run, one file per run — never mix labels in one capture
  (keeps a later train/test split leak-free). Run the command again with a
  different `--label` for each rig state.

---

## 5. Pull the captured files onto your laptop

From `base-station/` on your laptop:

```bash
./pull_captures.sh                 # -> ./captures
./pull_captures.sh some/other/dir  # custom local dir
```

- Copies files off the board, checks each one's size matches, and only
  deletes the on-device originals once everything is confirmed safe on your
  laptop (the board's storage isn't the long-term archive — your laptop is).
- Safe to re-run any time — it never nests folders and skips files it
  already has.
- If it prints "No captures found in the container", nothing was saved yet
  — go back and confirm step 4 actually reached the **Stop** step (browser
  UI) or ran to completion (terminal option).

---

## 6. Switch the firmware back to normal mode

Do this once you're done capturing — don't leave the board in raw-capture
mode permanently, since the normal dashboard doesn't work while it's on.

1. Open `base-station/sketch/app_config.h` again and change:
   ```c
   #define FUSER_RAW_CAPTURE_MODE 0
   ```
2. Redeploy:
   ```bash
   cd base-station
   ./deploy.sh
   ```
3. **Important:** after it redeploys, open the normal dashboard in your
   browser (`http://<device-ip>:8080`) and click the trash/**Remove** icon
   on this board's node. This clears a stale internal record left over from
   raw-capture mode (it looked like a 0-channel sensor to the dashboard
   while raw mode was running) — skipping this step can make the dashboard
   error out once real spectral data starts arriving again.

---

## 7. Run the offline test on your laptop

From `base-station/python/` on your laptop, using the local dev `.venv`:

```bash
cd base-station/python

# one-time: install the plotting dependency (not part of the on-device app)
.venv/bin/pip install -r requirements.txt -r tools/requirements-offline-experiment.txt

# test one specific config
.venv/bin/python3 tools/offline_experiment.py --axis-mode separate --bin-count 64 --scalars rms kurtosis std peak crest_factor skewness

# sweep a grid of configs, ranked by healthy-vs-fault separation, with a plot
.venv/bin/python3 tools/offline_experiment.py --sweep --plot-out ../../out.png
```

- `--captures-dir` defaults to `base-station/captures/` — no path needed if
  you pulled into the default location in step 5.
- `--healthy-label` accepts multiple labels (e.g.
  `--healthy-label healthy_noload healthy_load`) to pool sub-conditions for
  training while still reporting each one's own separation.
- `--scalars` accepts any of 6 per-axis time-domain scalars: `rms`,
  `kurtosis`, `std`, `peak`, `crest_factor`, `skewness` — pass any subset.
- `--sweep` tries axis fusion (summed/separate/none) × accel+mic fusion
  (with/without mic) × spectrum resolution (bin counts 8 through 512) ×
  scalar combos (none, each of the 6 alone, and all 6 together) — 280
  configs total, ranked by worst-case healthy-vs-fault separation.
- The report is in **sigma units matching production commissioning**
  (warning = 8σ, fault = 15σ) — a number here means the same thing it would
  mean after real commissioning on the device.
- `--plot-out PATH.png` saves a 3-panel PNG (an example raw window, its
  spectrum, and a healthy-vs-fault score histogram with the 8σ/15σ lines)
  so you can see the result, not just read numbers.

---

## Gotchas

- **Don't skip step 6's dashboard "Remove" click** after returning to
  normal mode — see step 6 for why.
- **Never mix labels in one capture file** — one label per run, by design.
- **No overlapping windows** — each captured window is disjoint. Get more
  data by running the rig longer, not by overlapping windows (avoids
  train/test leakage).
- `raw_capture.py`'s own default `--out` (`/data/captures`) does not work
  inside the container — always override it to `/tmp/captures`.
- If `adb shell ip route get 1.1.1.1` in step 2 returns nothing or an
  error, the board isn't connected to a network yet (WiFi/Ethernet) — the
  browser UI (step 4, Option A) needs it; the headless terminal option
  (Option B) does not, since it never needs a browser to reach the board.
- If a `python3 ...` command reports `ModuleNotFoundError`, you used the
  bare system `python3` instead of the app's venv interpreter — see step 3.
- If the browser page never loads even after `adb forward`/LAN IP (step 2),
  check `base-station/app.yaml` has `ports: [8080, 8081]`, not just
  `[8080]` — see the note in step 0.
- **`./deploy.sh` occasionally fails mid-push** with `adb: no
  devices/emulators found` or, on the next attempt, `descriptor app.yaml
  file missing from app` — a known flaky USB transfer, not a real error in
  the script's logic. Fix: wait a few seconds, confirm `adb devices` shows
  the board again, and just re-run `./deploy.sh`.
