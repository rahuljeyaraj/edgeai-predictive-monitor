#include <errno.h>
#include <string.h>

#include <PubSubClient.h>
#include <WiFi.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <freertos/task.h>

#include "app_config.h"
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

/* PubSubClient::publish() copies the *entire* payload byte-by-byte into
 * this->buffer before writing it to the socket (PubSubClient.cpp), so this
 * must be >= the largest telemetry frame threads/fuser_task.cpp can produce
 * (frame_codec/spectrum_codec.h's section-list frame: num_sections byte +
 * one SPECTRUM section per channel (mic, accel_x, accel_y, accel_z, each
 * pooled to MODEL_SPECTRUM_BINS bins) + one SCALAR_SET section of up to 24
 * rms/kurtosis/std/peak/crest_factor/skewness entries) - worst case (all
 * sensors enabled) that's under 3KB at MODEL_SPECTRUM_BINS=128
 * (app_config.h), an order of magnitude smaller than the JSON envelope an
 * early revision of this firmware used to send (~31.8KB at a comparable bin
 * count). setBufferSize() takes a uint16_t (max 65535) - this expression
 * stays comfortably under that even at much larger bin counts than today's. */
#define MQTT_BUFFER_SIZE                                                                         \
	(1 + 4 * (SPECTRUM_SECTION_OVERHEAD + MODEL_SPECTRUM_BINS * sizeof(float)) +              \
	 SCALAR_SECTION_OVERHEAD + 24 * SCALAR_ENTRY_SIZE)

/* Provisioning/connection color language - deliberately reuses the base
 * station's own registry/status_color.py tuples wherever the semantics
 * already match (same color vocabulary a technician has already learned
 * from the dashboard), with one new hue (magenta) for the one concept that
 * has no existing equivalent: this node's own local AP/provisioning mode. */
#define RGB_PROVISIONING 0xff00ffu /* magenta - new concept, no dashboard equivalent */
#define RGB_STA_TESTING  0xff00ffu /* same hue; faster BREATHE = "actively working" */
#define RGB_JOIN_FAILED  0xf59e0bu /* reuses status_color.py's WARNING tuple exactly */
#define RGB_CONNECTED    0x22d3eeu /* reuses status_color.py's NEW tuple */
#define RGB_RECOVERING   0x4d4d4du /* reuses status_color.py's OFFLINE tuple */

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
		mqtt_client.subscribe(cmd_topic, 1); /* QoS 1 - SENSOR_TELEMETRY_FRAME_PLAN.md S6's QoS guidance */
		Serial.printf("[transport] MQTT connected, subscribed to %s\n", cmd_topic);
	} else {
		Serial.printf("[transport] MQTT connect failed, rc=%d\n", mqtt_client.state());
	}
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

	Serial.printf("[transport] WiFi joined, IP=%s\n", WiFi.localIP().toString().c_str());
	return true;
}

