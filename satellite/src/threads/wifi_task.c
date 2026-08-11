/*
 * wifi_task.c — WiFi STA lifecycle + power management.
 *
 * Responsibilities:
 *   1. Bring up WiFi STA and join WIFI_SSID / WIFI_PASS
 *   2. Wait for IP assignment (event group bit)
 *   3. Cap TX power and configure dynamic CPU frequency scaling
 *
 * Revived from src/threads/tcp_task.c (Phase 7a) once the raw-TCP+AES
 * transport that used to share that file was retired in favor of MQTT
 * (docs/decisions/ADR-023-transport-adrs-superseded.md). See
 * docs/decisions/ADR-022-wifi-task-revived.md for why this lifecycle+PM
 * code got its own file again instead of folding into net_task.c.
 *
 * On WIFI_EVENT_STA_DISCONNECTED: clears WIFI_CONNECTED_BIT and reconnects.
 */

#include <string.h>
#include <errno.h>

#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"

#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_pm.h"
#include "esp_timer.h"

#include "epm_config.h"
#include "hal/hal_display.h"
#include "hal/hal_provisioning.h"
#include "threads/wifi_task.h"
#include "drivers/net_credentials.h"

static const char *TAG = "wifi_task";

#define WIFI_CONNECTED_BIT  BIT0
#define WIFI_MAX_RETRY      10

/* docs/performance/SATELLITE_STRESS_STABILITY_TEST.md's "captive-portal HTTP
 * server starves under extended STA retry churn" finding: with the AP up
 * under WIFI_MODE_APSTA during PROVISIONING, the immediate esp_wifi_connect()
 * retry below cycles roughly every ~3s against an unreachable saved SSID
 * (each attempt needs its own scan to find it) -- confirmed at ~700+ cycles
 * over 39 minutes to fragment internal heap badly enough (largest_free down
 * to 7168 bytes while total free stayed flat) that the portal's HTTP server
 * can't reliably allocate a response buffer. Backing off to this interval
 * *only* while hal_provisioning_active() is true cuts that churn roughly
 * 6x without touching the already-validated immediate-retry behavior used
 * everywhere else (normal operation, and the RECOVERING state's own 60s
 * silent-reconnect window, which both still want the fast path). */
#define PROVISIONING_RETRY_BACKOFF_MS 20000

/* Mirrors link_mqtt.c's own MQTT broker defaults, not shared via a header:
 * epm_drivers must not depend back on the main component's config (see
 * link_mqtt.c's header comment), and net_credentials.c must not depend on
 * either side (see drivers/net_credentials.h). Both files use the same
 * macro name and default literal so a single -DEPM_MQTT_BROKER_HOST=...
 * build_flag in platformio.ini still overrides both consistently. Only
 * used here to seed NVS's mqtt_broker_host/port fields on a genuinely
 * first boot; net_task/link_mqtt.c do not read these fields back from NVS
 * yet — that wiring is Phase 12b's job, once a provisioning submission can
 * actually carry a non-default broker address. */
#ifndef EPM_MQTT_BROKER_HOST
#define EPM_MQTT_BROKER_HOST "10.42.0.1"
#endif
#ifndef EPM_MQTT_BROKER_PORT
#define EPM_MQTT_BROKER_PORT 1883
#endif

/* ---------- module state ---------- */

static EventGroupHandle_t s_wifi_event_group  = NULL;
static int                s_retry_cnt         = 0;
static uint32_t           s_connects          = 0;
static uint32_t           s_disconnects       = 0;

/* SSID currently configured via esp_wifi_set_config() — set in
 * wifi_rf_init() and wifi_sta_reconfigure(), read by the event handlers
 * below for logging only (not used for any connection decision). */
static char s_current_ssid[NET_CRED_SSID_MAX_LEN + 1] = {0};

/* Lazily created on first use inside PROVISIONING -- see
 * PROVISIONING_RETRY_BACKOFF_MS above. */
static esp_timer_handle_t s_reconnect_backoff_timer;

/* JTAG-readable: 0=init 1=rf_init_done 2=sta_start 3=connecting 4=got_ip */
volatile uint32_t         g_wifi_debug_state  = 0;

/* ---------- WiFi event sub-handlers ---------- */

