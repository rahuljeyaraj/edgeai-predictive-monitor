# EdgeAI Predictive Monitor

### Sensors that watch. An AI that decides. A hand that pulls the plug.

A sensor pod that clips onto a machine, learns what *that* machine's healthy
vibration and sound are, and — when it is confident enough that something is
going wrong — **stops the motor**. Not an email. The motor.

Built on the **Arduino UNO Q**: real-time sampling and FFTs on the STM32U585
side, and on the Qualcomm Dragonwing QRB2210 Linux side, on-device PyTorch
training, an Edge Impulse fault classifier, a live dashboard, an MQTT broker,
and the decision to trip.

![System at a glance](report/diagrams/01-system-at-a-glance.png)

---

## Start here

**[📄 Read the full report →](report/REPORT.md)** — the design, the measured
results, the dead ends, the bill of materials, the schematics, and a
command-by-command build guide. It stands on its own; everything below is a
shortcut into it.

| I want to… | Go to |
|---|---|
| See it running, **with no hardware at all** | [Report → Appendix C.1](report/REPORT.md#appendix-c-build-one-yourself) — 10 minutes, one script |
| Understand what it does and why | [Report → Chapter 1](report/REPORT.md#chapter-1-the-machine-that-never-complains-until-its-too-late) |
| See the part that makes it *Physical AI* | [Report → Chapter 8](report/REPORT.md#chapter-8-the-day-it-stopped-itself) |
| Buy the parts | [Report → Appendix A](report/REPORT.md#appendix-a-bill-of-materials) — ≈ ₹8,115 for one machine |
| Wire it up | [Report → Appendix B](report/REPORT.md#appendix-b-wiring-and-pinout-reference) + [`hardware/kicad/`](hardware/kicad/) |
| Read the code | [Report → Appendix L](report/REPORT.md#appendix-l-reading-the-source) |

## Try it without buying anything

Runs the **real** dashboard on your laptop, fed by a simulator speaking the
**real** wire protocol, replaying **real** captured sensor data. Not a mock —
the registry, feature pipeline, autoencoder, setup flow, thresholds and
classifier are the same code that runs on the board.

```sh
sudo apt-get install -y mosquitto mosquitto-clients   # broker on :1883
sudo systemctl enable --now mosquitto

cd base-station
./start_desktop_dashboard.sh
```

It prints two URLs: the dashboard, and the simulated node's own control page.

## What it does

- **Learns each machine individually.** A short guided setup — name it, measure
  it switched off, run it, train — and that machine has its own model and its
  own thresholds. Training happens on the UNO Q, in seconds. No cloud.
- **Knows when a machine is simply off.** A running/stopped gate calibrated
  against each sensor's own measured noise floor, which turned out to be the
  hardest measurement in the project.
- **Names the fault.** A second model, trained in Edge Impulse and run on-device
  as TFLite, says *bearing* / *imbalance* / *loose mount* — one model per
  machine *type*, so five identical lathes share one.
- **Stops the motor.** On a sustained fault: a visible ten-second countdown with
  a Hold button, then a per-motor stop that latches until a human clears it. If
  the machine keeps turning, it reports the trip as **failed** rather than
  claiming success.
- **Tells someone.** A status ring on the machine, the board's own LED matrix,
  a five-tab live dashboard, and a Telegram alert.
- **Scales over Wi-Fi.** Satellite nodes (XIAO ESP32-S3) join by captive portal
  from a phone — no app, no recompile, no ID to type.

## Repository map

| Path | What's in it |
|---|---|
| [`base-station/`](base-station/) | The UNO Q: Zephyr firmware (`sketch/`), the Linux application (`python/`), host bridges (`host/`), 34 test modules (`tests/`) |
| [`satellite/`](satellite/) | XIAO ESP32-S3 wireless sensor node (PlatformIO) |
| [`motor-driver/`](motor-driver/) | The validation rig: Arduino Uno + CNC Shield firmware, the rig host, and its control page |
| [`hardware/kicad/`](hardware/kicad/) | Three real KiCad schematics, generated from Python |
| [`report/`](report/) | The full report and the generators for every diagram in it |
| [`3d-models/`](3d-models/) | Thirteen printable parts: node enclosures and the validation rig, as 3MF and STL |
| [`docs/BILL_OF_MATERIALS.md`](docs/BILL_OF_MATERIALS.md) | The canonical parts list: what to buy, per build scale, with links |
| [`docs/BUILD_GUIDE.md`](docs/BUILD_GUIDE.md) | Build one yourself, from printed shell to a commissioned machine |
| [`docs/`](docs/) | Design plans and investigation records — the long-form version of the report's appendices |

## Status

| | |
|---|---|
| Sensing, guided setup, per-machine anomaly model | Live-verified on hardware |
| Running/stopped gate with measured baseline | Live-verified on hardware |
| Physical motor trip: stop + latch + confirm | Live-verified on hardware, both directions |
| Dashboard, status ring, LED matrix, Wi-Fi onboarding | Live-verified on hardware |
| Edge Impulse classifier, on-device | Built; trained on 541 real captures from this rig |
| Satellite nodes | Built |
| Telegram alerts | Built and demonstrated; off pending one config value |
| Per-motor relay (cutting power, not just motion) | Not built |

The report's [Chapter 12](report/REPORT.md#chapter-12-proof-not-promises) has
every measured number, and
[Appendix J](report/REPORT.md#appendix-j-test-suite-and-verification-record)
records how each was checked.

## Safety

This is a condition-monitoring system with a protective trip. **It is not a
certified functional-safety system** and must not be used as a guard interlock —
it has no safety integrity level, no redundant channel, and if the base station
loses power nothing trips. It stops *motion*, not power. Every safety function a
machine already has stays exactly where it is. See
[Report → §8.4](report/REPORT.md#chapter-8-the-day-it-stopped-itself).

## Built for

- **Arduino Physical AI Challenge India 2026** — Industrial & Sustainability AI
- **Invent the Future with Arduino UNO Q and App Lab** (Hackster.io) —
  Industrial IoT

## Licence

[MIT](LICENSE).
