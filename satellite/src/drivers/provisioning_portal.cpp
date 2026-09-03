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
/* Cap on distinct SSIDs handle_scan() reports - generous for any real site,
 * bounds the on-stack dedup array to a fixed, small size. */
#define MAX_SCAN_RESULTS 24

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

/* Scan results cache - see rescan_and_cache()/handle_scan() below for why
 * this exists instead of scanning on every /scan request. */
static String cached_networks[MAX_SCAN_RESULTS];
static int cached_count;

/* Shared dark-theme styling, matching the base station dashboard's own
 * palette (base-station/python/frontend/style.css: #0f172a page / #1e293b
 * card / #334155 border / #e2e8f0 text / #10b981 primary-action green) so a
 * technician sees one consistent visual language whether they're looking at
 * this portal or the dashboard's own Network tab
 * (docs/WIFI_ONBOARDING_PLAN.md S1) a minute later. Kept as one inline
 * <style> block (no external stylesheet) since this is served standalone
 * off the ESP32's own web server, with no dashboard asset pipeline to draw
 * from. */
#define PORTAL_STYLE                                                                             \
	"<style>:root{color-scheme:dark}*{box-sizing:border-box}"                                \
	"body{margin:0;padding:28px 16px;background:#0f172a;"                                     \
	"font-family:system-ui,-apple-system,'Segoe UI',sans-serif;color:#e2e8f0}"                 \
	".card{max-width:420px;margin:0 auto;background:#1e293b;border:1px solid #334155;"         \
	"border-radius:12px;padding:20px}"                                                         \
	"h1{font-size:20px;font-weight:700;color:#f8fafc;margin:0 0 16px}"                          \
	".tip{background:rgba(245,158,11,.14);color:#f59e0b;border-radius:8px;padding:10px 12px;"  \
	"font-size:13px;margin-bottom:16px}"                                                       \
	".err{background:rgba(239,68,68,.14);color:#f87171;border-radius:8px;padding:10px 12px;"   \
	"font-size:13px;margin-bottom:16px}"                                                       \
	"label{display:block;font-size:13px;color:#94a3b8;margin:14px 0 6px}"                       \
	"input{width:100%;padding:10px 12px;font-size:16px;background:#0f172a;"                     \
	"border:1px solid #334155;border-radius:8px;color:#e2e8f0;font-family:inherit}"             \
	"input::placeholder{color:#64748b}"                                                        \
	".hint{font-size:12px;color:#64748b;margin-top:6px}"                                        \
	".chips{display:flex;flex-wrap:wrap;gap:8px}"                                              \
	".chip{font:inherit;font-size:14px;font-weight:600;color:#94a3b8;background:#0f172a;"       \
	"border:1px solid #334155;border-radius:999px;padding:6px 14px;cursor:pointer}"             \
	".chip.is-active{color:#e2e8f0;background:rgba(57,135,229,.22);border-color:#3987e5}"       \
	".scan-msg{font-size:13px;color:#64748b;margin-top:8px}"                                    \
	".btn-label{display:inline-flex;align-items:center;justify-content:center;gap:6px;"        \
	"height:30px;padding:0 12px;background-color:#334155;color:#e2e8f0;border:none;"           \
	"border-radius:6px;cursor:pointer;font:inherit;font-size:14px;margin-top:8px}"             \
	".btn-label:disabled{color:#64748b;cursor:not-allowed}"                                    \
	"button.connect{width:100%;margin-top:22px;padding:12px;font-size:16px;font-weight:600;"    \
	"color:#0f172a;background:#10b981;border:none;border-radius:8px;cursor:pointer}"            \
	"#msg{padding:12px;border-radius:8px;background:rgba(148,163,184,.12);color:#cbd5e1;"       \
	"font-size:14px}</style>"

static void append_json_escaped(String &out, const char *s)
{
	for (const char *p = s; *p; p++) {
		if (*p == '"' || *p == '\\') {
			out += '\\';
		}
		out += *p;
	}
}

static void append_attr_escaped(String &out, const char *s)
{
	for (const char *p = s; *p; p++) {
		if (*p == '"') {
			out += "&quot;";
		} else if (*p == '&') {
			out += "&amp;";
		} else {
			out += *p;
		}
	}
}

/* Distinguishes a raw IPv4 literal from an mDNS hostname in a previously-
 * saved broker value, so a re-provision splits it back into the right one
 * of the form's two fields (see send_form_page()) instead of always
 * offering it back as the mDNS field's value. */
