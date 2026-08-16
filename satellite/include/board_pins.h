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
 * (D8/D9/D10 = SCK/MISO/MOSI, set by the variant, not reassignable
 * without bit-banging SPI instead):
 *
 *   D8  (GPIO7)  SPI SCK   - KX134 (hardware default, shared bus)
 *   D9  (GPIO8)  SPI MISO  - KX134 (hardware default, shared bus)
 *   D10 (GPIO9)  SPI MOSI  - KX134 (hardware default, shared bus)
 *   D3  (GPIO4)  KX134 CS    - software chip-select, mirrors mcu/'s
 *                              spi2 cs-gpios pattern (boards/
 *                              arduino_uno_q.overlay)
 *   D2  (GPIO3)  KX134 INT1  - Buffer Full Interrupt, mirrors mcu/'s
 *                              kx134@0 int-gpios (D9/PB8 there)
 *   D0  (GPIO1)  I2S WS/LRCLK  - INMP441 mic, mirrors mcu/'s SAI1_A
 *                                 frame-clock role
 *   D1  (GPIO2)  I2S BCLK      - INMP441 mic, mirrors mcu/'s SAI1_A
 *                                 bit-clock role
 *   D4  (GPIO5)  I2S SD (data in) - INMP441 mic. Reuses the pin the
 *                                    variant labels SDA/A4 - onboard
 *                                    hardware I2C isn't used by this
 *                                    node, so the pin is free for plain
 *                                    GPIO/I2S duty here.
 *   D5  (GPIO6)  WS2812 DIN    - status ring, mirrors mcu/'s led_ring
 *                                 node (D4/PA12 there). Reuses the
 *                                 variant's SCL/A5 pin for the same
 *                                 "I2C unused, free for other duty"
 *                                 reason as D4 above.
 *
 * D6/D7 (GPIO43/44, the variant's UART0 TX/RX) are left unused - not
 * needed since this node has no UART link to any other device (WiFi/MQTT
 * replaces mcu/'s LPUART1 entirely; USB CDC handles the debug log/
 * Serial console instead of a second UART).
 *
 * LED_BUILTIN (GPIO21, from pins_arduino.h - not one of the D0-D10 pins)
 * is the onboard single-color status LED, used as the heartbeat indicator
 * in main.cpp - the direct equivalent of mcu/src/main.c's heartbeat_led
 * (onboard LED3 green channel), independent of the WS2812 ring the same
 * way mcu/'s heartbeat LED is independent of that board's own ring.
 */

#define PIN_KX134_CS   D3
#define PIN_KX134_INT1 D2

#define PIN_MIC_I2S_WS   D0
#define PIN_MIC_I2S_BCLK D1
#define PIN_MIC_I2S_SD   D4

#define PIN_WS2812_DIN D5

#endif /* BOARD_PINS_H_ */
