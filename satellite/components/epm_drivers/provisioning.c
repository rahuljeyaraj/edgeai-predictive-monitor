#include "hal/hal_provisioning.h"

#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"

#include "drivers/ap_credentials.h"
#include "drivers/dns_captive.h"
#include "drivers/http_captive.h"
#include "drivers/net_credentials.h"
#include "drivers/node_id.h"

/*
 * Real hal/hal_provisioning.h implementation (Phase 12b) — replaces
 * provisioning_stub.c. Orchestrates the three pieces wifi_provision_task.c's
 * PROVISIONING/STA_TESTING states need:
 *
 *   - the AP itself, brought up as WIFI_MODE_APSTA (not a full mode switch)
 *     so STA keeps retrying/reconnecting while the portal stays reachable —
 *     see wifi_provision_task.c's PROVISIONING/STA_TESTING state machine.
 *     Auth mode is EPM_PROVISIONING_AP_OPEN-gated (defined below): open by
 *     default per ADR-041, or WPA2 with the random per-device password from
 *     drivers/ap_credentials.c per ADR-031 when that flag is set to 0;
 *   - drivers/dns_captive.c's DNS wildcard responder, so the OS
 *     captive-portal prompt auto-opens;
 *   - drivers/http_captive.c's form itself.
 */

static const char *TAG = "provisioning";

/* Overridable via platformio.ini build_flags, same pattern as
 * src/epm_config.h. Not folded into that header because epm_drivers must
 * not depend back on the main component's config (src already depends on
 * epm_drivers to call hal_provisioning_start(); the reverse would be a
 * component dependency cycle) — see link_mqtt.c's EPM_MQTT_BROKER_HOST/PORT
 * for the same precedent. Default 1 (OPEN -- no password) as of ADR-041,
 * superseding ADR-031's original WPA2-PSK-by-default decision: any phone in
 * RF range of a unit currently in provisioning mode can join its AP and
 * reach the onboarding page with no password barrier. This is a deliberate,
 * understood tradeoff for this project's deployment context (small fleet,
 * physically supervised bring-up), not an oversight -- see ADR-041. Set to
 * 0 to restore ADR-031's original WPA2-PSK, random-per-device-password
 * behavior. */
#ifndef EPM_PROVISIONING_AP_OPEN
#define EPM_PROVISIONING_AP_OPEN 1
#endif

static bool s_active;
static bool s_ap_netif_created;

static void ensure_ap_netif(void)
{
    if (!s_ap_netif_created) {
        esp_netif_create_default_wifi_ap();
        s_ap_netif_created = true;
    }
}

void hal_provisioning_start(void)
{
    if (s_active) {
        return;
    }

    ensure_ap_netif();

    char node_id[NODE_ID_LEN + 1];
    node_id_derive(node_id, sizeof(node_id));

#if !EPM_PROVISIONING_AP_OPEN
    char ap_pass[AP_CRED_PASSWORD_LEN + 1];
    if (ap_credentials_get_or_create(ap_pass, sizeof(ap_pass)) != 0) {
        ESP_LOGE(TAG, "failed to obtain provisioning AP password — provisioning unavailable");
        return;
    }
#endif

    wifi_config_t ap_cfg = {
        .ap = {
            .channel        = 1,
            .max_connection = 4,
#if EPM_PROVISIONING_AP_OPEN
            .authmode       = WIFI_AUTH_OPEN,
#else
            .authmode       = WIFI_AUTH_WPA2_PSK,
#endif
            .pmf_cfg        = {.capable = false, .required = false},
        },
    };
    snprintf((char *)ap_cfg.ap.ssid, sizeof(ap_cfg.ap.ssid), "EPM-SAT-%s", node_id);
    ap_cfg.ap.ssid_len = (uint8_t)strlen((char *)ap_cfg.ap.ssid);
#if !EPM_PROVISIONING_AP_OPEN
    strncpy((char *)ap_cfg.ap.password, ap_pass, sizeof(ap_cfg.ap.password) - 1);
#endif

    esp_err_t err = esp_wifi_set_mode(WIFI_MODE_APSTA);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_wifi_set_mode(APSTA) failed: 0x%x", err);
        return;
    }
    err = esp_wifi_set_config(WIFI_IF_AP, &ap_cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_wifi_set_config(AP) failed: 0x%x", err);
        return;
    }

#if EPM_PROVISIONING_AP_OPEN
    ESP_LOGW(TAG, "provisioning AP up: ssid=\"%s\" (OPEN -- EPM_PROVISIONING_AP_OPEN=1, "
                  "no password, see ADR-041) http://192.168.4.1", ap_cfg.ap.ssid);
#else
    /* Password logged once at bring-up (ADR-031): until an actual physical
     * labeling process exists (a process gap ADR-031's Consequences
     * section already flags, not a firmware one), the serial console is
     * the only channel the person provisioning the unit has to read this. */
    ESP_LOGI(TAG, "provisioning AP up: ssid=\"%s\" password=\"%s\" (WPA2-PSK, http://192.168.4.1)",
             ap_cfg.ap.ssid, ap_pass);
#endif

    dns_captive_start();
    if (http_captive_start() != 0) {
        ESP_LOGE(TAG, "http_captive_start failed — portal unreachable, AP still up");
    }

    s_active = true;
}

void hal_provisioning_stop(void)
{
    if (!s_active) {
        return;
    }

    http_captive_stop();
    dns_captive_stop();

    esp_err_t err = esp_wifi_set_mode(WIFI_MODE_STA);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "esp_wifi_set_mode(STA) failed: 0x%x", err);
    }

    s_active = false;
}

bool hal_provisioning_active(void)
{
    return s_active;
}

bool hal_provisioning_take_submission(struct net_credentials *out)
{
    return http_captive_take_submission(out);
}

void hal_provisioning_report_result(bool success)
{
    ESP_LOGI(TAG, "credential test result: %s", success ? "CONNECTED" : "FAILED");
    http_captive_report_result(success);
}