static bool looks_like_ipv4(const char *s)
{
	int dots = 0;

	if (!s || !*s) {
		return false;
	}
	for (const char *p = s; *p; p++) {
		if (*p == '.') {
			dots++;
			continue;
		}
		if (!isdigit((unsigned char)*p)) {
			return false;
		}
	}
	return dots == 3;
}

static void send_form_page(const char *error_msg)
{
	bool prefill_is_ip = looks_like_ipv4(broker_prefill);
	String html;

	html.reserve(3072);
	html += "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
		"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
		"<title>EPM Satellite Setup</title>" PORTAL_STYLE
		"</head><body><div class=\"card\"><h1>Connect this sensor to Wi-Fi</h1>";

	if (error_msg && error_msg[0]) {
		html += "<div class=\"err\">";
		html += error_msg;
		html += "</div>";
	}

	html += "<div class=\"tip\">Submitting tests this network without disconnecting you. "
		"On success this device's own Wi-Fi turns off a few seconds later and it joins "
		"your network &mdash; reconnect your phone to your normal Wi-Fi afterward.</div>"
		"<label>Nearby networks</label><div class=\"chips\" id=\"chips\"></div>"
		"<div class=\"scan-msg\" id=\"scanmsg\">Scanning&hellip;</div>"
		"<button type=\"button\" class=\"btn-label\" id=\"rescan\" onclick=\"doScan()\">Scan for networks</button>"
		"<form method=\"POST\" action=\"/save\">"
		"<label>Wi-Fi network name (SSID)</label>"
		"<input name=\"ssid\" id=\"ssid\" required maxlength=\"32\" autocapitalize=\"off\" autocorrect=\"off\">"
		"<label>Wi-Fi password (leave blank if open)</label>"
		"<input name=\"password\" type=\"password\" maxlength=\"64\" autocomplete=\"off\">"
		"<label>Base station address (mDNS name)</label>"
		"<input name=\"broker_host\" maxlength=\"64\" autocapitalize=\"off\" autocorrect=\"off\" value=\"";
	append_attr_escaped(html, prefill_is_ip ? MQTT_BROKER_HOST : broker_prefill);
	html += "\">"
		"<label>IP address (optional &mdash; only if mDNS doesn't resolve)</label>"
		"<input name=\"broker_ip\" id=\"broker_ip\" placeholder=\"e.g. " BASE_STATION_HOTSPOT_IP
		"\" maxlength=\"64\" autocapitalize=\"off\" autocorrect=\"off\" value=\"";
	if (prefill_is_ip) {
		append_attr_escaped(html, broker_prefill);
	}
	html += "\">"
		"<div class=\"hint\" id=\"hotspot-hint\" style=\"display:none\">Connecting straight to "
		"the base station's own hotspot &mdash; its fixed address is filled in above.</div>"
		"<button class=\"connect\" type=\"submit\">Connect</button>"
		"</form></div><script>"
		"var HOTSPOT_SSID=\"" BASE_STATION_HOTSPOT_SSID "\",HOTSPOT_IP=\"" BASE_STATION_HOTSPOT_IP "\";"
		"function pick(ssid){document.getElementById('ssid').value=ssid;"
		"var ip=document.getElementById('broker_ip'),hint=document.getElementById('hotspot-hint');"
		"if(ssid===HOTSPOT_SSID){ip.value=HOTSPOT_IP;ip.dataset.auto='1';hint.style.display='block';}"
		"else{if(ip.dataset.auto==='1'){ip.value='';}ip.dataset.auto='';hint.style.display='none';}}"
		"function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/\"/g,'&quot;');}"
		"function renderChips(list){"
		"var el=document.getElementById('chips'),msg=document.getElementById('scanmsg');"
		"if(!list.length){msg.textContent='No networks found \\u2014 type the name below.';el.innerHTML='';return;}"
		"msg.textContent='';"
		"el.innerHTML=list.map(function(n){return '<button type=\"button\" class=\"chip\" "
		"onclick=\"pick(this.dataset.ssid)\" data-ssid=\"'+esc(n)+'\">'+esc(n)+'</button>';}).join('');}"
		"function doScan(){var btn=document.getElementById('rescan');btn.disabled=true;"
		"btn.textContent='Scanning\\u2026';document.getElementById('scanmsg').textContent='Scanning\\u2026';"
		"fetch('/scan').then(function(r){return r.json();}).then(function(d){renderChips(d.networks||[]);"
		"btn.disabled=false;btn.textContent='Scan for networks';})"
		".catch(function(){document.getElementById('scanmsg').textContent="
		"'Couldn\\u2019t scan \\u2014 type the name below.';btn.disabled=false;"
		"btn.textContent='Scan for networks';});}"
		"doScan();"
		"</script></body></html>";

	web_server.send(200, "text/html", html);
}

