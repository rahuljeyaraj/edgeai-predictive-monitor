# Plan — Base station + satellite WiFi onboarding

Status: **§1 (base station) implemented + live-verified 2026-07-29**, including
Round 2's captive-portal auto-open (confirmed working on a real phone) and
Round 3's scan/messaging fixes below. **§2 (satellite) implemented 2026-08-01,
restyled + scan/RGB fixes added 2026-08-17, built + compiles clean, NOT yet
live-verified on real hardware** — see "Implementation notes (§2, satellite)"
and its 2026-08-17 follow-up below. §3 (sim onboarding) remains out of scope
(no onboarding needed, see §3). Companion to
[SENSOR_TELEMETRY_FRAME_PLAN.md](SENSOR_TELEMETRY_FRAME_PLAN.md) and
[EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md](EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md).

## Round 2: UX follow-up fixes (2026-07-29, built, NOT yet live-verified)

Real usage of the Round 1 build surfaced five issues, all addressed:

1. **No auto-loading portal page** — joining `EPM-BaseStation` required
   manually typing the IP, unlike a real airport-WiFi captive portal.
   Fixed with the actual captive-portal trick: `provision-wifi.sh` now
   drops an `/etc/NetworkManager/dnsmasq-shared.d/captive-portal.conf`
   (`address=/#/10.42.0.1`) so every DNS lookup a joined device makes
   resolves to the Hotspot's own IP, and `wifi_bridge.py` runs a new
   port-80 listener that 302-redirects any request to the dashboard's
   Network tab (`http://<ip>:8080/?tab=network`). An OS's own
   connectivity-check probe (Apple/Google/Microsoft/Firefox all ping
   different well-known URLs) hits this instead of the real internet and
   the OS pops its browser open on the redirect target automatically —
   same mechanism real captive portals use. `app.js` gained a `?tab=`
   deep-link so that URL lands directly on Network, not Fleet.
