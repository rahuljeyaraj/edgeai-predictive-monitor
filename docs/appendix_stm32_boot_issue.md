# Appendix: STM32U585 Boot-After-Flash Issue (Requires Power Cycle)

**Board:** Arduino UNO Q (STM32U585 MCU + Qualcomm QRB2210 MPU)
**Firmware:** Zephyr RTOS
**Status:** Resolved
**Affects:** Anyone flashing the STM32U585 over SWD via the QRB2210-internal OpenOCD bridge

---

## 1. Background: how this board's debug access works

Before describing the bug, it's worth explaining why this board behaves differently from a normal STM32 dev board, because the root cause and the fix both depend on this.

On most STM32 boards, the SWD pins (SWCLK, SWDIO) are broken out to a header, and you connect an external debug probe (ST-Link, J-Link, etc.) directly to the chip. The UNO Q does not do this. The STM32U585's SWD pins are **not** exposed on any external header. Instead, they are wired internally to GPIO pins on the QRB2210 (the Linux-side processor), and OpenOCD runs *on the QRB2210 itself*, bit-banging SWD over those GPIOs using the `linuxgpiod` driver. A developer's PC reaches this OpenOCD instance indirectly, over `adb`, by running commands inside the Linux side's shell.

This matters because it rules out an entire category of "obvious" fixes (like "just use an external programmer") that would work on a normal board but don't apply here at all — there is no electrical path for an external probe to reach this chip.

The flashing command used throughout this debugging session was:

```bash
adb shell "cd /opt/openocd && ./bin/openocd -f openocd_gpiod.cfg -c 'program /tmp/zephyr.elf verify reset exit'"
```

This pushes a compiled Zephyr ELF to the board (via a prior `adb push`), then tells OpenOCD to program it, verify it, issue a reset, and exit.

---

## 2. The symptom

After flashing a Zephyr "blinky" firmware build, the LED did **not** start blinking. The flash itself reported success — OpenOCD verified the write — but nothing ran.

The only way to get the firmware running was to physically unplug and replug the board's power. After a power cycle, the same firmware ran immediately.

This was a regression: earlier blinky builds, before any UART configuration work, had flashed and run immediately with no power cycle required. Something had changed in between, or some assumption that used to hold was no longer holding — that distinction matters for diagnosis, and is addressed below.

---

## 3. Why this matters: STM32 boot mode basics

To understand the root cause, you need to understand how an STM32 microcontroller decides *where to start executing code* every time it resets.

Every STM32 has multiple possible "boot sources" — most relevantly:

- **Main flash memory** (where your actual application lives, conventionally starting at address `0x08000000`)
- **System memory / ROM bootloader** (a fixed piece of code burned in by ST at the factory, used for initial programming over UART/USB/etc. — on the STM32U585 this lives at `0x0BF90000`)

Which one the chip jumps to is decided by a piece of hardware logic that runs very early in the boot sequence, before any of your code executes. That decision depends on:

- The electrical level of the **BOOT0 pin** at the moment of boot (high or low)
- A set of persistent configuration bits called **option bytes**, stored in a special flash region separate from your application code. The relevant ones here are `nBOOT0` and `nSWBOOT0`.

The behavior, in plain terms:

- If `nSWBOOT0 = 1` (the typical factory-default state): the chip looks at the **physical BOOT0 pin**. If BOOT0 reads low, it boots from main flash (your firmware). If BOOT0 reads high, it boots from the ROM bootloader instead.
- If `nSWBOOT0 = 0`: the physical BOOT0 pin is ignored entirely, and the chip instead uses the value baked into the `nBOOT0` option byte to make the same decision.

Critically: **the BOOT0 pin's electrical state is only sampled at power-on.** A software-triggered reset, or a debugger-issued hardware reset over SWD (the kind OpenOCD issues after flashing), does **not** re-sample this pin the same way. On this chip family, the pin's level is latched once, early in the power-on sequence, and resets that don't involve actually losing and restoring power don't redo that latch.

This single fact explains the entire symptom: a power cycle freshly samples BOOT0 and gets the correct answer; every other kind of reset (SWD soft reset, SWD hardware SRST, the `reset` step OpenOCD runs after programming) does not, and falls back to whatever was latched before — which, in this case, was the wrong source.

---

## 4. Diagnosis

### 4.1 Confirming where the chip was actually jumping to

The Cortex-M33 core (the CPU inside the STM32U585) has a register called **VTOR** (Vector Table Offset Register), which points to the address of the vector table the CPU is using — in practice, this tells you where the CPU believes its code lives. Reading this register after a flash+reset tells you, definitively, where the chip actually booted from.

