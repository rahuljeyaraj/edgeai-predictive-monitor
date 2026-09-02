#include <errno.h>
#include <string.h>

#include <PubSubClient.h>
#include <WiFi.h>
#include <esp_wifi.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <freertos/task.h>

#include "app_config.h"
#include "board_pins.h"
#include "frame_codec/spectrum_codec.h"
#include "frame_codec/wire_protocol.h"
#include "hal/hal_credentials.h"
#include "hal/hal_display_rgb.h"
#include "hal/hal_provisioning.h"
#include "hal/hal_transport.h"
#include "threads/transport_task.h"

/*
 * Owns the WiFi + MQTT connection (docs/SENSOR_TELEMETRY_FRAME_PLAN.md S6) -
 * the WiFi/MQTT counterpart to the base station's own SPI link
 * (base-station/sketch/spi_link.cpp implements hal_transport.h there;
 * transport_task.cpp is this node's equivalent single implementation).
 * Combined connection-owning + receive-side dispatch into one file here
 * because PubSubClient already owns the connection-keeping and framing a
 * hand-rolled UART driver would otherwise need separate files for (DMA ring
 * buffers, a streaming byte parser, TX-busy tracking) - there's no
 * equivalent split needed here.
 *
 * As of docs/WIFI_ONBOARDING_PLAN.md S2, this task also owns the
 * provision/connect/recover state machine below - previously connect_wifi()
 * blocked indefinitely on the one compiled-in WIFI_SSID/WIFI_PASSWORD; now a
 * node with no saved NVS credentials brings up its own AP + captive portal
 * (hal/hal_provisioning.h) instead of blocking, and only ever blocks for a
 * bounded join-attempt window.
 */

#define TRANSPORT_TASK_STACK_WORDS 6144
#define TRANSPORT_TASK_PRIORITY    5
#define TRANSPORT_LOOP_DELAY_MS    10
#define MQTT_RECONNECT_BACKOFF_MS  2000

/* The broker kills a client that has sent nothing for keepalive x 1.5, and
 * PubSubClient only emits its keepalive PINGREQ from inside
 * mqtt_client.loop(). transport_publish_spectrum() holds mqtt_mutex across a
 * BLOCKING socket write, and the loop() call in the CONNECTED state takes
 * that same mutex - so a congested write stalls the keepalive for exactly as
 * long as the write blocks. That ties the two timeouts into one ordering
 * constraint:
 *
 *     socket write timeout  <  MQTT keepalive x 1.5
 *
 * The library defaults VIOLATE it. arduino-esp32's WiFiClient defaults to a
 * 30s write timeout; PubSubClient's keepalive defaults to 15s, so mosquitto
 * kills at 22.5s. 30 > 22.5 means any sustained congested write is
 * *guaranteed* to lose the session. Measured 2026-08-21: both satellites
 * cycling "has exceeded timeout, disconnecting" -> reconnect about every 30s
 * while still answering every single 1s ping. On the dashboard that reads as
 * assets going offline and coming back, which looks like a WiFi or dashboard
 * fault and is neither - see the offline_detector notes before re-debugging
 * this from the browser end.
 *
 * 5s against a 67.5s kill window leaves an order of magnitude of margin. A
 * write blocked 5s has already missed its epoch anyway, so failing it fast
 * and dropping that one frame (telemetry is QoS 0 - "occasional loss
 * acceptable", SENSOR_TELEMETRY_FRAME_PLAN.md S6) is strictly better than
 * holding the mutex and losing the whole session. */
#define MQTT_KEEPALIVE_S           45
#define MQTT_SOCKET_TIMEOUT_S      5

/* Bounded STA join attempt, both for a saved-creds boot reconnect and for
 * testing a freshly-submitted portal form - generous for a real join (a
 * wrong password/AP-out-of-range reliably resolves well before this). */
#define STA_JOIN_TIMEOUT_MS 15000
/* How long CONNECTED silently retries a dropped STA link (WiFi.reconnect(),
 * no portal) before giving up and reopening the AP - covers a router
 * reboot/transient blip without bothering a technician. */
