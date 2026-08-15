/*
 * test_status_led.c — host-native test for components/epm_codec/wire_protocol.c's
 * mqtt_decode_status_led() (Phase 7c). Links the real source, same pattern as
 * test_scalar_map.c linking epm_codec/scalar_map.c.
 *
 * Build/run: see tests/host/README.md.
 */
#include <stdio.h>
#include <string.h>

#include "frame_codec/wire_protocol.h"
#include "test_util.h"

static void test_valid_payload_parsed_correctly(void)
{
	uint8_t wire[sizeof(struct display_rgb_payload)];
	uint32_t rgb = 0x00FF00;
	uint8_t mode = 1;
	uint16_t period_ms = 1500;

	memcpy(&wire[0], &rgb, sizeof(rgb));
	wire[4] = mode;
	memcpy(&wire[5], &period_ms, sizeof(period_ms));

	struct display_rgb_payload out;
	int ok = mqtt_decode_status_led(wire, sizeof(wire), &out);

	ok = ok && (out.rgb == rgb) && (out.mode == mode) && (out.period_ms == period_ms);

	char detail[128];
	snprintf(detail, sizeof(detail), "rgb=0x%06lx(want 0x%06lx) mode=%u(want %u) period_ms=%u(want %u)",
		 (unsigned long)out.rgb, (unsigned long)rgb, (unsigned)out.mode, (unsigned)mode,
		 (unsigned)out.period_ms, (unsigned)period_ms);

	test_report("status_led_valid_payload_parsed", ok, EXPECT_PASS, detail);
}

static void test_too_short_payload_rejected(void)
{
	uint8_t wire[sizeof(struct display_rgb_payload) - 1] = {0};
	struct display_rgb_payload out;

	int rejected = !mqtt_decode_status_led(wire, sizeof(wire), &out);

	test_report("status_led_too_short_payload_rejected", rejected, EXPECT_PASS,
		    rejected ? "correctly rejected" : "incorrectly accepted a short payload");
}

static void test_empty_payload_rejected(void)
{
	struct display_rgb_payload out;

	int rejected = !mqtt_decode_status_led(NULL, 0, &out);

	test_report("status_led_empty_payload_rejected", rejected, EXPECT_PASS,
		    rejected ? "correctly rejected" : "incorrectly accepted a zero-length payload");
}

static void test_oversized_payload_still_parses_leading_bytes(void)
{
	uint8_t wire[sizeof(struct display_rgb_payload) + 4];
	uint32_t rgb = 0xFF00FF;
	uint8_t mode = 2;
	uint16_t period_ms = 150;

	memset(wire, 0xAA, sizeof(wire));
	memcpy(&wire[0], &rgb, sizeof(rgb));
	wire[4] = mode;
	memcpy(&wire[5], &period_ms, sizeof(period_ms));

	struct display_rgb_payload out;
	int ok = mqtt_decode_status_led(wire, sizeof(wire), &out);

	ok = ok && (out.rgb == rgb) && (out.mode == mode) && (out.period_ms == period_ms);

	test_report("status_led_oversized_payload_parses_leading_bytes", ok, EXPECT_PASS,
		    ok ? "trailing bytes correctly ignored" : "unexpected decode result");
}

int main(void)
{
	test_valid_payload_parsed_correctly();
	test_too_short_payload_rejected();
	test_empty_payload_rejected();
	test_oversized_payload_still_parses_leading_bytes();

	return test_summary();
}
