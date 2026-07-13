# Project Progress

Living status doc for `edgeai-predictive-monitor`. Edit this as work progresses; paste it into new
Claude Code sessions to bring them up to speed.

## Why this repo exists

Built for the [Arduino Physical AI Challenge India 2026](https://robu.in/arduino-physical-ai-challenge-india-2026/).

The original implementation, `edgeai-predictive-monitor-unoq`, drove the UNO Q's MCU directly with a
raw Zephyr/west application (`west build -b arduino_uno_q`, flashed over OpenOCD via adb). That
bypasses the Arduino App Lab framework the competition is built around, which risked disqualification.
This repo (`edgeai-predictive-monitor`) is a from-scratch port that sticks to the App Lab app
structure (`app.yaml` + `python/` + `sketch/`) so everything builds/deploys the way the competition
expects.

Porting is happening step by step, MCU side first.

## Architecture

- **`base-station/`** — the Arduino UNO Q App Lab app.
  - `python/main.py` — Linux (QRB2210/MPU) side, runs via the App framework.
  - `sketch/sketch.ino` — MCU (STM32U5) side, Arduino API sketch built against the `arduino:zephyr`
    core (FQBN `arduino:zephyr:unoq`).
  - `deploy.sh` — pushes the app to the board over adb and (re)starts it via `arduino-app-cli`.
- **`satellite/`** — ESP32-S3 satellite node. Not started yet.

## Progress log

- **2026-07-13** — Ported the MCU heartbeat only from the old repo's
  [`mcu/src/main.c`](../../edgeai-predictive-monitor-unoq/mcu/src/main.c): `sketch/sketch.ino` blinks
  `LED_BUILTIN` on a 500ms period. Confirmed working on hardware via `adb push` +
  `arduino-app-cli app start`. Wrote `base-station/deploy.sh` to script that push/start/log cycle.
  Note: old code blinked a specific status LED (LED3 green, PH11) reached via a raw devicetree label —
  not accessible from a plain Arduino sketch, so this uses `LED_BUILTIN` (UNO Q's onboard RGB LED, red
  channel) as the equivalent "firmware is alive" indicator instead.

## Next up

Port the remaining threads from the old `main.c`, one at a time, onto the App Lab sketch/python split.
Old repo had these as separate Zephyr threads under `mcu/src/threads/`:

- [ ] `accel_sampler_thread` — accelerometer sampling
- [ ] `mic_sampler_thread` — microphone sampling
- [ ] `fuser_thread` — sensor fusion / inference
- [ ] `rgb_display_thread` — external WS2812 ring
- [ ] `matrix_display_thread` — LED matrix display
- [ ] `transport_thread` — communication (likely becomes the MCU <-> Python RPC link via
      `arduino-router`, or MCU <-> satellite link — TBD which)

Satellite (ESP32-S3) side hasn't been started at all yet — no code has been ported or written there.

## Reference

- Old repo (for logic/behavior reference only, not to be built as-is): `../edgeai-predictive-monitor-unoq`
- Old MCU entry point: `edgeai-predictive-monitor-unoq/mcu/src/main.c`
- App Lab app layout / CLI: apps live on-device under `/home/arduino/ArduinoApps/<app-name>`;
  managed with `arduino-app-cli app start|stop|logs <path>` (see `base-station/deploy.sh`).