#define RECOVERY_WINDOW_MS 60000
/* Delay between "STA confirmed good" and actually tearing the AP down, so
 * the portal's success response has time to reach the technician's phone
 * over the AP link before that link itself goes away - the concrete fix
 * for the "success response never arrives" race the base station's own
 * onboarding hit (only possible here because AP+STA run concurrently). */
#define PROVISIONING_AP_TEARDOWN_GRACE_MS 4000
/* Hold PIN_BOOT_BUTTON this long to force re-provisioning from ANY state -
 * long enough that a normal tap (e.g. someone bumping the board) can't
 * trigger it by accident, short enough a technician isn't left guessing
 * whether it registered. The only field-accessible way back to the portal
 * before this existed was waiting out a genuine RECOVERY_WINDOW_MS WiFi
 * drop, or a full erase+reflash - neither works for "this node is happily
 * connected to the wrong network/broker, give me the form back." */
#define FORCE_PROVISION_HOLD_MS 3000

/* PubSubClient::publish() packs the MQTT PUBLISH header + topic string +
 * the *entire* payload byte-by-byte into this->buffer before writing it to
 * the socket (PubSubClient.cpp: MQTT_MAX_HEADER_SIZE + 2 + strlen(topic) +
 * plength must all fit), so this must be >= that whole packet, not just the
 * largest telemetry frame threads/fuser_task.cpp can produce
 * (frame_codec/spectrum_codec.h's section-list frame: num_sections byte +
 * one SPECTRUM section per channel (mic, accel_x, accel_y, accel_z, each
 * pooled to MODEL_SPECTRUM_BINS bins) + one SCALAR_SET section of up to 24
 * rms/kurtosis/std/peak/crest_factor/skewness entries) - worst case (all
 * sensors enabled) that's under 3KB at MODEL_SPECTRUM_BINS=128
 * (app_config.h), an order of magnitude smaller than the JSON envelope an
 * early revision of this firmware used to send (~31.8KB at a comparable bin
 * count). setBufferSize() takes a uint16_t (max 65535) - this expression
 * stays comfortably under that even at much larger bin counts than today's.
 * Missing the header+topic margin here silently broke every publish() the
 * moment a real frame hit the payload-only bound exactly (mic+accel, this
 * node's first multi-sensor stage) - publish() returns false with no
 * further diagnostic, so this must stay in sync with data_topic's max
 * length (sizeof(data_topic) below) or the same silent failure recurs. */
#define MQTT_BUFFER_SIZE                                                                         \
	(MQTT_MAX_HEADER_SIZE + 2 + sizeof(data_topic) +                                          \
	 1 + 4 * (SPECTRUM_SECTION_OVERHEAD + MODEL_SPECTRUM_BINS * sizeof(float)) +              \
	 SCALAR_SECTION_OVERHEAD + 24 * SCALAR_ENTRY_SIZE)

/* Provisioning/connection color language (revised 2026-08-17). Two hues,
 * neither borrowed from registry/status_color.py's NodeStatus palette
 * anymore - that palette is now also the dashboard's exact colors, so
 * reusing any of its hues here (as this used to do for JOIN_FAILED/
 * CONNECTED/RECOVERING) would put three unrelated meanings on one color
 * with only a blink pattern telling them apart.
 *
 * Magenta = "setup/provisioning" family. Blue = "connectivity trouble"
 * family (const = the specific, immediately-fixable case of MQTT being
 * unreachable while WiFi is fine; breathe = the vaguer, self-healing case
 * of WiFi itself having dropped). CONNECTED with MQTT up gets no color of
 * its own at all - the base station is reachable and will push the real
 * NodeStatus color moments later, so this ring just keeps showing whatever
 * it last showed until that arrives. */
#define RGB_PROVISIONING 0xff00ffu /* magenta, CONST - waiting for setup */
#define RGB_STA_TESTING   0xff00ffu /* same hue, BREATHE = "actively testing" */
#define RGB_MQTT_DOWN     0x0000ffu /* blue, CONST - wifi fine, broker unreachable */
#define RGB_RECOVERING    0x0000ffu /* blue, BREATHE - wifi dropped, retrying */

static WiFiClient wifi_client;
static PubSubClient mqtt_client(wifi_client);
static SemaphoreHandle_t mqtt_mutex;

static char node_id[7]; /* 6 lowercase hex chars + NUL, MAC-derived (derive_node_id() below) */
static char data_topic[32];
static char cmd_topic[32];