static void on_wifi_sta_start(void)
{
    g_wifi_debug_state = 2;  /* sta_start event received */
    rgb_led_set_state(RGB_WIFI_CONN);
    ESP_LOGI(TAG, "STA started — connecting to \"%s\"...", s_current_ssid);
    g_wifi_debug_state = 3;  /* esp_wifi_connect() about to be called */
    esp_wifi_connect();
}

static void reconnect_backoff_cb(void *arg)
{
    (void)arg;
    esp_wifi_connect();
}

static void on_wifi_disconnected(wifi_event_sta_disconnected_t *d)
{
    g_wifi_debug_state = 3;  /* back to connecting state */
    rgb_led_set_state(RGB_WIFI_CONN);
    xEventGroupClearBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    s_retry_cnt++;
    s_disconnects++;
    const char *reason_str =
        (d->reason == 200) ? "BEACON_TIMEOUT"         :
        (d->reason == 201) ? "NO_AP_FOUND"            :
        (d->reason == 202) ? "AUTH_FAIL"              :
        (d->reason == 210) ? "NO_AP_COMPAT_SECURITY"  :
        (d->reason == 211) ? "NO_AP_AUTHMODE"         :
        (d->reason == 212) ? "NO_AP_RSSI"             :
        (d->reason == 15)  ? "4WAY_TIMEOUT"           :
        (d->reason == 17)  ? "IE_4WAY_DIFFERS"        :
        (d->reason == 8)   ? "ASSOC_LEAVE"            : "OTHER";
    ESP_LOGW(TAG, "Disconnect reason: %s (%d) attempt=%d",
             reason_str, d->reason, s_retry_cnt);
    if (s_retry_cnt % WIFI_MAX_RETRY == 0) {
        ESP_LOGE(TAG, "WiFi: %d consecutive failures [%s] on \"%s\" — verify "
                 "saved credentials (NVS epm_net namespace, seeded from "
                 "wifi_creds.h on first boot)", s_retry_cnt, reason_str, s_current_ssid);
    }
    /* Do NOT vTaskDelay here — this runs in the system event loop.
     * Blocking triggers the interrupt watchdog. During PROVISIONING, defer
     * the retry to a timer instead of calling esp_wifi_connect() straight
     * away — see PROVISIONING_RETRY_BACKOFF_MS. Everywhere else (including
     * the RECOVERING state that precedes PROVISIONING), retry immediately,
     * unchanged from before. */
    if (hal_provisioning_active()) {
        if (s_reconnect_backoff_timer == NULL) {
            const esp_timer_create_args_t args = {
                .callback = reconnect_backoff_cb,
                .name     = "wifi_prov_backoff",
            };
            if (esp_timer_create(&args, &s_reconnect_backoff_timer) != ESP_OK) {
                esp_wifi_connect(); /* fall back to immediate retry */
                return;
            }
        }
        esp_timer_stop(s_reconnect_backoff_timer); /* no-op if not running */
        esp_timer_start_once(s_reconnect_backoff_timer,
                              (uint64_t)PROVISIONING_RETRY_BACKOFF_MS * 1000ULL);
        return;
    }
    esp_wifi_connect();
}

static void on_got_ip(ip_event_got_ip_t *ev)
{
    g_wifi_debug_state = 4;  /* IP obtained */
    ESP_LOGI(TAG, "Got IP: " IPSTR " (after %d attempt(s))",
             IP2STR(&ev->ip_info.ip), s_retry_cnt + 1);
    s_retry_cnt = 0;
    s_connects++;
    rgb_led_set_state(RGB_TCP_CONN);
    xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
}

static void wifi_event_handler(void *arg, esp_event_base_t event_base,
                                int32_t event_id, void *event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        on_wifi_sta_start();
        return;
    }
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        on_wifi_disconnected((wifi_event_sta_disconnected_t *)event_data);
        return;
    }
    if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        on_got_ip((ip_event_got_ip_t *)event_data);
    }
}

/* ---------- public API ---------- */

/*
 * Phase 1: WiFi RF init — call BEFORE any I2S/DMA tasks are started.
 *
 * I2S DMA interrupts firing during WiFi's RF scan phase disrupt the WiFi
 * firmware's internal RF state-machine timing, causing TG1WDT_SYS_RST at
 * ~600 ms on every boot.  Starting WiFi before the I2S engine is armed
 * eliminates that interference window entirely.
 */
