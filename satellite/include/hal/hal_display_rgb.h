#ifndef HAL_DISPLAY_RGB_H_
#define HAL_DISPLAY_RGB_H_

#include <stdint.h>

/*
 * RGB status ring contract - identical command surface to
 * mcu/src/hal/hal_display_rgb.h (CONST/BREATHE/STROBE, packed 0xRRGGBB,
 * same set/tick split), driven here by MQTT STATUS_LED commands (Appendix
 * B S3) instead of mcu/'s UART DISPLAY_RGB frames - same three-mode
 * vocabulary by design (Appendix B's STATUS_LED doc: "deliberately the
 * same three-mode vocabulary as the UART-side MCU display's
 * RGB_DISPLAY_CONST/BREATHE/STROBE"). src/drivers/rgb_ws2812.cpp is the
 * implementation, using Adafruit_NeoPixel instead of mcu/'s Zephyr
 * led_strip driver.
 */

enum rgb_display_mode {
	RGB_DISPLAY_CONST = 0,
	RGB_DISPLAY_BREATHE = 1,
	RGB_DISPLAY_STROBE = 2,
};

int hal_display_rgb_init(void);
void hal_display_rgb_set(uint32_t rgb, enum rgb_display_mode mode, uint16_t period_ms);
void hal_display_rgb_tick(void);

#endif /* HAL_DISPLAY_RGB_H_ */
