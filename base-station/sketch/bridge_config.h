/*
 * Shared Bridge (Arduino_RouterBridge) configuration for every module that
 * brings the link up.
 *
 * BRIDGE_BAUD is the MCU<->MPU serial baud (Serial1 <-> /dev/ttyHS1). It MUST
 * match the router's --serial-baudrate on the Linux side, set in the systemd
 * drop-in (see base-station/provision-baud.sh + docs/PROGRESS.md) - a mismatch
 * silently breaks the whole link. Raised from the library default 115200
 * because the fuser pushes the full-resolution float32 spectrum (~64 KB/s at
 * the old repo's 15.6 Hz), which 115200 (~11.5 KB/s) cannot carry.
 *
 * Why 1000000 specifically - it satisfies two independent constraints at once:
 *   1. EXACT divisor. 1000000 = 16MHz Serial1 kernel clock / 16 exactly, so the
 *      STM32 UART baud is precise. 921600 is NOT an exact divisor here; when it
 *      was tried the MCU went completely silent even on a clean reboot (the
 *      core's Serial1 doesn't realize the needed fractional divider accurately,
 *      so the baud lands far enough off to break the link). Stick to
 *      exact-divisor rates (1000000, 2000000, ...); do NOT assume a "standard"
 *      rate like 921600/115200 is safe on the MCU side.
 *   2. OVER16 RX margin. At <=1MHz the STM32 USART uses 16x oversampling; at
 *      ~2MHz it drops to OVER8, whose thinner RX sampling margin made the
 *      router->MCU direction unreliable. At 2000000, MCU->MPU notifies streamed
 *      perfectly but round-trip Bridge.provide() registrations and MPU->MCU
 *      calls intermittently failed ("method not available") - the MCU wasn't
 *      reliably receiving the router's responses. That breaks the LED
 *      matrix/RGB providers and the sampler info calls, not just tests.
 *      1000000 keeps OVER16 and fixed it, while still carrying the full-res
 *      frame at 15.6 Hz (~41ms send inside the 64ms epoch, ~64% link util).
 *
 * Bridge.begin(BRIDGE_BAUD) is idempotent - only the first caller's baud
 * actually takes effect (subsequent calls return early) - but every module
 * passes BRIDGE_BAUD so the effective baud doesn't depend on setup() ordering.
 */
#pragma once

#define BRIDGE_BAUD 1000000