static struct node_credentials creds;

enum transport_state {
	TRANSPORT_STATE_BOOT_STA_ATTEMPT,
	TRANSPORT_STATE_PROVISIONING,
	TRANSPORT_STATE_STA_TESTING,
	TRANSPORT_STATE_CONNECTED,
	TRANSPORT_STATE_RECOVERING,
	/* Reached only via a held PIN_BOOT_BUTTON (see FORCE_PROVISION_HOLD_MS) -
	 * handled by the exact same switch-case body as TRANSPORT_STATE_
	 * PROVISIONING (AP+portal up, waiting for a submission), but kept as a
	 * distinct value on purpose: the background "have_saved_creds &&
	 * WiFi.status() != WL_CONNECTED -> keep retrying" / "already connected
	 * -> declare self-healed, close the portal" logic below the switch
	 * statement is keyed on state == TRANSPORT_STATE_PROVISIONING
	 * specifically. A technician forcing this state is very often still
	 * connected to a perfectly good WiFi link (e.g. the actual bug that
	 * motivated this: WiFi fine, wrong MQTT broker address) - reusing
	 * PROVISIONING as-is would have that self-heal check fire on the very
	 * next 10ms tick and slam the portal shut before they could touch it. */
	TRANSPORT_STATE_FORCED_PROVISIONING,
};

/* Tracks whether the ring is currently showing RGB_MQTT_DOWN while in
 * TRANSPORT_STATE_CONNECTED, so hal_display_rgb_set() is
 * only called on an actual transition (every ~10ms loop tick otherwise,
 * which would keep resetting the BREATHE phase and never let it actually
 * breathe). UNKNOWN forces a fresh check the moment CONNECTED is (re-)entered. */
enum mqtt_led_state {
	MQTT_LED_UNKNOWN = 0,
	MQTT_LED_UP,
	MQTT_LED_DOWN,
};

static void derive_node_id(void)
{
	/* "AA:BB:CC:DD:EE:FF" -> last 3 octets, lowercase, no separators -
	 * derived automatically from each ESP32's factory-assigned WiFi MAC
	 * address, matching base-station/python/tools/satellite_node_sim.py's
	 * node id shape exactly. WiFi.macAddress() needs no prior
	 * WiFi.mode()/begin() call (falls back to reading the factory MAC
	 * directly), so this can - and must, for the AP SSID below - run
	 * before any connection decision is made. */
	String mac = WiFi.macAddress();

	mac.replace(":", "");
	mac.toLowerCase();
	String last6 = mac.substring(mac.length() - 6);

	strncpy(node_id, last6.c_str(), sizeof(node_id) - 1);
	node_id[sizeof(node_id) - 1] = '\0';

	snprintf(data_topic, sizeof(data_topic), "epm/%s/data", node_id);
	snprintf(cmd_topic, sizeof(cmd_topic), "epm/%s/cmd", node_id);
}

/* MQTT message callback - runs inside mqtt_client.loop() (transport_task's
 * own task context, see transport_task_entry() below). The only inbound
 * command type over MQTT is STATUS_LED (the cmd topic,
 * SENSOR_TELEMETRY_FRAME_PLAN.md S6) - there's no per-type dispatch switch
 * needed since there's only ever one type to handle. Payload is binary
 * ([TYPE: 1B][display_rgb_payload], frame_codec/wire_protocol.h) rather
 * than the JSON envelope an earlier revision of this firmware parsed
 * here. */
static void mqtt_callback(char *topic, uint8_t *payload, unsigned int length)
{
	(void)topic;

	uint8_t type;
	const uint8_t *body;
	size_t body_len;

	if (!mqtt_decode_message(payload, length, &type, &body, &body_len)) {
		return;
	}
	if (type != MQTT_MSG_TYPE_STATUS_LED) {
		return;
	}
	if (body_len < sizeof(struct display_rgb_payload)) {
		return;
	}

	struct display_rgb_payload cmd;

	memcpy(&cmd, body, sizeof(cmd));

	hal_display_rgb_set(cmd.rgb, (enum rgb_display_mode)cmd.mode, cmd.period_ms);
	Serial.printf("[transport] RX STATUS_LED rgb=0x%06x mode=%u period_ms=%u\n", cmd.rgb,
		      (unsigned)cmd.mode, cmd.period_ms);
}

