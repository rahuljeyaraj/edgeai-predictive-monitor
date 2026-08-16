#include "drivers/link_mqtt.h"

#include <errno.h>
#include <stdio.h>
#include <string.h>

#include "esp_event.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "mqtt_client.h"

#include "drivers/net_credentials.h"
#include "drivers/node_id.h"
#include "frame_codec/wire_protocol.h"
#include "hal/hal_transport.h"

/*
 * MQTT link driver - implements hal/hal_transport.h on top of ESP-IDF's
 * esp-mqtt component. WiFi STA itself is owned by src/wifi_task.c (see
 * docs/decisions/ADR-011-mqtt-transport-added.md); this driver derives the
 * node id via drivers/node_id.h (shared with provisioning.c's AP SSID,
 * Phase 12b) and starts the MQTT client once that WiFi link is already up
 * (caller's job - see src/threads/net_task.c, which blocks on
 * wifi_wait_connected() first).
 *
 * Analog of the reference repo's satellite/src/threads/transport_task.cpp,
 * split into this HAL-conforming driver behind hal/hal_transport.h.
 */

static const char *TAG = "link_mqtt";

/* Overridable via platformio.ini build_flags, same pattern as
 * src/epm_config.h. Not folded into that header because these constants
 * are private to this driver - nothing else in the tree needs them, and
 * epm_drivers must not depend on the main component's config (src already
 * depends on epm_drivers to call link_mqtt_start(); the reverse would be
 * a component dependency cycle). Default host unverified against the real
 * Uno Q - a known open item, not a code fix
 * (blocked on physical hardware access). */
#ifndef EPM_MQTT_BROKER_HOST
#define EPM_MQTT_BROKER_HOST "10.42.0.1"
#endif
#ifndef EPM_MQTT_BROKER_PORT
#define EPM_MQTT_BROKER_PORT 1883
#endif

/* Mitigation, not a fix, for a latent ESP-IDF esp-mqtt bug:
 * esp_mqtt_client_init() never checks esp_event_loop_create()'s return
 * value, so if that private event loop's allocation fails under tight heap
 * margin, event_loop_handle stays NULL and the client crashes with
 * LoadProhibited one call later in esp_mqtt_client_register_event() (see
 * docs/decisions/ADR-024-esp-mqtt-heap-guard.md). Live-measured free
 * internal heap immediately before this call is consistently ~67.5 KB
 * post-Phase-7a (docs/decisions/ADR-023-transport-adrs-superseded.md); this
 * threshold sits well below that observed margin so it only trips if
 * something regresses heap usage upstream of this call, not under today's
 * normal operating conditions. This is a TOCTOU guard, not a real fix - it
 * narrows the crash window but can't close it (heap could still drop
 * between this check and esp_mqtt_client_init() itself). */
#ifndef EPM_MQTT_MIN_FREE_HEAP_BYTES
#define EPM_MQTT_MIN_FREE_HEAP_BYTES 32768
#endif

#define TOPIC_BUF_SIZE 32

static char s_node_id[NODE_ID_LEN + 1];
static char s_data_topic[TOPIC_BUF_SIZE];
static char s_cmd_topic[TOPIC_BUF_SIZE];

static esp_mqtt_client_handle_t s_client;
static SemaphoreHandle_t s_mutex;
static volatile bool s_connected;
static transport_cmd_fn s_cmd_handler;

static struct link_mqtt_stats s_stats;

