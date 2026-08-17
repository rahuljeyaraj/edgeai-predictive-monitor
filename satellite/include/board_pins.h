#ifndef BOARD_PINS_H_
#define BOARD_PINS_H_

#include <Arduino.h>

/*
 * Seeed Studio XIAO ESP32S3 pin map for this satellite node - the
 * Arduino-side equivalent of mcu/boards/arduino_uno_q.overlay's role
 * (that file documents the STM32U585's pin assignments for the same set
 * of peripherals: KX134 accel over SPI, INMP441 mic over I2S, WS2812
 * status ring). D0-D10 numbering and their underlying GPIOn mapping are
 * from framework-arduinoespressif32's variants/XIAO_ESP32S3/pins_arduino.h
 * (D0=GPIO1 .. D5=GPIO6, D6=GPIO43, D7=GPIO44, D8=GPIO7, D9=GPIO8,
 * D10=GPIO9) - not guessed from the silkscreen.
 *
 * Only 11 GPIOs (D0-D10) are broken out on this board, so every pin below
 * is deliberately chosen to avoid the hardware SPI bus's fixed pins
 * (D8/D9/D10, set by the variant, not reassignable without bit-banging
 * SPI instead). The KX134 breakout labels those three pins SCL/ADR/SDA
 * (it's a dual-protocol part; those are its I2C-mode pin names, silkscreened
 * on the board), but this node drives it in SPI mode - see kx134.cpp - so
 * electrically they're SCK/MISO/MOSI. Named per the board silkscreen below,
 * not the electrical role, matching the wiring guide's pin table:
 *
 *   D8  (GPIO7)  KX134 SCL  (= SPI SCK)  - hardware default, shared bus
 *   D9  (GPIO8)  KX134 ADR  (= SPI MISO) - hardware default, shared bus
 *   D10 (GPIO9)  KX134 SDA  (= SPI MOSI) - hardware default, shared bus
 *   D6  (GPIO43) KX134 CS    - software chip-select, mirrors mcu/'s
 *                              spi2 cs-gpios pattern (boards/
 *                              arduino_uno_q.overlay)
 *   D7  (GPIO44) KX134 INT1  - Buffer Full Interrupt, mirrors mcu/'s
 *                              kx134@0 int-gpios (D9/PB8 there)
 *   D1  (GPIO2)  MIC SCK (I2S BCLK)   - INMP441 mic, mirrors mcu/'s SAI1_A
 *                                        bit-clock role
 *   D2  (GPIO3)  MIC WS (I2S LRCLK)   - INMP441 mic, mirrors mcu/'s SAI1_A
 *                                        frame-clock role
 *   D3  (GPIO4)  MIC SD (I2S data in) - INMP441 mic
 *   D5  (GPIO6)  WS2812 DIN    - status ring, mirrors mcu/'s led_ring
 *                                 node (D4/PA12 there).
 *
 * D0/D4 are left unused by this pin map.
 *
 * LED_BUILTIN (GPIO21, from pins_arduino.h - not one of the D0-D10 pins)
 * is the onboard single-color status LED, used as the heartbeat indicator
 * in main.cpp - the direct equivalent of mcu/src/main.c's heartbeat_led
 * (onboard LED3 green channel), independent of the WS2812 ring the same
 * way mcu/'s heartbeat LED is independent of that board's own ring.
 */

#define PIN_KX134_SCK  D8
#define PIN_KX134_MISO D9
#define PIN_KX134_MOSI D10
#define PIN_KX134_CS   D6
#define PIN_KX134_INT1 D7

#define PIN_MIC_I2S_WS   D2
#define PIN_MIC_I2S_BCLK D1
#define PIN_MIC_I2S_SD   D3

#define PIN_WS2812_DIN D5

#endif /* BOARD_PINS_H_ */