/* Dev-bench escape hatch (docs/WIFI_ONBOARDING_PLAN.md S2 "Implementation
 * notes"): if WIFI_SSID/WIFI_PASSWORD were both overridden via build_flags
 * (or a direct app_config.h edit) away from their "CHANGE_ME" placeholders,
 * AND NVS has nothing saved yet, seed NVS with them so this boot skips the
 * portal entirely. Only fires on a genuinely first boot - once any
 * credentials exist in NVS (compiled-seeded or real portal submissions),
 * this never overwrites them, so a later captive-portal re-provision to a
 * different network survives subsequent reflashes of the same bench build.
 * No-op for a real deployment build, which passes neither flag. */
static void maybe_seed_bench_credentials(void)
{
	if (strcmp(WIFI_SSID, "CHANGE_ME") == 0 || strcmp(WIFI_PASSWORD, "CHANGE_ME") == 0) {
		return;
	}

	struct node_credentials existing;

	if (hal_credentials_load(&existing)) {
		return; /* already provisioned (bench-seeded earlier, or a real portal submission) - don't clobber it */
	}

	struct node_credentials bench_creds;

	memset(&bench_creds, 0, sizeof(bench_creds));
	strncpy(bench_creds.wifi_ssid, WIFI_SSID, CREDS_SSID_MAX_LEN);
	strncpy(bench_creds.wifi_password, WIFI_PASSWORD, CREDS_PASS_MAX_LEN);
	strncpy(bench_creds.mqtt_broker_host, MQTT_BROKER_HOST, CREDS_BROKER_MAX_LEN);
	bench_creds.mqtt_broker_port = MQTT_BROKER_PORT;

	hal_credentials_save(&bench_creds);
	Serial.println("[transport] seeded NVS from compiled-in WIFI_SSID/WIFI_PASSWORD (dev-bench escape hatch)");
}

static void connect_mqtt(void)
{
	if (mqtt_client.connected()) {
		return;
	}

	Serial.printf("[transport] connecting to MQTT broker %s:%d as \"%s\"...\n",
		      creds.mqtt_broker_host, creds.mqtt_broker_port, node_id);
	mqtt_client.setServer(creds.mqtt_broker_host, creds.mqtt_broker_port);
	if (mqtt_client.connect(node_id)) {
		/* Set here rather than once at init: WiFiClient pushes SO_SNDTIMEO
		 * down onto the socket, and there is no socket until the connect
		 * above succeeds - every reconnect brings a fresh one. NOTE THE
		 * UNIT: arduino-esp32's WiFiClient::setTimeout() takes SECONDS,
		 * even though it overrides Stream::setTimeout(), whose argument is
		 * milliseconds. Passing a milliseconds value here would set a
		 * timeout thousands of seconds long and silently restore the bug
		 * this constant exists to close. */
		wifi_client.setTimeout(MQTT_SOCKET_TIMEOUT_S);
		mqtt_client.subscribe(cmd_topic, 1); /* QoS 1 - SENSOR_TELEMETRY_FRAME_PLAN.md S6's QoS guidance */
		Serial.printf("[transport] MQTT connected, subscribed to %s\n", cmd_topic);
	} else {
		Serial.printf("[transport] MQTT connect failed, rc=%d\n", mqtt_client.state());
	}
}

/* Debounced hold-detector for PIN_BOOT_BUTTON (active LOW) - fires exactly
 * once per physical press-and-hold, not once per loop tick for the whole
 * duration it's held down. Checked from the main state-machine loop
 * (transport_task_entry() below), NOT from inside attempt_sta_join()'s own
 * blocking wait - a press during a join attempt (up to STA_JOIN_TIMEOUT_MS)
 * isn't caught until that attempt resolves one way or the other, an
 * accepted gap given how rarely a technician needs this mid-join. */
static bool boot_button_force_requested(void)
{
	static uint32_t press_start;
	static bool consumed;

	if (digitalRead(PIN_BOOT_BUTTON) != LOW) {
		press_start = 0;
		consumed = false;
		return false;
	}
	if (press_start == 0) {
		press_start = millis();
		return false;
	}
	if (consumed || millis() - press_start < FORCE_PROVISION_HOLD_MS) {
		return false;
	}
	consumed = true;
	return true;
}