static void mqtt_event_handler(void *handler_args, esp_event_base_t base, int32_t event_id,
				void *event_data)
{
	(void)handler_args;
	(void)base;

	esp_mqtt_event_handle_t event = (esp_mqtt_event_handle_t)event_data;

	switch ((esp_mqtt_event_id_t)event_id) {
	case MQTT_EVENT_CONNECTED:
		s_connected = true;
		s_stats.connects++;
		s_stats.consecutive_disconnects = 0;
		esp_mqtt_client_subscribe(s_client, s_cmd_topic, 1);
		ESP_LOGI(TAG, "connected, subscribed to %s", s_cmd_topic);
		break;

	case MQTT_EVENT_DISCONNECTED:
		s_connected = false;
		s_stats.disconnects++;
		s_stats.consecutive_disconnects++;
		ESP_LOGW(TAG, "disconnected");
		break;

	case MQTT_EVENT_DATA: {
		/* Ignore fragmented deliveries larger than our buffer would
		 * ever need - every real cmd message fits in one publish. */
		if (event->current_data_offset != 0 || event->data_len != event->total_data_len) {
			ESP_LOGW(TAG, "dropping fragmented cmd message (%d/%d bytes)",
				 event->data_len, event->total_data_len);
			break;
		}

		uint8_t type;
		const uint8_t *payload;
		size_t payload_len;

		if (!mqtt_decode_message((const uint8_t *)event->data, (size_t)event->data_len,
					  &type, &payload, &payload_len)) {
			ESP_LOGW(TAG, "malformed cmd message (%d bytes)", event->data_len);
			break;
		}

		s_stats.cmds_received++;

		if (type == MQTT_MSG_TYPE_STATUS_LED) {
			struct display_rgb_payload rgb;

			if (!mqtt_decode_status_led(payload, payload_len, &rgb)) {
				ESP_LOGW(TAG, "STATUS_LED payload too short (%u < %u)",
					 (unsigned)payload_len,
					 (unsigned)sizeof(struct display_rgb_payload));
				break;
			}

			ESP_LOGI(TAG, "STATUS_LED rgb=0x%06lx mode=%u period_ms=%u",
				 (unsigned long)rgb.rgb, (unsigned)rgb.mode,
				 (unsigned)rgb.period_ms);
		}

		if (s_cmd_handler != NULL) {
			s_cmd_handler(type, payload, payload_len);
		}
		break;
	}

	default:
		break;
	}
}

int transport_init(void)
{
	if (s_mutex == NULL) {
		s_mutex = xSemaphoreCreateMutex();
		if (s_mutex == NULL) {
			return -ENOMEM;
		}
	}

	/* link_mqtt_start() calls this on every retry, not just once at boot
	 * (see its own comment below on the destroy-through-start sequence).
	 * consecutive_disconnects has to survive that repeated reset, or a
	 * link_mqtt_start() that never gets far enough to connect - stuck
	 * below the ADR-024 heap margin, e.g. - would silently zero the
	 * ADR-036 watchdog's counter on every 2s retry and never trip it. */
	uint32_t consecutive_disconnects = s_stats.consecutive_disconnects;

	memset(&s_stats, 0, sizeof(s_stats));
	s_stats.consecutive_disconnects = consecutive_disconnects;
	s_connected = false;
	s_cmd_handler = NULL;

	return 0;
}

const char *transport_node_id(void)
{
	return s_node_id;
}

int transport_publish_spectrum(const uint8_t *message, size_t len)
{
	if (message == NULL || len == 0) {
		return -EINVAL;
	}

	if (!s_connected || s_client == NULL) {
		return -ENOTCONN;
	}

	xSemaphoreTake(s_mutex, portMAX_DELAY);
	int msg_id = esp_mqtt_client_publish(s_client, s_data_topic, (const char *)message,
					      (int)len, 0, 0);
	xSemaphoreGive(s_mutex);

	if (msg_id < 0) {
		s_stats.publish_failures++;
		return -EIO;
	}

	s_stats.publishes++;
	return 0;
}

bool transport_is_connected(void)
{
	return s_connected;
}

int transport_set_cmd_handler(transport_cmd_fn fn)
{
	if (fn == NULL && s_cmd_handler == NULL) {
		return 0;
	}

	s_cmd_handler = fn;
	return 0;
}