static void send_status_page(void)
{
	String html;

	html.reserve(1280);
	html += "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
		"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
		"<title>EPM Satellite Setup</title>" PORTAL_STYLE
		"</head><body><div class=\"card\">"
		"<h1>Connecting&hellip;</h1><div id=\"msg\">Testing the network, please wait&hellip;</div>"
		"</div><script>"
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
	String broker_host = web_server.arg("broker_host");
	String broker_ip = web_server.arg("broker_ip");

	broker_host.trim();
	broker_ip.trim();

	/* Two fields (mDNS name + optional IP override), not one ambiguous
	 * "MQTT broker address" field (docs/WIFI_ONBOARDING_PLAN.md S2's
	 * original shape) - a filled-in IP always wins, since it's the one the
	 * technician deliberately typed (or the auto-fill picked, see
	 * send_form_page()'s HOTSPOT_SSID JS) specifically because mDNS won't
	 * do here. */
	String broker = broker_ip.length() > 0 ? broker_ip : broker_host;

	if (broker.length() == 0) {
		broker = broker_prefill;
	}

	memset(&pending, 0, sizeof(pending));
	strncpy(pending.wifi_ssid, ssid.c_str(), CREDS_SSID_MAX_LEN);
	strncpy(pending.wifi_password, password.c_str(), CREDS_PASS_MAX_LEN);

	/* Parse an optional trailing ":<port>" off whichever field won above -
	 * same shape docs/WIFI_ONBOARDING_PLAN.md S2 always used, now applying
	 * to either field rather than one merged one. */
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

/* A synchronous WiFi.scanNetworks() while hosting an active softAP is
 * standard ESP-IDF behavior, not a known limitation - but ESP32 AP+STA is
 * one radio, so the scan drags the softAP's channel away from whoever is
 * currently associated to it. Called only from contexts where that's known
 * to be harmless: once at AP start (nobody's associated yet) and from
 * handle_scan() itself, gated on the same condition. */
static void rescan_and_cache(void)
{
	int n = WiFi.scanNetworks();

	cached_count = 0;
	for (int i = 0; i < n && cached_count < MAX_SCAN_RESULTS; i++) {
		String ssid = WiFi.SSID(i);

		if (ssid.length() == 0) {
			continue;
		}

		bool dup = false;

		for (int j = 0; j < cached_count; j++) {
			if (cached_networks[j] == ssid) {
				dup = true;
				break;
			}
		}
		if (dup) {
			continue;
		}
		cached_networks[cached_count++] = ssid;
	}
	WiFi.scanDelete();
}

/* Nearby-network chip list (send_form_page()'s JS), the ESP32-native
 * counterpart to the base station's Round 3 fix of replacing an unreliable
 * <datalist> dropdown with real tappable buttons
 * (docs/WIFI_ONBOARDING_PLAN.md's wifi-onboarding-round-3 notes) - built
 * that way here from the start rather than repeating the same mobile-
 * browser mistake.
 *
 * send_form_page()'s doScan() fires this on every page load, which used to
 * mean the phone's own page load triggered a rescan that could kick it
 * off-channel mid-load (see the satellite-provisioning-portal-page-wont-
 * load memory) - a request reaching this handler at all means a client IS
 * currently associated, so only rescan when the station count says
 * otherwise; the rest of the time this just serves the cache filled by
 * rescan_and_cache() at AP start. */
static void handle_scan(void)
{
	if (WiFi.softAPgetStationNum() == 0) {
		rescan_and_cache();
	}

	String json = "{\"networks\":[";

	for (int i = 0; i < cached_count; i++) {
		if (i) {
			json += ",";
		}
		json += "\"";
		append_json_escaped(json, cached_networks[i].c_str());
		json += "\"";
	}
	json += "]}";

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

	/* Nobody can be associated to the softAP yet - it was just brought up
	 * above - so this first scan is always safe. Seeds handle_scan()'s
	 * cache so the very first page load has real chips instead of an empty
	 * list, without itself risking a kick. */
	rescan_and_cache();

	web_server.on("/", HTTP_GET, handle_root);
	web_server.on("/save", HTTP_POST, handle_save);
	web_server.on("/status", HTTP_GET, handle_status);
	web_server.on("/scan", HTTP_GET, handle_scan);
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