2. **Copy said "factory" / called AP mode onboarding-only** — both wrong:
   this AP is a real network usable standalone (satellites can join it
   directly, not just during setup). Reworded the heading ("Join a WiFi
   network") and caption in `network.js`, and dropped the "(onboarding)"
   suffix from the AP mode label.
3. **Redundant IP address shown in AP mode** — whoever reached the page
   already knows it (they typed it, or landed via the new captive-portal
   redirect). Now only shown once actually joined to a real network (STA
   mode).
4. **No visible list of nearby WiFi networks** — added a `scan` command to
   `wifi_bridge.py` (`nmcli device wifi list --rescan yes`), a
   `GET /network/wifi/scan` route, and a `<datalist>`-backed SSID field
   (native combobox: pick from the list or type a hidden network's name)
   with a manual "Rescan" button. **Known caveat, not yet re-tested**:
   Round 1's live testing found this radio can't scan for other networks
   while it's hosting its own AP (`nmcli device wifi list` returned
   nothing but itself) — i.e. the scan may come back empty in exactly the
   onboarding moment (technician on the Hotspot, about to join the real
   network) it'd be most useful for. It should still work once already on
   a real network (switching to a different one). The UI already degrades
   gracefully either way (manual SSID entry always works), but this needs
   a live check before calling the scan itself reliable.
5. **"Failed to fetch" on a successful join, no success message** — root
   cause: a successful join means the radio switches away from whatever
   network carried the connect request (the Hotspot itself, most often),
   so the HTTP response can never arrive — this is `fetch()` throwing a
   network-level `TypeError`, not the backend actually failing. `network.js`
   now tells that apart from a real HTTP-level failure and shows an
   amber "connection dropped — that usually means it worked, go check
   `http://epm-base.local`" notice instead of a bare fetch error, plus an
   explicit green success message on the (rarer) case the response does
   arrive.

**Not yet done:** live-verified on real hardware/phones (the redirect in
particular needs testing across iOS/Android/Windows/desktop-browser probe
behavior, which varies) — needs `provision-wifi.sh` re-run on the board
(new dnsmasq drop-in + port-80 listener) plus an app redeploy.

## Round 3: live-test findings + fixes (2026-07-29, built + deployed)

Round 2 shipped untested; a real phone test found the captive-portal
auto-open works, but surfaced new issues:

1. **Item 4's "known caveat" was wrong** — live-tested scanning while
   genuinely in AP mode (Hotspot up, confirmed via `nmcli`) and it DOES
   find nearby networks, contrary to Round 1's assumption. The real
   problem was different: `scan_payload()` forced `--rescan yes` on
   *every* call, so every scan paid the full ~7s real-scan cost, and any
   hiccup (timeout, a flaky phone-side captive-portal browser) came back
   indistinguishable from a genuine empty result — both were just `[]`.
   Fixed: switched to `--rescan auto` (nmcli's own cache-freshness
   judgment — repeat calls now return in ~0.1s when a recent scan already
   ran, confirmed live), and threaded a distinct `error` field end-to-end
   (`wifi_bridge.py` → `python/network/wifi.py` → the REST route →
   `network.js`) so a real scan failure shows "couldn't scan — try again"
   instead of a misleading "no networks found."
2. **Removed Round 2's new caption** ("this device broadcasts its own
   network... a real network other devices can connect to directly") —
   confusing, cut per live-test feedback. Heading reverted from "Join a
   WiFi network" back to **"Connect to Wi-Fi"**.
3. **Post-submit message often never got read** — on the onboarding
   hotspot, tapping Connect can close the page (or drop its connectivity)
   almost immediately once the device's own network switches, sometimes
   before any post-submit message renders at all. Fixed by moving the
   warning **before** the action instead of after: a short "this page may
   close after you tap Connect — that's normal, reopen the dashboard to
   check" tip is now always visible in AP mode, read before the risk
   starts rather than raced against it. The existing post-submit
   notice/error/success messages (which still show correctly whenever the
   page *does* survive) were also shortened to single short sentences.

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
- **Known follow-up**: addressed 2026-07-29 — see "Round 2" and "Round 3" above.

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

**Resolved (2026-08-01):** built as designed above, with one deliberate deviation
— see "Implementation notes" below.

### Implementation notes (§2, satellite, 2026-08-01)

- **Concurrent AP+STA, not full-switch** (deviates from the base station's
  single-radio design): ESP32-S3 supports `WIFI_MODE_APSTA` natively, so the
  satellite keeps its AP up *while* testing a submitted STA join and only tears it
  down once the join is **confirmed good** (with a ~4s grace period so the portal's
  success response reaches the phone before the AP link itself goes away). A failed/
  wrong-password attempt never disconnects the technician — they stay on the portal,
  see a real error inline, retry without rejoining anything. This is the concrete
  fix for the exact race the base station's own onboarding hit (Round 2/3's
  "Failed to fetch" / "this page may close" issues) — made possible here
  specifically because the satellite doesn't share the base station's single-radio
  constraint.