int link_mqtt_start(void)
{
	int rc = transport_init();

	if (rc != 0) {
		return rc;
	}

	size_t free_heap = heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);

	if (free_heap < EPM_MQTT_MIN_FREE_HEAP_BYTES) {
		ESP_LOGE(TAG, "free heap %u below %u-byte MQTT init safety margin - "
			 "skipping esp_mqtt_client_init() (see ADR-024)",
			 (unsigned)free_heap, (unsigned)EPM_MQTT_MIN_FREE_HEAP_BYTES);
		/* Counts as a failure to connect for ADR-036's watchdog. Without
		 * this, a boot stuck below the heap margin never reaches
		 * esp_mqtt_client_init(), so MQTT_EVENT_DISCONNECTED never fires
		 * and consecutive_disconnects never climbs - the watchdog never
		 * trips and the board is stuck until an external power-cycle. */
		s_stats.consecutive_disconnects++;
		return -ENOMEM;
	}

	node_id_derive(s_node_id, sizeof(s_node_id));
	snprintf(s_data_topic, sizeof(s_data_topic), "epm/%s/data", s_node_id);
	snprintf(s_cmd_topic, sizeof(s_cmd_topic), "epm/%s/cmd", s_node_id);

	/* net_task.c retries link_mqtt_start() on any non-zero return, including
	 * esp_mqtt_client_start() failing *after* esp_mqtt_client_init() already
	 * succeeded below. Without this, a retry overwrites s_client and leaks
	 * the previous handle - esp_mqtt_client_init() allocates the client
	 * struct, its internal event loop, and I/O buffers (buffer.size +
	 * buffer.out_size = 8KB alone), none of which esp_mqtt_client_start()
	 * failing tears back down on its own.
	 *
	 * The destroy-through-start sequence below is wrapped in s_mutex -
	 * the same lock transport_publish_spectrum() takes around s_client -
	 * because in normal operation this only ever runs once, before
	 * net_task_fn()'s publish loop starts, so there was never a concurrent
	 * publisher to race against. That invariant doesn't hold for a
	 * would-be second caller (e.g. an accelerated retry-path stress test),
	 * which could otherwise observe a torn-down or half-initialised
	 * s_client mid-publish. */
	xSemaphoreTake(s_mutex, portMAX_DELAY);

	if (s_client != NULL) {
		esp_mqtt_client_destroy(s_client);
		s_client = NULL;
	}

	/* Use the provisioned broker host/port from NVS (captive portal's "MQTT
	 * Broker Host/Port" fields, saved via net_credentials_save()) if
	 * present; fall back to the compile-time EPM_MQTT_BROKER_HOST/PORT dev-
	 * bench defaults on a genuinely unprovisioned board, same pattern
	 * wifi_task.c's wifi_rf_init() already uses for WIFI_SSID/WIFI_PASS.
	 * esp_mqtt_client_init() strdup()s the hostname internally
	 * (esp_mqtt_set_if_config() in mqtt_client.c), so a stack-local `creds`
	 * is safe here - no dangling pointer once this function returns. */
	const char *broker_host = EPM_MQTT_BROKER_HOST;
	uint16_t broker_port = EPM_MQTT_BROKER_PORT;
	struct net_credentials creds;

	if (net_credentials_load(&creds) == 0 && creds.mqtt_host[0] != '\0') {
		broker_host = creds.mqtt_host;
		broker_port = creds.mqtt_port;
	}

	esp_mqtt_client_config_t cfg = {
		.broker.address.hostname = broker_host,
		.broker.address.port = broker_port,
		.broker.address.transport = MQTT_TRANSPORT_OVER_TCP,
		.credentials.client_id = s_node_id,
		.network.reconnect_timeout_ms = 2000,
		.buffer.size = 4096,
		.buffer.out_size = 4096,
		.task.stack_size = 6144,
	};

	s_client = esp_mqtt_client_init(&cfg);
	if (s_client == NULL) {
		xSemaphoreGive(s_mutex);
		/* See the heap-guard branch above: any failure to reach a live
		 * connection has to count, or the ADR-036 watchdog never sees it. */
		s_stats.consecutive_disconnects++;
		return -ENOMEM;
	}

	esp_mqtt_client_register_event(s_client, ESP_EVENT_ANY_ID, mqtt_event_handler, NULL);

	esp_err_t err = esp_mqtt_client_start(s_client);

	xSemaphoreGive(s_mutex);

	if (err != ESP_OK) {
		ESP_LOGE(TAG, "esp_mqtt_client_start failed: 0x%x", err);
		s_stats.consecutive_disconnects++;
		return -EIO;
	}

	ESP_LOGI(TAG, "node_id=%s broker=%s:%d data_topic=%s", s_node_id, broker_host, broker_port,
		 s_data_topic);

	return 0;
}

void link_mqtt_get_stats(struct link_mqtt_stats *out)
{
	if (out == NULL) {
		return;
	}

	*out = s_stats;
}