/* Default regulatory domain excludes channels 12-14; routers outside the US
 * (ours included) commonly put their 2.4GHz AP there. Left unset, a join
 * silently stalls forever with no disconnect event, AND a scan just omits
 * the network entirely with no error either - both handle_scan() (the
 * portal's "Scan for networks" button) and attempt_sta_join() below hit
 * this, so every WiFi.mode() call site in transport_task_entry() calls this
 * right after, before any scan or join can happen on that radio session. */
static void set_wifi_regulatory_domain(void)
{
	wifi_country_t country = { .cc = "JP", .schan = 1, .nchan = 14,
				    .policy = WIFI_COUNTRY_POLICY_MANUAL };
	esp_wifi_set_country(&country);
}

/* Blocks up to STA_JOIN_TIMEOUT_MS waiting for WiFi.begin() to resolve one
 * way or the other - unlike the old connect_wifi(), never blocks
 * indefinitely, since an unprovisioned/mis-provisioned node has the
 * provisioning portal to fall back on instead of just hanging. */
static bool attempt_sta_join(const char *ssid, const char *password)
{
	Serial.printf("[transport] attempting WiFi join to \"%s\"...\n", ssid);
	WiFi.begin(ssid, password);

	uint32_t start = millis();

	while (WiFi.status() != WL_CONNECTED) {
		if (millis() - start >= STA_JOIN_TIMEOUT_MS) {
			Serial.println("[transport] WiFi join timed out");
			WiFi.disconnect();
			return false;
		}
		/* Keep the portal responsive during a foreground join test
		 * (TRANSPORT_STATE_STA_TESTING) - a no-op harmlessly if the
		 * portal isn't active (TRANSPORT_STATE_BOOT_STA_ATTEMPT). */
		hal_provisioning_poll();
		vTaskDelay(pdMS_TO_TICKS(100));
	}

	/* Arduino-ESP32 leaves STA mode in WIFI_PS_MIN_MODEM power save, which
	 * parks the radio between the AP's DTIM beacons. For a node that mostly
	 * idles that is the right default; for this one it is not - it publishes
	 * a ~2.7KB frame every FUSER_EPOCH_MS (app_config.h) over TCP, and TCP
	 * needs the *return* path to be prompt, not just the transmit path.
	 * Sleeping between beacons adds hundreds of ms to every ACK, which the
	 * broker's congestion control reads as a genuinely long, highly variable
	 * RTT: the window collapses, the RTO backs off into whole seconds, and
	 * the achievable rate lands far below what the link could carry. Measured
	 * here as ~0.3-0.8 published frames/s against a 5/s target, with the
	 * broker-side socket sitting at cwnd:2 and rto:8.7s.
	 *
	 * Set after every successful join rather than once at init: WiFi.begin()
	 * re-applies the mode's default power save, so a reconnect (or the
	 * STA_TESTING path below) would silently restore it. */
	WiFi.setSleep(false);
	/* Nagle would hold the tail segment of each frame waiting for either the
	 * previous segment's ACK or more payload - and there is no more payload
	 * until the next epoch, so it waits for the ACK every single time. That
	 * is a per-frame stall on exactly the path above, and telemetry frames
	 * are already whole-message writes, which is the case Nagle exists to
	 * coalesce and cannot improve. */
	wifi_client.setNoDelay(true);

	Serial.printf("[transport] WiFi joined, IP=%s, power save off\n",
		      WiFi.localIP().toString().c_str());
	return true;
}

/* hal_provisioning_start()'s return value used to be discarded at every call
 * site below - a WiFi.softAP() failure (radio not ready yet, bad SSID, etc.)
 * was then invisible on the serial console, indistinguishable from a
 * genuinely-up AP the technician's phone just hadn't found yet. */
