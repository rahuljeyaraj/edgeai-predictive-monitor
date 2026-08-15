/*
 * test_http_form_parse.c — host-native test for components/epm_drivers/
 * http_form_parse.c's application/x-www-form-urlencoded parsing (Phase 12b).
 * Links the real source, same pattern as test_scalar_map.c linking
 * epm_codec/scalar_map.c.
 *
 * Build/run: see tests/host/README.md.
 */
#include <string.h>

#include "drivers/http_form_parse.h"
#include "test_util.h"

static void test_url_decode_plus_and_percent(void)
{
	char s[64];
	strcpy(s, "hello+world%21%2Fpath");
	http_form_url_decode(s);

	int ok = strcmp(s, "hello world!/path") == 0;
	test_report("url_decode_plus_and_percent", ok, EXPECT_PASS, s);
}

static void test_url_decode_malformed_percent_passes_through(void)
{
	char s[32];
	strcpy(s, "50%off%2");
	http_form_url_decode(s);

	/* "%2" has no second hex digit and "%o" isn't hex at all — both are
	 * copied through unchanged rather than decoded. */
	int ok = strcmp(s, "50%off%2") == 0;
	test_report("url_decode_malformed_percent_passes_through", ok, EXPECT_PASS, s);
}

static void test_get_value_finds_middle_pair(void)
{
	char out[32] = {0};
	int found = http_form_get_value("ssid=MyNet&password=hunter2&mqtt_port=1883", "password",
					 out, sizeof(out));

	int ok = found && strcmp(out, "hunter2") == 0;
	test_report("get_value_finds_middle_pair", ok, EXPECT_PASS, out);
}

static void test_get_value_decodes_result(void)
{
	char out[32] = {0};
	int found = http_form_get_value("ssid=My+Home+Net&mqtt_host=broker.local", "ssid", out,
					 sizeof(out));

	int ok = found && strcmp(out, "My Home Net") == 0;
	test_report("get_value_decodes_result", ok, EXPECT_PASS, out);
}

static void test_get_value_missing_key_returns_false(void)
{
	char out[32] = {0};
	int found = http_form_get_value("ssid=MyNet&mqtt_host=broker.local", "password", out,
					 sizeof(out));

	test_report("get_value_missing_key_returns_false", !found, EXPECT_PASS,
		    !found ? "correctly not found" : "incorrectly found a missing key");
}

static void test_get_value_truncates_to_out_size(void)
{
	char out[5] = {0}; /* room for 4 chars + NUL */
	int found = http_form_get_value("ssid=LongNetworkName", "ssid", out, sizeof(out));

	int ok = found && strlen(out) == 4 && strncmp(out, "Long", 4) == 0;
	test_report("get_value_truncates_to_out_size", ok, EXPECT_PASS, out);
}

static void test_get_value_empty_value(void)
{
	char out[32] = "unchanged";
	int found = http_form_get_value("password=&ssid=MyNet", "password", out, sizeof(out));

	int ok = found && out[0] == '\0';
	test_report("get_value_empty_value", ok, EXPECT_PASS, out);
}

static void test_get_value_last_pair_no_trailing_amp(void)
{
	char out[32] = {0};
	int found = http_form_get_value("ssid=MyNet&mqtt_port=1883", "mqtt_port", out, sizeof(out));

	int ok = found && strcmp(out, "1883") == 0;
	test_report("get_value_last_pair_no_trailing_amp", ok, EXPECT_PASS, out);
}

int main(void)
{
	test_url_decode_plus_and_percent();
	test_url_decode_malformed_percent_passes_through();
	test_get_value_finds_middle_pair();
	test_get_value_decodes_result();
	test_get_value_missing_key_returns_false();
	test_get_value_truncates_to_out_size();
	test_get_value_empty_value();
	test_get_value_last_pair_no_trailing_amp();

	return test_summary();
}
