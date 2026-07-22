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
#include "hal/hal_display_rgb.h"
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
 */

#define TRANSPORT_TASK_STACK_WORDS 6144
#define TRANSPORT_TASK_PRIORITY    5
#define TRANSPORT_LOOP_DELAY_MS    10
#define MQTT_RECONNECT_BACKOFF_MS  2000
/* PubSubClient::publish() copies the *entire* payload byte-by-byte into
 * this->buffer before writing it to the socket (PubSubClient.cpp), so this
 * must be >= the largest telemetry frame threads/fuser_task.cpp can produce
 * (frame_codec/spectrum_codec.h's section-list frame: num_sections byte +
 * one SPECTRUM section per channel, each section a 5-byte header + fs/
 * fft_size/bin_count preamble + its dense float32 bins) - at
 * MIC_FFT_BIN_COUNT=ACCEL_FFT_BIN_COUNT=512 (app_config.h) that's ~4.1KB, an
 * order of magnitude smaller than the JSON envelope an early revision of
 * this firmware used to send (~31.8KB at the same bin count). setBufferSize()
 * takes a uint16_t (max 65535) - this expression stays comfortably under
 * that even at much larger bin counts than today's. */
#define MQTT_BUFFER_SIZE                                                                         \
	(1 + 2 * SPECTRUM_SECTION_OVERHEAD + (MIC_FFT_BIN_COUNT * sizeof(float)) +                \
	 (ACCEL_FFT_BIN_COUNT * sizeof(float)))

static WiFiClient wifi_client;
static PubSubClient mqtt_client(wifi_client);
static SemaphoreHandle_t mqtt_mutex;

static char node_id[7]; /* 6 lowercase hex chars + NUL, MAC-derived (derive_node_id() below) */
static char data_topic[32];
static char cmd_topic[32];

static void derive_node_id(void)
{
	/* "AA:BB:CC:DD:EE:FF" -> last 3 octets, lowercase, no separators -
	 * derived automatically from each ESP32's factory-assigned WiFi MAC
	 * address, matching base-station/python/tools/satellite_node_sim.py's
	 * node id shape exactly. */
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

static void connect_wifi(void)
{
	if (WiFi.status() == WL_CONNECTED) {
		return;
	}

	Serial.printf("[transport] connecting to WiFi SSID \"%s\"...\n", WIFI_SSID);
	WiFi.mode(WIFI_STA);
	WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

	/* Blocks indefinitely (with a periodic log) rather than giving up and
	 * retrying later: without WiFi this node has no purpose at all (no
	 * local fallback/buffering), so waiting is the only sensible
	 * option - the same reasoning as a real satellite node with no
	 * other job to get on with. */
	uint32_t attempts = 0;

	while (WiFi.status() != WL_CONNECTED) {
		vTaskDelay(pdMS_TO_TICKS(500));
		if (++attempts % 20 == 0) {
			Serial.println("[transport] still waiting for WiFi...");
		}
	}
	Serial.printf("[transport] WiFi connected, IP=%s\n", WiFi.localIP().toString().c_str());
}

static void connect_mqtt(void)
{
	if (mqtt_client.connected()) {
		return;
	}

	Serial.printf("[transport] connecting to MQTT broker %s:%d as \"%s\"...\n",
		      MQTT_BROKER_HOST, MQTT_BROKER_PORT, node_id);
	if (mqtt_client.connect(node_id)) {
		mqtt_client.subscribe(cmd_topic, 1); /* QoS 1 - SENSOR_TELEMETRY_FRAME_PLAN.md S6's QoS guidance */
		Serial.printf("[transport] MQTT connected, subscribed to %s\n", cmd_topic);
	} else {
		Serial.printf("[transport] MQTT connect failed, rc=%d\n", mqtt_client.state());
	}
}

static void transport_task_entry(void *arg)
{
	(void)arg;

	connect_wifi();
	derive_node_id();

	mqtt_client.setServer(MQTT_BROKER_HOST, MQTT_BROKER_PORT);
	mqtt_client.setBufferSize(MQTT_BUFFER_SIZE);
	mqtt_client.setCallback(mqtt_callback);

	while (1) {
		if (WiFi.status() != WL_CONNECTED) {
			connect_wifi();
		}

		if (!mqtt_client.connected()) {
			connect_mqtt();
			if (!mqtt_client.connected()) {
				vTaskDelay(pdMS_TO_TICKS(MQTT_RECONNECT_BACKOFF_MS));
				continue;
			}
		}

		xSemaphoreTake(mqtt_mutex, portMAX_DELAY);
		mqtt_client.loop();
		xSemaphoreGive(mqtt_mutex);

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