static void start_provisioning_ap(const char *ap_ssid, const char *broker)
{
	if (hal_provisioning_start(ap_ssid, broker) < 0) {
		Serial.printf("[transport] hal_provisioning_start FAILED for AP \"%s\" - softAP() rejected it\n",
			      ap_ssid);
		return;
	}
	Serial.printf("[transport] provisioning AP \"%s\" up, IP=%s\n", ap_ssid,
		      WiFi.softAPIP().toString().c_str());

	/* Temporary radio-health diagnostic: if this board's RX side can't see
	 * ANY nearby 2.4GHz network either, that points at an antenna/RF
	 * hardware fault on this unit rather than a phone/scanning problem. */
	int n = WiFi.scanNetworks();

	Serial.printf("[transport] diagnostic scan: %d network(s) seen\n", n);
	for (int i = 0; i < n && i < 5; i++) {
		/* Channel and auth mode, not just RSSI: a strong-signal network
		 * this node still can't authenticate to is usually an auth-mode
		 * mismatch (e.g. an AP demanding WPA3/PMF), and the channel says
		 * whether the country config even permits transmitting there. */
		Serial.printf("[transport]   %s (rssi=%d ch=%d auth=%d)\n", WiFi.SSID(i).c_str(),
			      WiFi.RSSI(i), WiFi.channel(i), (int)WiFi.encryptionType(i));
	}
	WiFi.scanDelete();
}

/* "WiFi join timed out" on its own says nothing about WHY - a wrong password,
 * an AP that never answered, and an association the AP actively refused all
 * look identical from WiFi.status(). The ESP-IDF disconnect reason separates
 * them (15 = 4-way handshake timeout, i.e. bad password; 201 = no AP found;
 * 205 = connection failed; 2/3 = auth/assoc expired), which is the difference
 * between "retype the password" and "this AP is refusing this client". */
static void wifi_sta_event_handler(WiFiEvent_t event, WiFiEventInfo_t info)
{
	if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
		Serial.printf("[transport] STA disconnected, reason=%u\n",
			      (unsigned)info.wifi_sta_disconnected.reason);
	}
}

