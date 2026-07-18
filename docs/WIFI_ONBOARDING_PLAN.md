# Plan — Base station + satellite WiFi onboarding

Status: **Design only — nothing implemented yet.** Captures a brainstorm session's
conclusions for later implementation. Companion to
[SENSOR_TELEMETRY_FRAME_PLAN.md](SENSOR_TELEMETRY_FRAME_PLAN.md) and
[EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md](EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md).

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

**Open item to verify on hardware:** whether the UNO Q's WiFi chip supports
concurrent AP+STA (check `iw list` capabilities). This determines whether the base
station can keep its own hotspot alive *while also* joined to the factory network,
versus having to fully switch modes on success.

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
