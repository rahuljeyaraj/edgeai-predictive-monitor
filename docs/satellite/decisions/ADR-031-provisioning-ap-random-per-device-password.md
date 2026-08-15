---
id: ADR-031
title: Provisioning AP defaults to WPA2 with a random, NVS-persisted per-device password (not open, not MAC-derived)
status: accepted
date: 2026-08-06
deciders: Abhinav Krishna N
---

## Context

Phase 12a ports the reference satellite's WiFi
onboarding design: a node with no saved credentials brings up its own AP
(`EPM-SAT-<node_id>`) plus a captive portal, instead of blocking forever on
compiled-in credentials. That portal itself is Phase 12b's job — this ADR
only decides how its AP will be secured, since Phase 12b's implementation
needs the decision already made.

The reference firmware's own AP is **open** (no password). Its own design
doc calls this "a deliberate, deployment-scale simplification for a
transient, physically-supervised onboarding step" — defensible at a
2-satellite demo scale where whoever is provisioning a unit is standing
next to it and no one else is in range. This project's deployment context
may not stay that small, and an open AP during the provisioning window is
a real (if brief) window for an unrelated device to join, see the portal,
and submit garbage credentials or simply occupy the AP's single-client
slot before the intended operator gets to it.

Phase 12a's own prompt suggested one specific improvement: a per-device
password derived from the node's own MAC-derived `node_id` (already
computed today by `components/epm_drivers/link_mqtt.c`'s
`derive_node_id()` — last 3 STA MAC octets, lowercase hex, no separators),
readable off a physical label or the serial log by whoever is provisioning
the unit. This ADR does not adopt that specific approach — see Options
below for why — but does adopt WPA2 with a per-device password.

## Options considered

### Option A: open AP (match the reference design as-is)
No password, `WIFI_AUTH_OPEN`. Simplest; zero extra state or generation
step; matches an already-shipped, working design at another team's demo
scale.

Rejected: the tradeoff the reference design accepted (small fleet,
physically supervised, brief window) isn't guaranteed to hold as this
project's deployment grows, and the fix is cheap enough (see Option C)
that there's no real reason to inherit the weaker default.

### Option B: WPA2, password derived from `node_id` (the prompt's suggestion)
Password = some fixed function of `node_id` (e.g. `"epm-" + node_id`),
recomputed identically every boot — no extra NVS state, no generation
step, and readable by re-deriving it from the node_id printed on a label
or logged at boot.

Rejected on inspection, not just as "extra complexity not worth it": the
AP's SSID (`EPM-SAT-<node_id>`) already broadcasts `node_id` in the clear
to anyone in range — that's the whole point of the SSID, so provisioning
can find the right unit. If the password is a fixed, public function of
that same broadcast value, then the password is derivable by anyone who
can merely see the SSID, with no physical access to the device required
at all. A per-device password that can be computed from the AP's own
advertisement isn't a real access control — it just makes the AP look
password-protected. (A variant using more of the full MAC rather than the
3 octets shown in the SSID doesn't close this either: 802.11 broadcasts
the AP's MAC/BSSID in every beacon and association frame regardless of
what the SSID text shows, so the full MAC is also observable to anyone
running ordinary WiFi-sniffing tools, not just to someone standing next
to the unit. Any password that is a pure function of publicly-broadcast
802.11 fields inherits this weakness, independent of which specific
MAC-derived value or salt formula is used.)

### Option C: WPA2, password randomly generated once and persisted in NVS
On first entry into `PROVISIONING` with no AP password saved yet, generate
one via `esp_fill_random()` (a true RNG, not a formula), persist it in
NVS, and log it once at `ESP_LOGI` when the AP comes up. Every later
PROVISIONING entry reuses the saved password rather than regenerating it
(so the physical label — once written down at first bring-up — stays
correct for the unit's whole life; a reboot doesn't silently change the
door).

This has exactly the same operational cost as Option B for the person
provisioning: they need physical or serial access to the unit to read the
password either way (a label written once at bring-up, or a fresh serial
log line). It has none of Option B's weakness, because the password isn't
a function of anything transmitted over the air — an RF-range attacker
gains nothing by observing the SSID or the AP's MAC. The only new
mechanism required is one `esp_fill_random()` call plus one NVS key,
guarded by the same "only if nothing saved yet" pattern
`net_credentials_seed_defaults()` (Phase 12a Task 1) already establishes
for WiFi/MQTT credentials.

## Decision

**Option C.** Phase 12b's real `hal_provisioning` implementation should
bring the AP up as WPA2-PSK, not open, with a password generated once via
`esp_fill_random()` and persisted (a natural fit for a new key in the same
`epm_net`-style NVS pattern Task 1 established, or a small sibling
namespace — Phase 12b's own call), logged once at `ESP_LOGI` when the AP
starts so the person provisioning the unit can read it off the serial
console. `EPM-SAT-<node_id>` stays the SSID, since the SSID's job (letting
a human pick the right unit out of several nearby) doesn't require secrecy
the way the password does.

Phase 12a itself does not implement this: `provisioning_stub.c`'s
`hal_provisioning_start()` is a no-op, so there is no AP to secure yet.
This ADR exists so that decision doesn't have to be rediscovered — or
worse, silently defaulted to the reference design's open AP — when Phase
12b actually builds `esp_http_server` + the DNS responder.

## Consequences

**Positive:**
- Closes the "password derivable from the AP's own broadcast" flaw that
  Option B (the originally-suggested approach) would have shipped with.
- No new operational burden versus Option B: the physical/serial-access
  precondition for reading the password is identical either way.
- Reuses an already-established pattern (seed-only-if-nothing-saved) from
  Phase 12a's own `net_credentials_seed_defaults()`, so Phase 12b's
  implementation has a direct in-repo precedent to follow rather than
  inventing new NVS-write discipline.

**Negative / trade-offs:**
- Still not a defense against a genuinely capable, motivated attacker: a
  random WPA2 password stops opportunistic/casual joining and stops the
  broadcast-derivability flaw specifically, but WPA2-PSK itself remains
  crackable offline given a captured 4-way handshake and enough compute —
  the same caveat that applies to WPA2 anywhere else in this project. A
  provisioning window is short-lived and physically supervised either way
  (matching the reference design's own stated assumption), so this is
  judged acceptable for v1; WPA3-SAE or an out-of-band pairing code (QR/
  NFC) would close that gap further and is left as future work if the
  threat model ever requires it.
- No physical label exists yet as a manufacturing step in this project —
  "read the password off a label" is aspirational until an actual
  labeling process exists; until then, the serial log line is the only
  real distribution channel. That's a process gap, not a firmware one, and
  applies equally to the `node_id`-on-a-label idea the original prompt
  raised.
- One more piece of NVS state for Phase 12b to own and for a future
  factory-reset/re-provisioning flow to consider clearing (e.g. should
  erasing WiFi credentials also rotate the AP password?) — not answered
  here, left for Phase 12b to decide when that flow is actually built.

## Validation

None — this ADR is a decision record for Phase 12b's implementation, which
does not exist yet. No AP is brought up by Phase 12a's code
(`provisioning_stub.c` is a no-op), so there is nothing to validate on
hardware in this phase. Phase 12b's own validation should confirm: the AP
advertises WPA2-PSK (not open), the password differs between two
physically distinct units, and the password survives a reboot without
regenerating (reusing the persisted value, not the first-boot-only
generation path).
