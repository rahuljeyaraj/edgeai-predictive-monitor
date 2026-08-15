# Pin Allocation — XIAO ESP32-S3

**Hardware:** Seeed Studio XIAO ESP32-S3 (ESP32-S3FH4R2)  
**Last updated:** 2026-08-07

---

## Allocated pins

| GPIO | Net name | Direction | Peripheral | Function | Notes |
|---|---|---|---|---|---|
| 0 | BOOT | Input | Strap | Boot mode select | FORBIDDEN for GPIO — hold LOW at reset = download mode |
| 1 | RGB_R | Output | LEDC_CH_0 | LED red channel | Common-cathode; active HIGH duty; LEDC timer 0 |
| 2 | I2S_BCLK | Output | I2S0 | Bit clock | 48 kHz sample rate, 32-bit I2S slot width (`MIC_SAMPLE_RATE_HZ`, `mic_inmp441_i2s.c`) |
| 3 | I2S_WS | Output | I2S0 | Word select (LRCLK) | 48 kHz frame rate |
| 4 | I2S_DIN | Input | I2S0 | Data in (mic → ESP32) | INMP441 DOUT |
| 5 | RGB_G | Output | LEDC_CH_1 | LED green channel | LEDC timer 0 |
| 6 | RGB_B | Output | LEDC_CH_2 | LED blue channel | LEDC timer 0 |
| 7 | SPI_SCLK | Output | SPI2 | SPI clock | KX134; 10 MHz (`accel_kx134_spi.c`'s `KX134_SPI_CLOCK_HZ`) |
| 8 | SPI_MISO | Input | SPI2 | SPI data in | KX134 acceleration data reads |
| 9 | SPI_MOSI | Output | SPI2 | SPI data out | KX134 command/config writes |
| 43 | SPI_CS | Output | SPI2 | Chip select | KX134; active LOW. Was UART0 TX, but the debug console runs over USB-JTAG (`esp-builtin`), leaving this pin free (`components/epm_drivers/accel_kx134_spi.c:61`, `KX134_PIN_CS`) |
| 44 | KX134_INT1 | Input | GPIO | Accelerometer interrupt 1 | Currently unused by firmware; wired but not read. Was UART0 RX, freed for the same reason as GPIO43 (`accel_kx134_spi.c:62`, `KX134_PIN_INT1`) |

---

## Retired pins

| GPIO | Former function | Retired | Replacement |
|---|---|---|---|
| 21 | LED indicator (led_task) | 2026-06-28 | GPIO1/5/6 (rgb_led_task) |

GPIO21 is now floating/unused. There is no pull configuration on this pin; it should be left disconnected or pulled to a defined state externally if board layout requires it.

---

## Free / unallocated GPIOs

| GPIO | Status | Notes |
|---|---|---|
| 10 | Free | Not used by the current SPI pinout |
| 11–20 | Free | Available for future expansion |
| 21 | Free (retired) | Was old LED — now unallocated |
| 36–42, 45–48 | Free | High-numbered GPIOs; some have input-only restrictions on ESP32-S3 |

---

## LEDC channel allocation

| Channel | GPIO | Color | Timer |
|---|---|---|---|
| LEDC_CH_0 | 1 | Red | LEDC_TIMER_0 |
| LEDC_CH_1 | 5 | Green | LEDC_TIMER_0 |
| LEDC_CH_2 | 6 | Blue | LEDC_TIMER_0 |
| CH_3 – CH_7 | — | Unallocated | — |

---

## GPIO selection rationale

GPIO1/5/6 selected for RGB LED because:
1. GPIO0 is a boot strap pin — any output on GPIO0 risks download-mode entry on reset
2. GPIO2/3/4 are I2S (cannot be reassigned without breaking mic capture)
3. GPIO7/8/9 are used for SPI (KX134 IMU, real hardware by default), and GPIO43/44
   for its CS/INT1 — free because the debug console runs over USB-JTAG rather than
   physical UART0 pins
4. GPIO10 is free — not used by the current SPI pinout
5. GPIO1/5/6 are the lowest-numbered free GPIOs that are full-featured (not input-only)
6. All three are confirmed LEDC-capable on ESP32-S3 (all GPIOs support LEDC output via GPIO matrix)

---

## INMP441 microphone wiring

| INMP441 pin | ESP32-S3 GPIO | Signal |
|---|---|---|
| WS | 3 | I2S_WS (LRCLK) |
| SCK | 2 | I2S_BCLK |
| SD | 4 | I2S_DIN |
| L/R | GND | Left channel select (L/R = LOW → left channel) |
| VDD | 3V3 | Power |
| GND | GND | Ground |

I2S configuration: standard Philips mode, 32-bit I2S slot width with the INMP441/ICS-43434's 24-bit samples left-justified within the slot, 48 kHz sample rate, mono (`I2S_SLOT_MODE_MONO`; L/R tied to GND selects the left channel). A capture call returns 1024-sample blocks (`MIC_RAW_BLOCK_SAMPLES`); internally the DMA driver chunks that into two 512-sample descriptors (`dma_frame_num = MIC_RAW_BLOCK_SAMPLES / 2`) to stay under the ESP32-S3's 4092-byte-per-descriptor limit. See `components/epm_drivers/mic_inmp441_i2s.c`.