static void transport_task_entry(void *arg)
{
	(void)arg;

	derive_node_id();
	maybe_seed_bench_credentials();

	char ap_ssid[64];

	snprintf(ap_ssid, sizeof(ap_ssid), "%s%s", PROVISIONING_AP_SSID_PREFIX, node_id);

	mqtt_client.setBufferSize(MQTT_BUFFER_SIZE);
	mqtt_client.setCallback(mqtt_callback);

	bool have_saved_creds = hal_credentials_load(&creds);
	enum transport_state state =
		have_saved_creds ? TRANSPORT_STATE_BOOT_STA_ATTEMPT : TRANSPORT_STATE_PROVISIONING;

	if (state == TRANSPORT_STATE_BOOT_STA_ATTEMPT) {
		WiFi.mode(WIFI_STA);
	} else {
		WiFi.mode(WIFI_AP_STA);
		hal_provisioning_start(ap_ssid, MQTT_BROKER_HOST);
		hal_display_rgb_set(RGB_PROVISIONING, RGB_DISPLAY_BREATHE, 1500);
	}

	uint32_t recovery_deadline = 0;

	while (1) {
		switch (state) {
		case TRANSPORT_STATE_BOOT_STA_ATTEMPT:
			if (attempt_sta_join(creds.wifi_ssid, creds.wifi_password)) {
				state = TRANSPORT_STATE_CONNECTED;
			} else {
				WiFi.mode(WIFI_AP_STA);
				hal_provisioning_start(ap_ssid, creds.mqtt_broker_host);
				hal_display_rgb_set(RGB_PROVISIONING, RGB_DISPLAY_BREATHE, 1500);
				state = TRANSPORT_STATE_PROVISIONING;
			}
			break;

		case TRANSPORT_STATE_PROVISIONING: {
			hal_provisioning_poll();

			struct node_credentials submitted;

			if (hal_provisioning_take_submission(&submitted)) {
				creds = submitted;
				hal_display_rgb_set(RGB_STA_TESTING, RGB_DISPLAY_BREATHE, 400);
				state = TRANSPORT_STATE_STA_TESTING;
			}
			break;
		}

		case TRANSPORT_STATE_STA_TESTING:
			if (attempt_sta_join(creds.wifi_ssid, creds.wifi_password)) {
				hal_credentials_save(&creds);
				hal_provisioning_report_result(true, NULL);
				hal_display_rgb_set(RGB_CONNECTED, RGB_DISPLAY_CONST, 0);
				vTaskDelay(pdMS_TO_TICKS(PROVISIONING_AP_TEARDOWN_GRACE_MS));
				hal_provisioning_stop();
				WiFi.mode(WIFI_STA);
				state = TRANSPORT_STATE_CONNECTED;
			} else {
				hal_provisioning_report_result(
					false, "Couldn't join that network - check the password and try again.");
				state = TRANSPORT_STATE_PROVISIONING;
			}
			break;

		case TRANSPORT_STATE_CONNECTED:
			if (WiFi.status() != WL_CONNECTED) {
				Serial.println("[transport] WiFi dropped, attempting silent recovery...");
				recovery_deadline = millis() + RECOVERY_WINDOW_MS;
				hal_display_rgb_set(RGB_RECOVERING, RGB_DISPLAY_CONST, 0);
				state = TRANSPORT_STATE_RECOVERING;
				break;
			}

			if (!mqtt_client.connected()) {
				connect_mqtt();
				if (!mqtt_client.connected()) {
					vTaskDelay(pdMS_TO_TICKS(MQTT_RECONNECT_BACKOFF_MS));
					break;
				}
			}

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
				hal_provisioning_start(ap_ssid, creds.mqtt_broker_host);
				hal_display_rgb_set(RGB_PROVISIONING, RGB_DISPLAY_BREATHE, 1500);
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
		 * first-join path). */
		if (state == TRANSPORT_STATE_PROVISIONING && have_saved_creds &&
		    WiFi.status() != WL_CONNECTED) {
			static uint32_t last_bg_retry;

			if (millis() - last_bg_retry >= 5000) {
				last_bg_retry = millis();
				WiFi.reconnect();
			}
		} else if (state == TRANSPORT_STATE_PROVISIONING && WiFi.status() == WL_CONNECTED) {
			Serial.println("[transport] WiFi self-healed while provisioning AP was up");
			hal_provisioning_report_result(true, NULL);
			hal_display_rgb_set(RGB_CONNECTED, RGB_DISPLAY_CONST, 0);
			vTaskDelay(pdMS_TO_TICKS(PROVISIONING_AP_TEARDOWN_GRACE_MS));
			hal_provisioning_stop();
			WiFi.mode(WIFI_STA);
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
	BaseType_t ok = xTaskCreate(transport_task_entry, "transport", TRANSPORT_TASK_STACK_WORDS,
				    NULL, TRANSPORT_TASK_PRIORITY, &handle);

	return ok == pdPASS ? 0 : -ENOMEM;
}
