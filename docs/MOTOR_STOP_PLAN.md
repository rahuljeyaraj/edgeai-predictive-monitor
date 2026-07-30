# Plan — AI-triggered motor stop (physical AI action)

Status: **STALE — superseded and built.** This design-only doc was
superseded by a locked "machinery protection trip" plan (API 670 framing,
not motor control; see `docs/progress4.md`'s intro and the
`machinery-protection-trip-plan` memory) that was actually implemented and
committed as `ebc08f4`. The rejected/superseded parts of this doc (the
operator-typed topic string in particular) are kept below for history, but
do not re-propose them. For current status, open issues, and live-hardware
findings, see [progress4.md](progress4.md). Companion to
[motor-driver.md](motor-driver.md).

## Why

The contest requires "physical AI": inference has to trigger a real-world
action, not just update a dashboard/LED/Telegram alert. Today
`motor-driver/` (the vibration-source stepper motors) is a fully
standalone Arduino Uno, controlled only by a human running
`motor-driver/run_demo.py` (or `dashboard.html`) from a host laptop over USB
serial. Nothing connects it to the base station's inference pipeline.

The host laptop already IS the motor's controller — it has the stepper
rig's Uno on USB and already runs `run_demo.py`. So the base station doesn't
need a direct wire to the rig; it just needs to tell that host machine
"stop" over the network. The base station already has a working MQTT
command channel built for exactly this shape of problem (pushing a command
out to a node), so this plan reuses it instead of inventing anything new.

No firmware changes anywhere. No new host-bridge daemon. No new hardware.

## Design