void wifi_rf_init(void)
{
    g_wifi_debug_state = 1;  /* rf_init started */
    s_wifi_event_group = xEventGroupCreate();
    configASSERT(s_wifi_event_group != NULL);

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    /* Register handlers AFTER esp_wifi_init() — this is the required order:
     * esp_wifi_init() sets up the WIFI_EVENT posting infrastructure; handlers
     * registered before it are placed in the default event loop but may never
     * receive WIFI_EVENT_STA_START on some IDF versions. */
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                               &wifi_event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                               &wifi_event_handler, NULL));

    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));

    /* Phase 12a: join with whatever's saved in NVS (net_credentials.c)
     * instead of the compiled WIFI_SSID/WIFI_PASS directly. On a genuinely
     * first boot (nothing saved yet) this seeds NVS from WIFI_SSID/
     * WIFI_PASS/EPM_MQTT_BROKER_HOST/PORT first — the dev-bench escape
     * hatch — so behavior here is unchanged from before this phase unless
     * a real provisioning submission has since been saved. */
    struct net_credentials creds;
    int cred_rc = net_credentials_load(&creds);
    if (cred_rc == -ENOENT) {
        struct net_credentials seed = {0};
        strncpy(seed.wifi_ssid, WIFI_SSID, sizeof(seed.wifi_ssid) - 1);
        strncpy(seed.wifi_password, WIFI_PASS, sizeof(seed.wifi_password) - 1);
        strncpy(seed.mqtt_host, EPM_MQTT_BROKER_HOST, sizeof(seed.mqtt_host) - 1);
        seed.mqtt_port = EPM_MQTT_BROKER_PORT;
        net_credentials_seed_defaults(&seed);
        cred_rc = net_credentials_load(&creds);
    }
    if (cred_rc != 0) {
        ESP_LOGE(TAG, "net_credentials_load failed (%d) — falling back to "
                 "compiled WIFI_SSID/WIFI_PASS directly", cred_rc);
        memset(&creds, 0, sizeof(creds));
        strncpy(creds.wifi_ssid, WIFI_SSID, sizeof(creds.wifi_ssid) - 1);
        strncpy(creds.wifi_password, WIFI_PASS, sizeof(creds.wifi_password) - 1);
    }

#if CONFIG_EPM_PROVISIONING_STARVATION_TEST
    /* TEMPORARY, test-only. Deterministic repro/verify aid for the
     * captive-portal HTTP server starvation documented in
     * docs/performance/SATELLITE_STRESS_STABILITY_TEST.md. Substitutes an
     * in-RAM bogus SSID/password so the very first join attempt fails and
     * wifi_provision_task.c's own state machine carries the board into
     * PROVISIONING, without ever touching NVS or the real saved
     * credentials loaded above. */
    strncpy(creds.wifi_ssid, "EPM_STARVATION_TEST_NONEXISTENT", sizeof(creds.wifi_ssid) - 1);
    creds.wifi_ssid[sizeof(creds.wifi_ssid) - 1] = '\0';
    strncpy(creds.wifi_password, "wrongpassword123", sizeof(creds.wifi_password) - 1);
    creds.wifi_password[sizeof(creds.wifi_password) - 1] = '\0';
    ESP_LOGW(TAG, "EPM_PROVISIONING_STARVATION_TEST: forcing bad SSID \"%s\"",
             creds.wifi_ssid);