static void transport_task_entry(void *arg)
{
	(void)arg;

	derive_node_id();
	maybe_seed_bench_credentials();
	pinMode(PIN_BOOT_BUTTON, INPUT_PULLUP);
	WiFi.onEvent(wifi_sta_event_handler);

	char ap_ssid[64];

	snprintf(ap_ssid, sizeof(ap_ssid), "%s%s", PROVISIONING_AP_SSID_PREFIX, node_id);

	mqtt_client.setBufferSize(MQTT_BUFFER_SIZE);
	mqtt_client.setKeepAlive(MQTT_KEEPALIVE_S);
	mqtt_client.setCallback(mqtt_callback);

	bool have_saved_creds = hal_credentials_load(&creds);
	enum transport_state state =
		have_saved_creds ? TRANSPORT_STATE_BOOT_STA_ATTEMPT : TRANSPORT_STATE_PROVISIONING;

	if (state == TRANSPORT_STATE_BOOT_STA_ATTEMPT) {
		WiFi.mode(WIFI_STA);
		set_wifi_regulatory_domain();
	} else {
		WiFi.mode(WIFI_AP_STA);
		set_wifi_regulatory_domain();
		start_provisioning_ap(ap_ssid, MQTT_BROKER_HOST);
		hal_display_rgb_set(RGB_PROVISIONING, RGB_DISPLAY_CONST, 0);
	}

	uint32_t recovery_deadline = 0;
	enum mqtt_led_state mqtt_led = MQTT_LED_UNKNOWN;

	while (1) {
		if (boot_button_force_requested() && state != TRANSPORT_STATE_FORCED_PROVISIONING) {
			Serial.println("[transport] BOOT button held - erasing saved credentials, forcing re-provisioning");
			hal_credentials_clear();
			memset(&creds, 0, sizeof(creds));
			hal_provisioning_stop(); /* no-op if not already active */
			WiFi.mode(WIFI_AP_STA);
			set_wifi_regulatory_domain();
			start_provisioning_ap(ap_ssid, MQTT_BROKER_HOST);
			hal_display_rgb_set(RGB_PROVISIONING, RGB_DISPLAY_CONST, 0);
			mqtt_led = MQTT_LED_UNKNOWN;
			state = TRANSPORT_STATE_FORCED_PROVISIONING;
		}

		switch (state) {
		case TRANSPORT_STATE_BOOT_STA_ATTEMPT:
			if (attempt_sta_join(creds.wifi_ssid, creds.wifi_password)) {
				state = TRANSPORT_STATE_CONNECTED;
			} else {
				WiFi.mode(WIFI_AP_STA);
				set_wifi_regulatory_domain();
				start_provisioning_ap(ap_ssid, creds.mqtt_broker_host);
				hal_display_rgb_set(RGB_PROVISIONING, RGB_DISPLAY_CONST, 0);
				state = TRANSPORT_STATE_PROVISIONING;
			}
			break;

		case TRANSPORT_STATE_PROVISIONING:
		case TRANSPORT_STATE_FORCED_PROVISIONING: {
			hal_provisioning_poll();

			struct node_credentials submitted;

			if (hal_provisioning_take_submission(&submitted)) {
				creds = submitted;
				hal_display_rgb_set(RGB_STA_TESTING, RGB_DISPLAY_BREATHE, 1000);
				state = TRANSPORT_STATE_STA_TESTING;
			}
			break;
		}

		case TRANSPORT_STATE_STA_TESTING:
			if (attempt_sta_join(creds.wifi_ssid, creds.wifi_password)) {
				hal_credentials_save(&creds);
				hal_provisioning_report_result(true, NULL);
				/* No dedicated "connected" color - the base station will
				 * push the real NodeStatus color within moments of the
				 * CONNECTED case below reaching MQTT, so the ring just
				 * keeps showing this STA_TESTING breathe until then. */
				vTaskDelay(pdMS_TO_TICKS(PROVISIONING_AP_TEARDOWN_GRACE_MS));
				hal_provisioning_stop();
				WiFi.mode(WIFI_STA);
				set_wifi_regulatory_domain();
				mqtt_led = MQTT_LED_UNKNOWN; /* force a real MQTT check next tick, not an assumed "up" */
				state = TRANSPORT_STATE_CONNECTED;
			} else {
				hal_provisioning_report_result(
					false, "Couldn't join that network - check the password and try again.");
				/* No separate "join failed" color - the setup page already
				 * shows the error text above; the ring just goes back to
				 * PROVISIONING's plain magenta. */
				hal_display_rgb_set(RGB_PROVISIONING, RGB_DISPLAY_CONST, 0);
				state = TRANSPORT_STATE_PROVISIONING;
			}
			break;

		case TRANSPORT_STATE_CONNECTED:
			if (WiFi.status() != WL_CONNECTED) {
				Serial.println("[transport] WiFi dropped, attempting silent recovery...");
				recovery_deadline = millis() + RECOVERY_WINDOW_MS;
				hal_display_rgb_set(RGB_RECOVERING, RGB_DISPLAY_BREATHE, 1000);
				mqtt_led = MQTT_LED_UNKNOWN;
				state = TRANSPORT_STATE_RECOVERING;
				break;
			}

			if (!mqtt_client.connected()) {
				/* Attempt FIRST, paint blue only if it actually
				 * fails. Painting RGB_MQTT_DOWN before the first
				 * attempt (as this used to) meant every single boot
				 * and every WiFi recovery flashed "broker
				 * unreachable" blue for the few hundred ms the
				 * normal, successful connect takes - a wrong color,
				 * shown at exactly the moment an operator is
				 * looking at a freshly-powered node. The ring stays
				 * dark until we genuinely know something instead. */
				connect_mqtt();
				if (!mqtt_client.connected()) {
					if (mqtt_led != MQTT_LED_DOWN) {
						hal_display_rgb_set(RGB_MQTT_DOWN, RGB_DISPLAY_CONST, 0);
						mqtt_led = MQTT_LED_DOWN;
					}
					vTaskDelay(pdMS_TO_TICKS(MQTT_RECONNECT_BACKOFF_MS));
					break;
				}
			}

			/* No dedicated "connected" color here either - MQTT is up,
			 * and connect_mqtt() has just (re)subscribed to the cmd
			 * topic, so the base station's RETAINED STATUS_LED (see
			 * mqtt_publisher.py's publish_status) lands within
			 * milliseconds with the real NodeStatus color. That
			 * retained replay is what makes "no connected color of our
			 * own" safe: before it, a node that reconnected after the
			 * last status change was never sent anything and sat on a
			 * stale local color indefinitely. */
			mqtt_led = MQTT_LED_UP;

			xSemaphoreTake(mqtt_mutex, portMAX_DELAY);
			mqtt_client.loop();
			xSemaphoreGive(mqtt_mutex);
			break;

		case TRANSPORT_STATE_RECOVERING:
			if (WiFi.status() == WL_CONNECTED) {
				Serial.println("[transport] WiFi recovered");
				state = TRANSPORT_STATE_CONNECTED;
				break;
			}

			if ((int32_t)(millis() - recovery_deadline) >= 0) {
				Serial.println("[transport] recovery window expired, reopening provisioning AP");
				WiFi.mode(WIFI_AP_STA);
				set_wifi_regulatory_domain();
				start_provisioning_ap(ap_ssid, creds.mqtt_broker_host);
				hal_display_rgb_set(RGB_PROVISIONING, RGB_DISPLAY_CONST, 0);
				state = TRANSPORT_STATE_PROVISIONING;
				break;
			}

			WiFi.reconnect();
			vTaskDelay(pdMS_TO_TICKS(2000));
			break;
		}

		/* Once the AP is back up (state PROVISIONING reached via
		 * RECOVERING), keep silently retrying the saved STA link in
		 * the background too - if the real network comes back on its
		 * own, self-heal and drop the AP again without needing a
		 * technician to submit anything (symmetric with the
		 * first-join path). Skipped while a phone/laptop is actually
		 * associated to the portal AP: ESP32 AP+STA is one radio, and
		 * an STA join/scan attempt forces the softAP to hop onto the
		 * STA's channel, which silently kicks any already-connected
		 * portal client mid-page-load - exactly the "setup page never
		 * loads" symptom this was causing with stale/unreachable
		 * saved creds retried every 5s regardless of who was on the
		 * AP. */
		if (state == TRANSPORT_STATE_PROVISIONING && have_saved_creds &&
		    WiFi.status() != WL_CONNECTED && WiFi.softAPgetStationNum() == 0) {
			static uint32_t last_bg_retry;

			if (millis() - last_bg_retry >= 5000) {
				last_bg_retry = millis();
				WiFi.reconnect();
			}
		} else if (state == TRANSPORT_STATE_PROVISIONING && WiFi.status() == WL_CONNECTED) {
			Serial.println("[transport] WiFi self-healed while provisioning AP was up");
			hal_provisioning_report_result(true, NULL);
			vTaskDelay(pdMS_TO_TICKS(PROVISIONING_AP_TEARDOWN_GRACE_MS));
			hal_provisioning_stop();
			WiFi.mode(WIFI_STA);
			set_wifi_regulatory_domain();
			state = TRANSPORT_STATE_CONNECTED;
		}

		vTaskDelay(pdMS_TO_TICKS(TRANSPORT_LOOP_DELAY_MS));
	}
}