Reuse the existing base-station → node MQTT command channel
(`base-station/python/ingestion/mqtt_publisher.py`, topic `epm/<node_id>/cmd`,
`[TYPE:1B][PAYLOAD]` envelope in `common/wire_protocol.py`) — currently the
only command it carries is `STATUS_LED`; its own docstring says "extend this
as more commands are implemented." Add a `MOTOR_STOP` command the same way,
addressed to a pseudo node-id like `"motor_rig"` (not a real registry entry,
just an MQTT topic target). A new small script on the host laptop subscribes
to that topic and calls the stepper rig's existing serial stop command
(`d`, already implemented in `motor-driver/src/main.cpp` and already wrapped
by `run_demo.py`'s `Rig.disable()`).

```
InferencePipeline (base station) --FAULT--> Registry.on_status_change
   --> wire_motor_stop_publishing() --> MQTT epm/motor_rig/cmd
      --> (LAN) --> motor_stop_listener.py on host laptop
         --> pyserial 'd' --> motor-driver firmware --> EN_PIN HIGH (drivers off)
```

### Tying a dashboard asset to a motor

The dashboard already models each monitored sensor as an "asset" — a
`RegistryEntry` (`base-station/python/registry/registry.py:81`) with
`node_id`, `device_type` ("Asset Class"), a nickname, `status`, etc., each
settable through its own method (`set_device_type`, `rename`, ...). Rather
than hardcoding "the base station's own node controls the one demo rig,"
give each asset an optional, operator-set link to a physical stop target:

- New field `RegistryEntry.motor_stop_topic: Optional[str] = None` — unset
  by default, so assets with no physical actuator are unaffected.
- New `Registry.set_motor_stop_topic(node_id, topic: Optional[str])`,
  mirroring `set_device_type` (`registry.py:377`).
- A small REST endpoint (alongside the existing rename/set-device-type
  endpoints in `api/`) + a field in the asset's settings/expanded panel —
  e.g. "Physical stop target" — so an operator can type `motor_rig` for
  whichever asset is the one wired to `motor-driver/`. Nothing else in
  the dashboard changes; unset stays the default for every other asset.
- `wire_motor_stop_publishing` (below) then generalizes: on transition to
  `FAULT`, if `registry.get(node_id).motor_stop_topic` is set, publish
  `MOTOR_STOP` to `epm/{motor_stop_topic}/cmd` — not hardcoded to
  `BASE_STATION_NODE_ID`. For the contest demo, only one asset (the base
  station's own node, since its SPI-connected accelerometer is the one
  physically mounted on the rig) would have `motor_stop_topic = "motor_rig"`
  set — but the mechanism supports any number of assets each pointing at
  their own actuator/listener later.

This is a deliberate revival of an idea the registry already tried once:
`RegistryEntry` used to carry `control_circuit_id`/`auto_cutoff_enabled`
fields (`registry.py:142-143` still pops them for backward-compat parsing of
old `registry.json` files) — added speculatively, never wired to any real
output, and deleted as dead code in a later redesign. This plan is that same
idea, actually wired end-to-end instead of left speculative.

### Resume is manual, by design

Once the rig stops, there's no more vibration, so
`InferencePipeline.handle_frame()` short-circuits at the `MotorStateGate`
check (`pipeline/inference.py:117`,
`if self._gate.update(frame) != MotorState.RUNNING: return None`) — scoring
simply stops, so status can never auto-recover to HEALTHY on its own. A
human has to physically/serially restart the rig (existing `e` / `b <rpm>`
commands, or `dashboard.html`) — this is a real constraint from how the
pipeline works, not a preference call to build a workaround for.

## Surfaces to touch (future implementation)

**`base-station/python/registry/registry.py`**
- Add `motor_stop_topic: Optional[str] = None` to `RegistryEntry`.
- Add `Registry.set_motor_stop_topic(node_id, topic)`, mirroring
  `set_device_type` (~line 377).

**`base-station/python/api/`** (wherever rename/set-device-type REST routes
live)
- New endpoint to set/clear an asset's `motor_stop_topic`.

**`base-station/python/frontend/`**
- One optional field in the asset settings/expanded panel: "Physical stop
  target" — text input, empty by default, calls the new endpoint.

**`base-station/python/common/wire_protocol.py`**
- Add `MOTOR_STOP = 0x09` to `MqttMsgType`.
- Add `encode_motor_stop_payload(stop: bool) -> bytes` /
  `decode_motor_stop_payload(...)`, mirroring
  `encode_display_rgb_payload`/`decode_display_rgb_payload` (1-byte bool).

**`base-station/python/ingestion/mqtt_publisher.py`**
- Add `publish_motor_stop(self, node_id: str, stop: bool) -> None`,
  mirroring `publish_status()`: encode, publish to
  `CMD_TOPIC_FMT.format(node_id=node_id)`.

**`base-station/python/main.py`**
- Add `wire_motor_stop_publishing(registry, host, port)`, a sibling of
  `wire_status_led_publishing` (~line 92): on `registry.on_status_change`,
  look up `registry.get(node_id).motor_stop_topic`; if set and
  `status == NodeStatus.FAULT`, call
  `publisher.publish_motor_stop(topic, True)`.
- Call it next to the existing `wire_status_led_publishing(...)` call
  (~line 404), same `if args.mqtt_host:` gate — this feature only works when
  MQTT is enabled, same as satellite ingestion already requires.

**`motor-driver/motor_stop_listener.py`** (new)
- Standalone script for the host laptop, next to `run_demo.py`. Takes
  `--port` (stepper rig's serial port) and `--mqtt-host` (base station's LAN
  IP) args.
- `paho.mqtt.client` subscriber on `epm/motor_rig/cmd` (same lib already
  used base-station-side; `pip install paho-mqtt` on the laptop if not
  present).
- On a `MOTOR_STOP` message with `stop=True`: call `.disable()` on a `Rig`
  instance (reuse the class already in `run_demo.py` — import it directly,
  `motor-driver/` is one folder, no packaging needed).
- Decodes the 2-byte envelope locally (doesn't import
  `base-station/python/common/wire_protocol.py` — that package deploys to
  the UNO Q container, a different machine/filesystem entirely; a short
  comment pointing back at `MqttMsgType.MOTOR_STOP` as the source of truth
  is enough, same as how the satellite firmware already independently
  re-implements wire formats in C++ without sharing code with the Python
  side).

**`docs/motor-driver.md`**
- Note the deliberate, narrow exception to its own "keep the rig's control
  path independent" recommendation (§1): one one-way safety-stop signal
  only, no RPM/speed coupling, added for the contest's physical-AI
  requirement.

## Prerequisite to verify first

Per project history, the mosquitto broker on the UNO Q ("lives on the board
itself, not the dev machine") was set up but **not yet confirmed working
live** as of the last MQTT LAN migration work. Before implementing, confirm
from the host laptop:
```
mosquitto_pub -h <uno-q-lan-ip> -p 1883 -t epm/test/cmd -m hello
```
actually reaches a running broker. If it doesn't, that's a blocking
prerequisite independent of this plan.

## Verification plan (once implemented)

1. Unit-level: encode/decode round-trip for the new `MOTOR_STOP` payload
   (mirrors any existing `wire_protocol` test, if present).
2. Manual publish test: `mosquitto_pub` a raw `MOTOR_STOP` byte pair to
   `epm/motor_rig/cmd`, confirm `motor_stop_listener.py` receives it and
   sends `d` (watch its own print/log, and the rig's serial console for the
   `[status] drivers=OFF` line).
3. End-to-end on hardware: run the rig at baseline RPM via `run_demo.py` or
   `dashboard.html`, drive the base station's own node into FAULT (real
   induced anomaly, or existing test/capture tooling), confirm the physical
   motors stop within about one debounce window, and confirm they stay
   stopped (no auto-restart) until a manual `e`/`b <rpm>` is sent.
4. No existing automated suite covers MQTT-hardware integration — this is a
   live smoke test, same as other hardware work in this repo's history.