#endif

    wifi_config_t wifi_cfg = {
        .sta = {
            /* WPA_WPA2_PSK: accept WPA or WPA2 AP.  Avoids the silent stall
             * caused by WIFI_AUTH_WPA2_PSK + pmf_capable=true on Windows Mobile
             * Hotspot (WPA2-Personal without 802.11w). */
            .threshold.authmode = WIFI_AUTH_WPA_WPA2_PSK,
            .pmf_cfg            = { .capable = false, .required = false },
        },
    };
    strncpy((char *)wifi_cfg.sta.ssid, creds.wifi_ssid, sizeof(wifi_cfg.sta.ssid) - 1);
    strncpy((char *)wifi_cfg.sta.password, creds.wifi_password, sizeof(wifi_cfg.sta.password) - 1);
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg));
    strncpy(s_current_ssid, creds.wifi_ssid, sizeof(s_current_ssid) - 1);
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    ESP_ERROR_CHECK(esp_wifi_start());

    /* HW-OPT: cap TX power to 17 dBm to reduce peak current draw on the
     * XIAO USB-C 3.3 V rail.  Negligible range loss at <10 m deployment.
     * Unit: quarter-dBm.  WIFI_TX_POWER_QTR_DBM=68 → 17.0 dBm. */
    if (esp_wifi_set_max_tx_power(WIFI_TX_POWER_QTR_DBM) == ESP_OK) {
        ESP_LOGI(TAG, "WiFi TX power capped to %.1f dBm (%d q-dBm)",
                 WIFI_TX_POWER_QTR_DBM / 4.0f, (int)WIFI_TX_POWER_QTR_DBM);
    } else {
        ESP_LOGW(TAG, "esp_wifi_set_max_tx_power failed — using default");
    }

    /* Dynamic CPU frequency scaling: 240 MHz during active DSP/net bursts,
     * 80 MHz during idle gaps between frames.  Requires CONFIG_PM_ENABLE=y
     * and CONFIG_FREERTOS_USE_TICKLESS_IDLE=y in sdkconfig.defaults.
     *
     * Previously configured inside tcp_task.c's task loop, after the first
     * successful connection (docs/decisions/ADR-015). Now that there is no
     * task loop, this runs here instead — at RF init, before WIFI_PS_NONE
     * or the connection even exists — which is strictly earlier than
     * before, so no window opens where DFS is unconfigured that didn't
     * already exist previously. */
    esp_pm_config_t pm_cfg = {
        .max_freq_mhz       = 240,
        .min_freq_mhz       = 80,
        .light_sleep_enable = false,
    };
    if (esp_pm_configure(&pm_cfg) != ESP_OK) {
        ESP_LOGW(TAG, "esp_pm_configure failed — fixed 240 MHz");
    }
    /* WIFI_PS_NONE (set above) must not be overridden elsewhere.
     * WIFI_PS_MIN_MODEM causes the radio to enter DTIM sleep between beacon
     * intervals, which upstream code (net_task's MQTT keepalive) relies on
     * not happening. */

    ESP_LOGI(TAG, "WiFi RF init — SSID: \"%s\"", creds.wifi_ssid);
}

/* Phase 2: block until IP assigned (or timeout). Returns true if connected. */
bool wifi_wait_connected(TickType_t ticks_to_wait)
{
    EventBits_t bits = xEventGroupWaitBits(s_wifi_event_group,
                                           WIFI_CONNECTED_BIT,
                                           pdFALSE, pdTRUE,
                                           ticks_to_wait);
    return (bits & WIFI_CONNECTED_BIT) != 0;
}

void wifi_task_get_stats(struct wifi_task_stats *out)
{
    if (out == NULL) return;
    out->connects    = s_connects;
    out->disconnects = s_disconnects;
    out->retry_cnt   = (uint32_t)s_retry_cnt;
}

void wifi_sta_reconfigure(const char *ssid, const char *password)
{
    wifi_config_t wifi_cfg = {
        .sta = {
            .threshold.authmode = WIFI_AUTH_WPA_WPA2_PSK,
            .pmf_cfg            = { .capable = false, .required = false },
        },
    };
    strncpy((char *)wifi_cfg.sta.ssid, ssid, sizeof(wifi_cfg.sta.ssid) - 1);
    strncpy((char *)wifi_cfg.sta.password, password, sizeof(wifi_cfg.sta.password) - 1);

    /* esp_wifi_disconnect() first: esp_wifi_set_config() while a connect/
     * retry cycle is in flight does not interrupt it, so without this the
     * STA could keep retrying the OLD credentials for up to WIFI_MAX_RETRY
     * attempts before ever trying the new ones. */
    esp_wifi_disconnect();
    xEventGroupClearBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg));
    strncpy(s_current_ssid, ssid, sizeof(s_current_ssid) - 1);
    s_current_ssid[sizeof(s_current_ssid) - 1] = '\0';
    s_retry_cnt = 0;
    ESP_LOGI(TAG, "STA reconfigured — connecting to \"%s\"...", s_current_ssid);
    esp_wifi_connect();
}
