#include <ctype.h>
#include <errno.h>
#include <string.h>

#include <Arduino.h>
#include <DNSServer.h>
#include <WebServer.h>
#include <WiFi.h>

#include "app_config.h"
#include "hal/hal_provisioning.h"

/*
 * Implements hal_provisioning.h. Single-threaded by construction: every
 * function here only ever runs from within threads/transport_task.cpp's
 * own loop (hal_provisioning_poll() calls the synchronous WebServer's
 * handleClient(), which runs request handlers inline, on the same
 * call stack) - no mutex needed around the module state below, unlike
 * drivers/rgb_ws2812.cpp's cmd_mutex which guards state shared across
 * two real tasks.
 */

#define DNS_PORT 53
#define HTTP_PORT 80

enum portal_status {
	PORTAL_STATUS_IDLE = 0,
	PORTAL_STATUS_TESTING,
	PORTAL_STATUS_SUCCESS,
	PORTAL_STATUS_FAILED,
};

static DNSServer dns_server;
static WebServer web_server(HTTP_PORT);
static bool provisioning_active;

static char ap_ssid[64];
static char broker_prefill[CREDS_BROKER_MAX_LEN + 1];

static struct node_credentials pending;
static bool has_pending;

static enum portal_status status = PORTAL_STATUS_IDLE;
static char status_detail[96];

static void send_form_page(const char *error_msg)
{
	String html;

	html.reserve(2048);
	html += "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
		"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
		"<title>EPM Satellite Setup</title>"
		"<style>body{font-family:sans-serif;max-width:420px;margin:2em auto;padding:0 1em}"
		"input{width:100%;box-sizing:border-box;padding:.6em;margin:.3em 0 1em;font-size:1em}"
		"button{width:100%;padding:.8em;font-size:1.1em}"
		".tip{background:#fff3cd;padding:.8em;border-radius:6px;margin-bottom:1em;font-size:.9em}"
		".err{background:#f8d7da;padding:.8em;border-radius:6px;margin-bottom:1em}</style>"
		"</head><body><h2>Connect this sensor to WiFi</h2>";

	if (error_msg && error_msg[0]) {
		html += "<div class=\"err\">";
		html += error_msg;
		html += "</div>";
	}

	html += "<div class=\"tip\">Submitting tests this network without disconnecting you. "
		"On success this device's own WiFi turns off in a few seconds and it joins your "
		"network &mdash; reconnect your phone to your normal WiFi afterward.</div>"
		"<form method=\"POST\" action=\"/save\">"
		"<label>WiFi network name (SSID)</label>"
		"<input name=\"ssid\" required maxlength=\"32\" autocapitalize=\"off\" autocorrect=\"off\">"
		"<label>WiFi password</label>"
		"<input name=\"password\" type=\"password\" maxlength=\"64\">"
		"<label>MQTT broker address</label>"
		"<input name=\"broker\" maxlength=\"64\" autocapitalize=\"off\" autocorrect=\"off\" value=\"";
	html += broker_prefill;
	html += "\">"
		"<button type=\"submit\">Connect</button>"
		"</form></body></html>";

	web_server.send(200, "text/html", html);
}

static void send_status_page(void)
{
	String html;

	html.reserve(1024);
	html += "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
		"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
		"<title>EPM Satellite Setup</title>"
		"<style>body{font-family:sans-serif;max-width:420px;margin:2em auto;padding:0 1em}"
		"#msg{padding:.8em;border-radius:6px;background:#e2e3e5}</style></head><body>"
		"<h2>Connecting&hellip;</h2><div id=\"msg\">Testing the network, please wait&hellip;</div>"
		"<script>"
		"function poll(){fetch('/status').then(r=>r.json()).then(function(d){"
		"var m=document.getElementById('msg');"
		"if(d.state=='testing'){m.textContent='Testing the network, please wait\\u2026';setTimeout(poll,1000);}"
		"else if(d.state=='success'){m.textContent='Connected. This device will switch networks in a few seconds.';}"
		"else{m.textContent='Failed: '+d.detail;setTimeout(function(){location.href='/';},2500);}"
		"});}"
		"poll();"
		"</script></body></html>";

	web_server.send(200, "text/html", html);
}

static void handle_root(void)
{
	if (status == PORTAL_STATUS_TESTING || status == PORTAL_STATUS_SUCCESS) {
		send_status_page();
	} else {
		send_form_page(status == PORTAL_STATUS_FAILED ? status_detail : NULL);
	}
}

/* Any unmatched path - including each OS's own connectivity-check probe
 * URL (Apple/Google/Microsoft/Firefox each ping something different) and
 * every arbitrary domain the DNS wildcard resolves to this device's IP -
 * gets a 302 back to "/". This, combined with the DNS wildcard in
 * hal_provisioning_start(), is what makes the portal auto-pop-open on a
 * joined phone/laptop instead of requiring the technician to type the AP
 * IP manually - a bare 200 page at every path is less reliable at
 * triggering that across platforms than an explicit redirect. */
static void handle_captive_redirect(void)
{
	String location = "http://";

	location += WiFi.softAPIP().toString();
	location += "/";

	web_server.sendHeader("Location", location, true);
	web_server.send(302, "text/plain", "");
}

