# Plan — Base station + satellite WiFi onboarding

Status: **§1 (base station) implemented + live-verified 2026-07-29** (real
disruptive hardware test, plus a manual phone join of the onboarding AP through
the actual form). §2/§3 (satellite/sim onboarding) remain design-only. Companion
to
[SENSOR_TELEMETRY_FRAME_PLAN.md](SENSOR_TELEMETRY_FRAME_PLAN.md) and
[EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md](EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md).

## Implementation notes (§1, 2026-07-29)

- **The board's own "open item" resolved**: NetworkManager (not hostapd/
  dnsmasq) turned out to already be the network stack in use (`nmcli`
  present + active, board already joined to a real WiFi network via an NM
  connection profile), and avahi-daemon was already running and publishing
  `<hostname>.local` by default. So the implementation is nmcli-driven, not
  a hand-rolled hostapd/dnsmasq stack, and mDNS needed no new code — just a
  hostname rename (`epm` → `epm-base`) to match this doc.
- **Host-side**: `base-station/host/wifi_bridge.py` (root, systemd via
  `host/wifi-bridge.service`) drives `nmcli` from outside the app container
  (same non-privileged-container reasoning as `host/spi_bridge.py`/
  `host/gpu_bridge.py`), exposed over `/dev/wifi-link.sock`. A monitor loop
  brings up an open `EPM-BaseStation` Hotspot NM profile whenever wlan0
  isn't genuinely joined to a real network — covers "no creds yet," "join
  failed," and "dropped later" with one check. One-time setup:
  `base-station/provision-wifi.sh` (mirrors `provision-spi.sh`).
- **Concurrent AP+STA**: not assumed/needed. Joining the factory network
  drops the Hotspot outright (single physical wlan0 switching modes) —
  the doc's "full switch on success" fallback, not "keep hotspot alive."
- **Credentials**: NetworkManager owns persistence + autoconnect itself
  (`/etc/NetworkManager/system-connections/*.nmconnection`) — no app-side
  credential store was added.
- **App-side**: `base-station/python/network/wifi.py` (status poller +
  blocking `connect()`), routes `GET/POST /network/wifi/*` in `api/app.py`,
  and the dashboard's "Network" tab (previously a placeholder) now hosts
  the SSID/password form + live mode/SSID/IP display.
- **AP security**: open (no password) — a deliberate, deployment-scale
  simplification for a transient, physically-supervised onboarding step.
- **Live-verified 2026-07-29**: killed the board's real WiFi, watched the
  Hotspot fallback come up automatically (~3s), joined `EPM-BaseStation`
  from a real phone, submitted real factory-WiFi credentials through the
  actual Network tab form, confirmed it rejoined and stayed stable. Three
  bugs only surfaced by this live test (all fixed, re-verified, still not
  committed as of the first pass — see repo history for the commit that
  landed this):
  1. AP-mode status reported the NM connection *profile* name ("Hotspot")
     instead of the real broadcast SSID technicians would actually see.
  2. `nmcli device wifi connect <ssid> password <pw>` (the "quick connect"
     shorthand) is unreliable on this board's nmcli version — fails with
     `802-11-wireless-security.key-mgmt: property is missing` even against
     a fresh profile. Fixed by building the connection profile explicitly
     (`connection add` → `modify wifi-sec.key-mgmt wpa-psk`
     → `up`) instead of relying on nmcli's shorthand.
  3. A real concurrency bug: the monitor loop's "not connected yet" check
     could queue behind an in-flight connect attempt and force the Hotspot
     back up *right after* a successful join, undoing it. Fixed by having
     that guard also bail out when already connected to a real network.
- **Known follow-up**: user has additional issues to fix in a future
  session (not yet itemized here — ask at the start of that session).

## 0. Scope

- **Demo:** 2 real ESP32-S3 satellites + 3-4 simulated satellites (host-machine
  processes via [`satellite_node_sim.py`](../base-station/python/tools/satellite_node_sim.py),
  no radio). The demo is a pre-recorded video, so venue-network robustness (client
  isolation, etc.) isn't a live risk for it — but the design below should still hold
  up for real deployments later.
- **Decision (superseded during brainstorm):** an earlier two-tier idea (satellites
  always join a fixed base-station-owned AP; only the base station touches the
  factory network) was dropped. All devices — base station and satellites — join
  the factory WiFi directly.

## 1. Base station onboarding

1. Boots with no saved WiFi credentials → starts its own AP + captive portal.
2. Technician connects to that AP; a one-page form asks for the factory WiFi SSID +
   password.
3. Base station attempts to join as a client. On success: stores the credentials,
   advertises itself via mDNS (e.g. `epm-base.local`).
4. On failure: falls back to AP mode automatically — no manual factory reset
   required.

**Resolved (2026-07-29):** not pursued — implemented on the assumption of a single
physical radio (full switch on success, hotspot doesn't stay alive concurrently).
See "Implementation notes" above.

## 2. Satellite onboarding (ESP32-S3)

ESP32-S3 confirmed supports AP, STA, and AP+STA concurrently.

1. Boots with no saved credentials → starts its own AP with a unique SSID (e.g.
   `EPM-SAT-<id>`) + a one-page captive-portal form.
2. Form fields: **SSID, password, and MQTT broker address.** The broker field is
   pre-filled with the base station's mDNS name (`epm-base.local`) but overridable
   with a raw IP — some managed/factory WiFi blocks multicast (mDNS) across VLANs,
   so this avoids depending on a discovery protocol that might silently fail on
   segmented networks. This reuses the same onboarding step already needed for
   SSID/password, no extra engineering.
3. On success: stores credentials, drops its own AP, joins the factory network,
   connects to the broker at the given address, and appears on the base station
   dashboard automatically (existing "New" node flow).
4. On failure or a later disconnect: auto-falls back to its own AP, so a node can
   always be recovered without a manual reset.

**Why per-device AP+portal instead of BLE provisioning:** at demo scale (2 real
satellites) the extra engineering for BLE-based provisioning isn't justified.
Per-device AP+portal means a phone/laptop must join each device's network one at a
time, which gets tedious past roughly 10 devices — noted here as a **documented
future upgrade path** if a real deployment scales to dozens of satellites.

## 3. Simulated satellites

No onboarding needed — they're host-machine processes, not radios. For a live (not
recorded) demo run, point `main.py --mqtt-host` at the UNO Q's actual IP/hostname
instead of `localhost` (the flag already exists — no new code needed).

## 4. mDNS primer (why it's used, and its one real caveat)

mDNS resolves a stable name (`epm-base.local`) to whatever the current IP is via a
local multicast query/response, so it survives the base station's IP changing on
DHCP renewal/reconnect without any manual reconfiguration — a client just re-asks
"who is `epm-base.local`?" and gets back the current answer.

**Caveat:** multicast traffic often doesn't cross routed VLANs and is sometimes
blocked outright on managed/enterprise WiFi. This is exactly why the satellite
onboarding form (§2) has a manual-IP-override field rather than relying on mDNS
alone.

## 5. Explicitly out of scope for this doc

No firmware/dashboard code changes yet — design only. Other brainstormed dashboard
features from the same session (chart-clutter redesign, per-node EI data-collection
UI, dev/perf page, Telegram alerts, clickable status filters, LED matrix status
message) are **not** covered here — separate topics, still backlog/undocumented.
