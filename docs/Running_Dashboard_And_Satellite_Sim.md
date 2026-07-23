# Running the dashboard + satellite node simulator(s)

Exact copy-pasteable commands to run the real dashboard backend
(`mpu/main.py --mqtt-host`) against one or more standalone
`mpu/tools/satellite_node_sim.py` processes, each mimicking a real ESP32
satellite node over MQTT (see `docs/Appendix_B_Wire_Protocol_Specification.md`).

Two variants are covered:

- **Variant A** (below): dashboard and simulator(s) both run on the host —
  quickest for dev iteration, no real hardware involved at all.
- **Variant B** ([further down](#variant-b-dashboard-on-the-uno-q-device-real-mcu--mqtt-satellite-nodes-together)):
  dashboard runs on the real uno-q device so it can also ingest the real
  MCU over UART (`--serial-port /dev/ttyHS1`) at the same time as MQTT
  satellite nodes (real or simulated) — `mpu/main.py` supports both
  ingestion sources simultaneously.

## 1. Check for / kill an old backend process

If a previous `mpu/main.py` is still bound to port 8080:

```bash
ps aux | grep "mpu/main.py"
```

If one shows up, kill it by PID:

```bash
kill <PID>
```

## 2. Install and start Mosquitto (one-time)

```bash
sudo apt-get update
sudo apt-get install -y mosquitto mosquitto-clients
sudo systemctl enable --now mosquitto
```

Verify it's listening on 1883:

```bash
ss -ltn | grep 1883
```

## 3. Start the dashboard backend

From the repo root, in its own terminal:

```bash
cd ~/workspace/edgeai-predictive-monitor-unoq
PYTHONPATH=mpu/common:mpu/ingestion:mpu/registry:mpu/pipeline:mpu/history:mpu/api:mpu/monitoring \
    python3 mpu/main.py --mqtt-host localhost --port 8080
```

Open `http://localhost:8080` in a browser.

## 4. Start satellite simulator(s)

Point `--data-dir` straight at the dataset root (e.g. the downloaded
[vibration-based-fault-diagnosis-of-machines](https://www.kaggle.com/datasets/sumairaziz/vibration-based-fault-diagnosis-of-machines)
folder) — the simulator recurses through nested folders like
`Cracking/Cracking_X/M(1).csv` and automatically skips `.mat` files and any
stray `*.csv:Zone.Identifier` Windows artifact files.

```bash
cd ~/workspace/edgeai-predictive-monitor-unoq
python3 mpu/tools/satellite_node_sim.py --mqtt-host localhost \
    --data-dir /home/rahuljeyaraj/workspace/vibration-based-fault-diagnosis-of-machines \
    --ui-port 9101
```

Run it again with a different `--ui-port` for each additional fake node:

```bash
python3 mpu/tools/satellite_node_sim.py --mqtt-host localhost \
    --data-dir /home/rahuljeyaraj/workspace/vibration-based-fault-diagnosis-of-machines \
    --ui-port 9102
```

Each prints its node id and UI URL on startup. Open that URL, tick
Accel/Mic enabled, pick a file (e.g. `Wear/Wear_Z/M(2).csv`) from the
dropdown, and hit "Go Online."

Or drive it headless via its HTTP API instead of the browser UI (useful
for scripting/CI):

```bash
curl -s -X POST http://localhost:9101/channel -H "Content-Type: application/json" \
    -d '{"channel":"accel","enabled":true,"file":"Wear/Wear_Z/M(2).csv"}'
curl -s -X POST http://localhost:9101/online -H "Content-Type: application/json" \
    -d '{"online":true}'
```

---

## Variant B: dashboard on the uno-q device (real MCU + MQTT satellite nodes together)

Runs `mpu/main.py` **on the uno-q device itself** via `adb shell`, with
both ingestion sources at once: the real MCU over LPUART1
(`--serial-port /dev/ttyHS1`) and MQTT satellite nodes
(`--mqtt-host localhost`, broker also running on-device). A
`satellite_node_sim.py` on your **host** machine can then reach the
device's broker over the LAN and show up in the same dashboard.

This is one-time setup per fresh device; skip installed steps on repeat runs.

### 1. Confirm adb sees the device

```bash
adb devices
```

### 2. Install prerequisites on-device (one-time)

The device (Debian trixie) needs the Mosquitto broker plus every
third-party Python package `mpu/` imports. Most are in apt;
`python-statemachine` is not packaged for Debian and needs pip.

```bash
adb shell "sudo apt-get update"
adb shell "sudo apt-get install -y mosquitto mosquitto-clients \
    python3-paho-mqtt python3-fastapi python3-uvicorn \
    python3-numpy python3-psutil python3-torch python3-serial"
adb shell "sudo python3 -m pip install --break-system-packages 'python-statemachine==3.2.0'"
```

Verify everything imports:

```bash
adb shell "python3 -c 'import statemachine, numpy, psutil, torch, fastapi, uvicorn, paho.mqtt.client, serial; print(\"all deps OK\")'"
```

### 3. Open Mosquitto to the LAN (one-time)

The Debian mosquitto package binds to `127.0.0.1:1883` by default —
fine for a dashboard running on the same device, but unreachable from a
simulator on your host. Add a LAN listener:

```bash
adb shell "printf 'listener 1883 0.0.0.0\nallow_anonymous true\n' | sudo tee /etc/mosquitto/conf.d/lan.conf"
adb shell "sudo systemctl restart mosquitto"
adb shell "systemctl is-active mosquitto; ss -tlnp | grep 1883"
```

Expect `0.0.0.0:1883` in the `ss` output, not `127.0.0.1:1883`.

**Security note**: this allows anonymous pub/sub from anyone on the same
LAN. Fine for a private dev network; do not do this on a shared/untrusted
network without adding auth.

### 4. Push the code

```bash
adb shell "rm -rf /home/arduino/mpu"
adb push mpu /home/arduino/mpu
```

The `rm -rf` first matters: `adb push mpu /home/arduino/mpu` nests the
source folder one level deeper (`/home/arduino/mpu/mpu/...`) instead of
overwriting in place if `/home/arduino/mpu` already exists — silently
leaving stale code in place. Always wipe the destination first when
re-pushing after code changes.

### 5. Start the dashboard on-device

```bash
adb shell "cd /home/arduino/mpu && \
    PYTHONPATH=/home/arduino/mpu/common:/home/arduino/mpu/ingestion:/home/arduino/mpu/registry:/home/arduino/mpu/pipeline:/home/arduino/mpu/history:/home/arduino/mpu/api:/home/arduino/mpu/monitoring \
    nohup python3 main.py --serial-port /dev/ttyHS1 --mqtt-host localhost --port 8080 \
    > /home/arduino/mpu_dashboard.log 2>&1 & disown"
```

Check it came up clean:

```bash
adb shell "tail -n 20 /home/arduino/mpu_dashboard.log"
```

Should show `Uvicorn running on http://0.0.0.0:8080` with no traceback.
Find the device's LAN IP (printed by e.g. `spectrum_server.py`, or
`adb shell "ip route get 1"`) and open `http://<device-ip>:8080` from any
browser on the same LAN.

Pass only one of `--serial-port`/`--mqtt-host` if you just want one
ingestion source (e.g. no MCU connected yet — MQTT-only is enough to test
with a simulator).

### 6. Point a simulator at the device's broker, from your host

```bash
mkdir -p /tmp/sat-sim-data   # or any dir; must exist, and see note below
python3 mpu/tools/satellite_node_sim.py --mqtt-host <device-ip> --mqtt-port 1883 \
    --data-dir /home/rahuljeyaraj/workspace/vibration-based-fault-diagnosis-of-machines \
    --ui-port 9101
```

`--data-dir` must contain real waveform files (see Variant A step 4) —
an empty directory starts the simulator fine but it never actually
publishes anything, since no channel has data to read.

Open the printed `http://localhost:9101` UI, enable a channel, pick a
file, hit "Go Online" (or drive it headlessly per the `curl` snippet
above, pointed at port 9101). The node should appear in the on-device
dashboard's node list (`GET http://<device-ip>:8080/nodes`) within a few
seconds.