static void handle_save(void)
{
	String ssid = web_server.arg("ssid");

	if (ssid.length() == 0) {
		send_form_page("Network name is required.");
		return;
	}

	String password = web_server.arg("password");
	String broker = web_server.arg("broker");

	if (broker.length() == 0) {
		broker = broker_prefill;
	}

	memset(&pending, 0, sizeof(pending));
	strncpy(pending.wifi_ssid, ssid.c_str(), CREDS_SSID_MAX_LEN);
	strncpy(pending.wifi_password, password.c_str(), CREDS_PASS_MAX_LEN);

	/* Single "MQTT broker address" field, matching docs/
	 * WIFI_ONBOARDING_PLAN.md S2 exactly (not split host/port inputs) -
	 * parse an optional trailing ":<port>" off the end. */
	String host = broker;
	uint16_t port = MQTT_BROKER_PORT;
	int colon = broker.lastIndexOf(':');

	if (colon > 0) {
		String port_str = broker.substring(colon + 1);
		bool all_digits = port_str.length() > 0;

		for (unsigned int i = 0; i < port_str.length() && all_digits; i++) {
			if (!isDigit((unsigned char)port_str[i])) {
				all_digits = false;
			}
		}
		if (all_digits) {
			host = broker.substring(0, colon);
			port = (uint16_t)port_str.toInt();
		}
	}

	strncpy(pending.mqtt_broker_host, host.c_str(), CREDS_BROKER_MAX_LEN);
	pending.mqtt_broker_port = port;

	has_pending = true;
	status = PORTAL_STATUS_TESTING;
	status_detail[0] = '\0';

	send_status_page();
}

static void handle_status(void)
{
	String json = "{\"state\":\"";

	switch (status) {
	case PORTAL_STATUS_TESTING:
		json += "testing";
		break;
	case PORTAL_STATUS_SUCCESS:
		json += "success";
		break;
	case PORTAL_STATUS_FAILED:
		json += "failed";
		break;
	default:
		json += "idle";
		break;
	}

	json += "\",\"detail\":\"";
	for (const char *p = status_detail; *p; p++) {
		if (*p == '"' || *p == '\\') {
			json += '\\';
		}
		json += *p;
	}
	json += "\"}";

	web_server.send(200, "application/json", json);
}

int hal_provisioning_start(const char *ap_ssid_in, const char *broker_prefill_in)
{
	if (provisioning_active) {
		return 0;
	}

	strncpy(ap_ssid, ap_ssid_in, sizeof(ap_ssid) - 1);
	ap_ssid[sizeof(ap_ssid) - 1] = '\0';
	strncpy(broker_prefill, broker_prefill_in, sizeof(broker_prefill) - 1);
	broker_prefill[sizeof(broker_prefill) - 1] = '\0';

	status = PORTAL_STATUS_IDLE;
	status_detail[0] = '\0';
	has_pending = false;

	/* Open AP, matching the base station's own deliberate "open, for a
	 * transient physically-supervised onboarding step" choice
	 * (docs/WIFI_ONBOARDING_PLAN.md S1). */
	if (!WiFi.softAP(ap_ssid)) {
		return -EIO;
	}

	dns_server.start(DNS_PORT, "*", WiFi.softAPIP());

	web_server.on("/", HTTP_GET, handle_root);
	web_server.on("/save", HTTP_POST, handle_save);
	web_server.on("/status", HTTP_GET, handle_status);
	/* Known OS captive-portal probe paths get the same redirect as
	 * onNotFound below - registered explicitly since a couple of OS
	 * captive-portal detectors are picky about matching the probe path
	 * exactly rather than tolerating a generic 404-turned-302. */
	web_server.on("/generate_204", HTTP_GET, handle_captive_redirect);
	web_server.on("/gen_204", HTTP_GET, handle_captive_redirect);
	web_server.on("/hotspot-detect.html", HTTP_GET, handle_captive_redirect);
	web_server.on("/ncsi.txt", HTTP_GET, handle_captive_redirect);
	web_server.on("/connecttest.txt", HTTP_GET, handle_captive_redirect);
	web_server.onNotFound(handle_captive_redirect);
	web_server.begin();

	provisioning_active = true;

	return 0;
}

void hal_provisioning_poll(void)
{
	if (!provisioning_active) {
		return;
	}

	dns_server.processNextRequest();
	web_server.handleClient();
}

bool hal_provisioning_take_submission(struct node_credentials *out)
{
	if (!has_pending) {
		return false;
	}

	*out = pending;
	memset(&pending, 0, sizeof(pending)); /* drop the in-RAM password once handed off */
	has_pending = false;

	return true;
}

void hal_provisioning_report_result(bool ok, const char *error_detail)
{
	status = ok ? PORTAL_STATUS_SUCCESS : PORTAL_STATUS_FAILED;
	strncpy(status_detail, error_detail ? error_detail : "", sizeof(status_detail) - 1);
	status_detail[sizeof(status_detail) - 1] = '\0';
}

void hal_provisioning_stop(void)
{
	if (!provisioning_active) {
		return;
	}

	web_server.stop();
	dns_server.stop();
	WiFi.softAPdisconnect(true);

	provisioning_active = false;
}

bool hal_provisioning_active(void)
{
	return provisioning_active;
}
