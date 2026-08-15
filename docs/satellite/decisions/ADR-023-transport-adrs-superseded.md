---
id: ADR-023
title: TCP+AES transport ADRs superseded — MQTT is the sole transport
status: accepted
date: 2026-08-05
deciders: Abhinav Krishna N
---

## Context

`docs/decisions/ADR-011-mqtt-transport-added.md` added MQTT alongside the
existing raw-TCP+AES-GCM transport (`ADR-010-tcp-frame-protocol.md`'s framing
design, `ADR-007-aes-gdma-encryption.md`'s hardware-accelerated encryption)
as additive, non-breaking work — explicitly deferring the TCP path's removal
to "Phase 7 with its own ADR once the base station gateway is the confirmed
production path" (ADR-011). `docs/decisions/ADR-015-tcp-task-split-deferred.md`
made the same deferral for `tcp_task.c`'s WiFi/PM code split. That point has
now arrived: this session (Phase 7a) deletes
`tcp_task.c`, `tcp_task.h`, and `epm_protocol.h` outright
(`git rm`), and the WiFi STA lifecycle + power management code they also
carried moves to `src/threads/wifi_task.c`
(`docs/decisions/ADR-022-wifi-task-revived.md`). MQTT
(`components/epm_drivers/link_mqtt.c`, driven from `src/threads/net_task.c`)
is now this satellite's only transport.

## Decision

**Fleet interop wins over a bespoke protocol.** The base station this
satellite must join (an Uno Q running a fixed WiFi AP + Mosquitto broker +
Python ingestion pipeline, per ADR-011's context) is unmodifiable and speaks
MQTT with a section-list frame codec — not `ADR-010`'s 48-byte-header TCP
framing. Keeping the TCP+AES path alive bought nothing once MQTT was proven
byte-correct against the real pipeline
(`tests/host/decode_check.py`, cited in ADR-011's Validation section): it
was flash and RAM spent maintaining a protocol no consumer on the fleet
speaks, plus the ~28 KB of permanently reserved internal DRAM
(`ADR-007`'s `DMA_ATTR` staging buffers) and the CPU/complexity budget of a
GDMA-accelerated AES-GCM pipeline (`ADR-007`) that has no role once the
TCP framing it protected (`ADR-010`) is gone.

The following ADRs are marked `status: superseded` in their frontmatter
(append-only — content unchanged, not deleted, per this repo's ADR
convention):

- **`docs/decisions/ADR-007-aes-gdma-encryption.md`** — AES-GCM-128 via
  hardware accelerator + GDMA. Superseded because its subject, the TCP
  transport's encryption layer, no longer exists.
- **`docs/decisions/ADR-010-tcp-frame-protocol.md`** — TCP framing,
  MSG_MORE batching, keepalive tuning, `epm_header_t` wire format.
  Superseded because the wire format and transport it describes are
  deleted; MQTT + the section-list codec
  (`components/epm_codec/frame_codec/`) is the current wire format.
- **`docs/decisions/ADR-011-mqtt-transport-added.md`** — added MQTT
  *alongside* TCP. Superseded in the narrow sense that its "additive,
  TCP stays" framing no longer holds — MQTT is not "alongside" anything
  now, it is the only transport. Its findings (frame-shape requirements,
  the base station's `_validate_frame_bins` first-frame-commits behavior,
  the `epm_codec`/`epm_hal`/`epm_drivers` component split) remain accurate
  and load-bearing; only the "TCP path is untouched" framing is stale.

## Consequences

**Positive:**

- One transport to reason about, test, and carry through future phases —
  no more dual-transport RAM/flash tax (ADR-011's Validation section
  recorded 94.3% flash at the point both transports coexisted).
- `net_task.c` is now each producer queue's sole consumer, letting
  `ADR-021`'s second-queue split collapse back to one queue per producer
  (see `ADR-021`'s addendum).
- WiFi STA lifecycle and power management, previously entangled with the
  TCP transport inside `tcp_task.c`, now live in a focused
  `wifi_task.c` with no transport logic of its own (`ADR-022`).

**Negative / trade-offs — confidentiality regresses, not yet solved:**

- Dropping AES-GCM means telemetry now travels as MQTT QoS 0 plaintext
  over local WiFi to the broker (`link_mqtt.c`, per `ADR-011`). The TCP
  path's per-frame encryption (`ADR-007`) had no equivalent carried over —
  this is a genuine loss of confidentiality, not merely a deferred
  decision dressed up as one.
- The intended mitigation is broker-side TLS (MQTTS on port 8883, or an
  equivalent VPN/network-level control at the base station) — but this is
  **not implemented**. `link_mqtt.c` currently configures a plaintext
  `mqtt://` broker URL. Enabling TLS depends on the real Uno Q base
  station's broker configuration (cert provisioning, trust anchor), which
  is outside this satellite's repo and not yet available to test against
  (see `ADR-011`'s addenda on real-hardware validation being deferred).
  This is recorded here explicitly as an open item, not silently dropped.
- No other confidentiality control (payload-level encryption independent
  of transport, e.g.) is planned to replace AES-GCM; broker TLS is the
  only mitigation path under consideration.

## Validation

`grep -rn "tcp_connect\|encrypt_frame_data\|snapshot_send_tcp\|wifi_task_start\b" src/ components/` —
empty. `tests/host/` full suite passes. `pio run -e xiao_esp32s3` clean.