```bash
adb shell "cd /opt/openocd && ./bin/openocd -f openocd_gpiod.cfg -c 'init' -c 'mdw 0xE000ED08' -c 'exit'"
```

(`mdw` = "memory display word"; `0xE000ED08` is the fixed system address of VTOR on any Cortex-M33.)

**Result before the fix:** `0xBF90000`

This is — once you account for OpenOCD's output dropping a leading zero in the printed value — the same address as the STM32U585's documented ROM bootloader entry point, `0x0BF90000`. In other words: after every SWD-triggered reset, the chip was jumping straight into ST's factory bootloader, not into the flashed Zephyr application. The application was sitting correctly in flash at `0x08000000` the whole time — it just was never being reached.

### 4.2 Ruling out reset method as the variable

It was possible that some specific reset mechanism (software reset vs. a proper hardware reset via the SRST line) was the issue, rather than BOOT0 sampling generally. This was tested directly:

```
reset_config srst_only srst_nogate
reset run
```

This forces OpenOCD to use a hardware SRST-line reset rather than a software-triggered one. VTOR was re-read afterward and still showed `0xBF90000`. This ruled out "wrong reset type" as the cause — the behavior was identical regardless of which reset mechanism was used, which is consistent with the explanation above: *no* reset short of a real power cycle re-samples BOOT0 on this chip.

### 4.3 Ruling out the UART work as the cause

Because the regression appeared sometime after UART overlay changes were made (adding `hw-flow-control`, setting `current-speed`, redirecting the `chosen` console to `lpuart1`), it was reasonable to suspect those changes. This was tested by removing each overlay change individually, and then by emptying the devicetree overlay entirely. The symptom persisted in every case — including with no overlay at all. This ruled out the UART work as the cause. (The actual explanation for why the regression *appeared* around that time is presumably that some other change altered behavior right alongside it; root-causing that further is moot, since the issue lives in chip-level boot configuration, not in the UART overlay.)

### 4.4 Attempting a software fix via option bytes — and why it was blocked

The "correct" software fix for this class of problem is to **write the option bytes** so the chip ignores the BOOT0 pin's power-on-only sampling altogether and boots from main flash unconditionally, regardless of reset type. This means setting `nSWBOOT0 = 0` together with the `nBOOT0` bit forced to "boot from main flash" — at which point the BOOT0 pin's physical state stops mattering at all, on any kind of reset.

This requires writing to `FLASH_OPTR`, the flash option control register, at address `0x40023800`. Attempting this directly returned **"Failed to read memory."**

The reason is **TrustZone**. The STM32U585 implements Arm TrustZone, which partitions the chip into a Secure state and a Non-Secure state. Certain registers — including the option bytes register — are only accessible from the Secure state. The OpenOCD connection in this setup was confirmed to be in the **Non-Secure** state, and:

- The board's OpenOCD target configuration file (`stm32u5x.cfg`) only defines Non-Secure targets (`stm32u5.ap0`, `stm32u5.cpu`) — there is no Secure-state target configured at all.
- The installed OpenOCD build does not support the `cortex_m_security_state` command needed to switch states even if a Secure target existed.

In short: there was no available software path, with the tooling on hand, to reach the registers needed to apply the "proper" fix. This wasn't a matter of running the wrong command — the access path itself didn't exist.

### 4.5 Why an external programmer wasn't an option

A natural next thought is to reach for an external SWD probe (ST-Link, J-Link) and a tool with full TrustZone support, like ST's own STM32CubeProgrammer, which does support Secure-state option byte writes. This was investigated and ruled out for two independent reasons:

1. **No probe was available** at the time.
2. Even with a probe, it **would not have helped on this specific board**: the STM32U585's SWD lines are not broken out to any external header on the UNO Q. They are wired internally to QRB2210 GPIOs and are only reachable via the OpenOCD instance running inside the board's own Linux side. There is no electrical point to clip an external probe onto. This also ruled out running STM32CubeProgrammer on the QRB2210 itself (even via x86 emulation, which was separately confirmed to not be officially supported for this chip's architecture) — CubeProgrammer expects to talk to a USB-attached probe, and no such device exists on this board's SWD path.

### 4.6 Why a factory reset wasn't the answer

It was proposed that an Arduino factory reset (via the Arduino Flasher CLI, which reflashes the board over a different mode entered by shorting pins on the JCTL header) might clear the issue. This was correctly anticipated to **not** help, and that was confirmed: the Flasher CLI reflashes the **Linux image on the QRB2210's eMMC storage** — the Debian OS side of the board. It has no interaction whatsoever with the STM32U585's flash, option bytes, or boot configuration. The STM32 side and its boot misconfiguration are completely outside what a factory reset touches.

---

## 5. The fix: a hardware BOOT0 jumper

With every software path closed off, the fix applied was **physical** rather than software-based — and turned out to be the cleaner solution, not just a workaround.

### 5.1 The idea

Recall from Section 3: the chip's boot-source decision (when `nSWBOOT0 = 1`, the relevant case here) depends on the BOOT0 pin's level, and the *problem* is that this level is only sampled reliably at power-on. If the BOOT0 pin's level is fixed in hardware — rather than left floating or dependent on some other circuit's power-on state — then it reads the same way on *every* boot, power-on or otherwise, because there's nothing time-dependent left for it to sample differently. This sidesteps the option-bytes problem entirely: you don't need to change how the chip *decides* using BOOT0, you just need to make sure BOOT0 always says the same thing.

### 5.2 Where BOOT0 is physically accessible

On this board, the BOOT0 net (`MCU_BOOT0`) is exposed at **pin 1 of the JANALOG header** — this is the only external, physical access point to this signal on the whole board.

### 5.3 What was done

A jumper wire was connected from **JANALOG pin 1 to GND**, permanently tying BOOT0 low.

Per the boot logic in Section 3: BOOT0 low → boot from main flash (`0x08000000`) → the application. This now holds true on every reset type, including the SWD-triggered reset OpenOCD issues after every `program ... verify reset exit` command, because the pin's physical level no longer depends on anything that only happens at power-on — it's just permanently grounded.

### 5.4 Trade-off to be aware of

This jumper permanently forces "boot from flash." If the ROM bootloader is ever needed again in the future (for example, a recovery scenario where the flash is corrupted, or a different programming method that specifically requires bootloader mode), the jumper will need to be temporarily removed first. This is a reasonable trade-off for normal development.

---

## 6. Verification

The fix was verified at two levels: behavioral (does the LED actually blink) and register-level (is VTOR actually pointing where it should).

**Step 1 — Flash normally, no power cycle:**
```bash
adb shell "cd /opt/openocd && ./bin/openocd -f openocd_gpiod.cfg -c 'program /tmp/zephyr.elf verify reset exit'"
```
Result: LED began blinking immediately after the flash command completed, with no power cycle.

**Step 2 — Confirm at the register level, without reflashing:**
```bash
adb shell "cd /opt/openocd && ./bin/openocd -f openocd_gpiod.cfg -c 'init' -c 'mdw 0xE000ED08' -c 'exit'"
```
Result:
```
0xe000ed08: 08000000
```
VTOR now reads `0x08000000` — the application's vector table in main flash — instead of the bootloader address seen before the fix. This confirms the chip is now booting into the application unconditionally, not just that the previously-flashed firmware happened to still be running.

Both checks passing together confirms the fix is solid: the chip reliably boots into application flash on any reset, not only on power-on.

---

## 7. Summary

| | |
|---|---|
| **Symptom** | Firmware doesn't start after SWD flash; only starts after a full power cycle |
| **Root cause** | STM32U585 only samples the BOOT0 pin at power-on; SWD-triggered resets (including the one OpenOCD issues after flashing) don't re-sample it, so the chip kept booting into the ROM bootloader instead of the application |
| **Confirmed via** | Reading VTOR (`0xE000ED08`) after flash+reset — found pointing at the bootloader address (`0xBF90000`) instead of application flash (`0x08000000`) |
| **Ruled out** | Reset mechanism (soft vs. hardware SRST — both showed identical behavior); UART devicetree overlay changes (removed entirely, no change); factory reset (only affects the Linux/QRB2210 side, not STM32 boot config) |
| **Blocked path** | Writing option bytes (`nSWBOOT0`/`nBOOT0`) via OpenOCD — blocked by TrustZone Secure-state access, which neither the board's OpenOCD target config nor the installed OpenOCD build supports; external probe + STM32CubeProgrammer not viable either, since this board's SWD lines aren't exposed on any header |
| **Fix applied** | Permanent hardware jumper: JANALOG pin 1 (`MCU_BOOT0`) tied to GND, forcing BOOT0 low on every boot |
| **Trade-off** | ROM bootloader mode now requires temporarily removing the jumper if ever needed again |
| **Verified by** | LED blinks immediately post-flash (no power cycle); VTOR confirmed reading `0x08000000` after a soft reconnect with no reflash |