- **Captive-portal auto-open** uses the ESP32-native equivalent of the base
  station's dnsmasq trick: `DNSServer` wildcard-resolves every DNS lookup to the
  AP's own IP, and the `WebServer` answers any unmatched path — including each OS's
  own connectivity-check probe URL (`/generate_204`, `/hotspot-detect.html`,
  `/ncsi.txt`, `/connecttest.txt`, etc.) — with a 302 redirect back to `/`, matching
  what actually worked live for the base station (a redirect, not a bare 200 page,
  is what reliably triggers the OS's automatic captive-portal browser).
- **State machine** (`satellite/src/threads/transport_task.cpp`): `BOOT_STA_ATTEMPT`
  (saved creds, bounded 15s reconnect) → `CONNECTED`, or → `PROVISIONING` (AP+portal
  up) on failure. A submission moves `PROVISIONING` → `STA_TESTING` (bounded 15s
  join, AP stays up) → `CONNECTED` on success (credentials persisted only now, never
  on an unverified submission) or back to `PROVISIONING` with an inline error on
  failure. `CONNECTED` → `RECOVERING` on WiFi loss (silent `WiFi.reconnect()` retries
  for 60s, MQTT-only blips don't trigger this) → back to `CONNECTED` if it self-heals,
  or → `PROVISIONING` (AP reopens, no reboot) if the window expires; background
  reconnect attempts continue even with the AP up, so a network coming back on its
  own drops the AP again without a technician submitting anything.
- **Credentials**: ESP32 NVS via `Preferences.h` (`satellite/include/hal/
  hal_credentials.h`, `satellite/src/drivers/nvs_credentials.cpp`), namespace
  `epm_net` — no external dependency needed, bundled with the arduino-esp32 core,
  matching this port's existing minimal-dependency style.
- **Dev-bench escape hatch**: `WIFI_SSID`/`WIFI_PASSWORD` stay in `app_config.h` as
  compile-time overrides; if both are set via `build_flags` away from their
  `"CHANGE_ME"` placeholders, NVS is auto-seeded from them on first boot and the
  portal is skipped — avoids the AP+form dance on this project's 2 real bench boards
  during frequent reflash cycles. No-op for a real deployment build, which passes
  neither flag.
- **RGB status colors** reuse the base station's own `status_color.py` tuples
  wherever the semantics already match (same color language a technician has
  already learned from the dashboard) — WARNING's amber for a failed join attempt,
  NEW's cyan for freshly connected, OFFLINE's grey for silent recovery — plus one
  new hue (magenta) for the one genuinely new concept, local AP/provisioning mode.
- **Not yet done**: live-verified on real ESP32-S3 hardware (compiles clean,
  `-Wall -Wextra` warning-free, RAM 33.5%/Flash 23.5% used) — needs a real join
  test against `EPM-BaseStation`, a wrong-password retry-in-place test, a WiFi-loss
  recovery test, and an iOS + Android captive-portal auto-open check (probe/redirect
  behavior varies by OS, same caveat the base station's own Round 2/3 testing found).

### Follow-up (2026-08-17): dashboard-matched styling, network scan, split mDNS/IP fields, RGB gaps closed

Built without a fresh live-hardware pass (compiles clean, `pio run` verified; portal
HTML/JS behavior verified in a headless-browser mock, not the real ESP32-served
page — same caveat as above still applies, now also covering these changes).

1. **Portal now visually matches the dashboard**, not the light-themed placeholder
   CSS the first build shipped with: `satellite/src/drivers/provisioning_portal.cpp`'s
   inline `<style>` block now reuses the exact palette from
   `base-station/python/frontend/style.css` (`#0f172a` page / `#1e293b` card /
   `#334155` border / `#e2e8f0` text / `#10b981` primary-action green). No tabs —
   this is a single-purpose page, unlike the dashboard's multi-tab shell.
2. **Nearby-network scan + tap-to-fill chips**, the same fix the base station's own
   onboarding needed in Round 3 (a `<datalist>` dropdown doesn't render reliably in
   a mobile/captive-portal browser) — built this way from the start here instead of
   repeating that mistake. New `GET /scan` route calls `WiFi.scanNetworks()`
   synchronously (a couple of seconds, same blocking tradeoff `attempt_sta_join()`
   already makes) and returns a de-duped SSID list; the page's JS renders one
   tappable pill per network. Unlike the base station's Linux radio, ESP32 AP+STA
   scanning while hosting an active softAP is standard ESP-IDF behavior, not a known
   limitation — not yet confirmed against real interference/noise on the bench
   boards, though. Live hardware testing the same day surfaced one real gap: the
   page only ever scanned once, on load — no way to retry if the technician's
   target network powers on late or a first scan just misses it. Fixed with an
   **exact copy of the base station's own "Scan for networks" button**
   (`network.js`'s `.btn-label` — same label text, same style: `#334155`
   background, `#e2e8f0` text, 30px height, 6px radius) added to the portal page,
   wired to the same `/scan` fetch the initial page-load scan already used, now
   pulled into a shared `doScan()` so both call sites stay in sync. Button
   disables and reads "Scanning…" for the duration, same as the base station's.
3. **MQTT broker address split into two fields** instead of one field that silently
   accepted either an mDNS name or a raw IP: "Base station address (mDNS name)"
   (prefilled from `MQTT_BROKER_HOST`/last-saved value) plus an optional "IP address
   — only if mDNS doesn't resolve" field. A filled-in IP always wins over the mDNS
   field at submit time. Picking the base station's own hotspot SSID
   (`BASE_STATION_HOTSPOT_SSID` = `EPM-BaseStation`, `app_config.h`) from the scan
   chip list auto-fills the IP field with `BASE_STATION_HOTSPOT_IP` = `10.42.0.1`
   (matching `base-station/host/wifi_bridge.py`'s own `HOTSPOT_IP`) and shows an
   inline hint — this is the direct answer to "what do I put in the MQTT field if
   I'm joining the base station's own hotspot instead of the factory network,"
   since mDNS resolution on that hotspot's own subnet is untested and there'd
   otherwise be no way for a technician to know that fixed address. Switching to a
   different chip afterward clears the auto-filled IP again (tracked via a
   `data-auto` flag so a manually-typed IP is never clobbered).
4. **Two real RGB gaps closed** — both existed since the 2026-08-01 build but were
   never wired up, and neither the "not yet done" line above nor
   [[satellite-bringup-guide]]'s own troubleshooting table (which had already
   started documenting an amber "last attempt failed" ring color) had a build that
   actually produced it:
   - `RGB_JOIN_FAILED` (amber) was defined in `transport_task.cpp` but never once
     passed to `hal_display_rgb_set()` — a failed portal submission left the ring on
     whatever `RGB_STA_TESTING` (magenta) was showing, indistinguishable from
     "still testing." Now set (`RGB_DISPLAY_BREATHE`, 700ms) the moment a submitted
     join fails, before returning to `PROVISIONING`.
   - No ring color existed at all for "WiFi joined fine, but the MQTT broker itself
     is unreachable" (wrong host/IP, broker down, firewall) — `TRANSPORT_STATE_
     CONNECTED` looked identically solid-cyan whether or not telemetry was actually
     flowing. New `RGB_MQTT_UNREACHABLE` (`0xff0000`, reusing `status_color.py`'s
     tuned WS2812 FAULT red rather than inventing a new hue — safe to reuse since
     this state can only ever show while the dashboard has no MQTT channel to push
     a real FAULT command anyway) now breathes on the ring for as long as
     `mqtt_client.connected()` is false, switching back to solid cyan the moment it
     connects.
5. **Technician-triggered re-provisioning, closing a real field gap found live
   the same day**: before this, the *only* way back to the portal once a node had
   joined a network was waiting out a genuine WiFi drop (`RECOVERY_WINDOW_MS`,
   60s) or a full `pio run -t erase` + reflash — no help at all for "this node is
   happily connected to the wrong network, or the right network with a wrong
   broker address, please give me the form back." Holding the XIAO's onboard
   **BOOT button** (`PIN_BOOT_BUTTON` = GPIO0, `board_pins.h`) for 3s
   (`FORCE_PROVISION_HOLD_MS`) now forces the AP+portal back up from *any*
   state, including `CONNECTED` — deliberately landing in a new
   `TRANSPORT_STATE_FORCED_PROVISIONING` rather than reusing `PROVISIONING`
   as-is, since `PROVISIONING`'s existing background self-heal check
   (`WiFi.status() == WL_CONNECTED` → declare success, close the portal) would
   otherwise fire on the very next 10ms tick whenever the node was still
   genuinely connected underneath (the exact case this exists for). The
   existing STA link, if any, is left alone (concurrent AP+STA, same as
   `STA_TESTING`) until a real submission supersedes it. No new hardware — BOOT
   already exists on the XIAO for bootloader entry, just unused at runtime
   before this.

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