int transport_init(void)
{
	mqtt_mutex = xSemaphoreCreateMutex();
	if (mqtt_mutex == NULL) {
		return -ENOMEM;
	}

	return 0;
}

const char *transport_node_id(void)
{
	return node_id;
}

int transport_publish_spectrum(const uint8_t *message, size_t len)
{
	int ret = 0;

	xSemaphoreTake(mqtt_mutex, portMAX_DELAY);
	if (!mqtt_client.connected()) {
		ret = -ENOTCONN;
	} else if (!mqtt_client.publish(data_topic, message, len, false)) {
		/* QoS 0, matching SENSOR_TELEMETRY_FRAME_PLAN.md S6's telemetry QoS ("High
		 * frequency; occasional loss acceptable") - PubSubClient's
		 * publish() has no QoS parameter; it always sends QoS 0,
		 * which happens to already be the documented choice here. */
		ret = -EIO;
	}
	xSemaphoreGive(mqtt_mutex);

	return ret;
}

int transport_task_start(void)
{
	int ret = transport_init();

	if (ret < 0) {
		return ret;
	}

	TaskHandle_t handle = NULL;
	BaseType_t ok = xTaskCreatePinnedToCore(transport_task_entry, "transport",
						TRANSPORT_TASK_STACK_WORDS, NULL,
						TRANSPORT_TASK_PRIORITY, &handle, CORE_RADIO);

	return ok == pdPASS ? 0 : -ENOMEM;
}
