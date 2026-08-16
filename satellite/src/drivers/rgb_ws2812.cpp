#include <errno.h>
#include <math.h>

#include <Adafruit_NeoPixel.h>
#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

#include "board_pins.h"
#include "hal/hal_display_rgb.h"

/*
 * Implements hal_display_rgb.h for a WS2812B ring using Adafruit_NeoPixel
 * - the Arduino/ESP32 port of mcu/src/drivers/rgb_ws2812.c. The
 * CONST/BREATHE/STROBE command model and the set()/tick() split are
 * carried over verbatim (same signal-processing logic, not MCU-specific);
 * only the pixel-push mechanism changes (Adafruit_NeoPixel::show()
 * instead of Zephyr's led_strip_update_rgb()).
 */

#define BREATHE_PI 3.14159265f

/* Same ring product as mcu/'s (user-confirmed: satellite carries the same
 * accelerometer, mic, and WS2812 ring hardware) - 8 pixels. */
#define RING_NUM_PIXELS 8

static Adafruit_NeoPixel ring(RING_NUM_PIXELS, PIN_WS2812_DIN, NEO_GRB + NEO_KHZ800);

struct rgb_command {
	uint32_t rgb;
	enum rgb_display_mode mode;
	uint16_t period_ms;
	int64_t start_ms;
};

static struct rgb_command current_cmd = {.rgb = 0, .mode = RGB_DISPLAY_CONST, .period_ms = 0, .start_ms = 0};
static SemaphoreHandle_t cmd_mutex;

static void set_ring(uint32_t rgb, uint8_t scale_pct)
{
	uint8_t r = (uint8_t)((((rgb >> 16) & 0xFF) * scale_pct) / 100);
	uint8_t g = (uint8_t)((((rgb >> 8) & 0xFF) * scale_pct) / 100);
	uint8_t b = (uint8_t)(((rgb & 0xFF) * scale_pct) / 100);

	for (int i = 0; i < RING_NUM_PIXELS; i++) {
		ring.setPixelColor(i, ring.Color(r, g, b));
	}
	ring.show();
}

int hal_display_rgb_init(void)
{
	cmd_mutex = xSemaphoreCreateMutex();
	if (cmd_mutex == NULL) {
		return -ENOMEM;
	}

	ring.begin();
	/* Blank the ring: WS2812 pixels keep whatever they last latched
	 * until told otherwise, unlike a simple PWM LED that resets to off
	 * - same rationale mcu/'s hal_display_rgb_init() documents. */
	set_ring(0x000000, 100);

	return 0;
}

void hal_display_rgb_set(uint32_t rgb, enum rgb_display_mode mode, uint16_t period_ms)
{
	xSemaphoreTake(cmd_mutex, portMAX_DELAY);
	current_cmd.rgb = rgb;
	current_cmd.mode = mode;
	current_cmd.period_ms = period_ms;
	current_cmd.start_ms = millis();
	xSemaphoreGive(cmd_mutex);

	if (mode == RGB_DISPLAY_CONST) {
		set_ring(rgb, 100);
	}
}

void hal_display_rgb_tick(void)
{
	struct rgb_command cmd;

	xSemaphoreTake(cmd_mutex, portMAX_DELAY);
	cmd = current_cmd;
	xSemaphoreGive(cmd_mutex);

	if (cmd.mode == RGB_DISPLAY_CONST || cmd.period_ms == 0) {
		return;
	}

	int64_t elapsed = (int64_t)millis() - cmd.start_ms;
	float phase = (float)(elapsed % cmd.period_ms) / (float)cmd.period_ms;
	uint8_t scale_pct;

	if (cmd.mode == RGB_DISPLAY_BREATHE) {
		scale_pct = (uint8_t)((1.0f - cosf(phase * 2.0f * BREATHE_PI)) * 50.0f);
	} else {
		scale_pct = (phase < 0.5f) ? 100 : 0;
	}

	set_ring(cmd.rgb, scale_pct);
}
