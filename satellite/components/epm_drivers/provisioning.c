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
 *     see wifi_provision_task.c's PROVISIONING/STA_TESTING state machine and
 *     docs/decisions/ADR-031 for the WPA2-random-password policy this
 *     applies via drivers/ap_credentials.c;
 *   - drivers/dns_captive.c's DNS wildcard responder, so the OS
 *     captive-portal prompt auto-opens;
 *   - drivers/http_captive.c's form itself.
 */

static const char *TAG = "provisioning";

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

    char ap_pass[AP_CRED_PASSWORD_LEN + 1];
    if (ap_credentials_get_or_create(ap_pass, sizeof(ap_pass)) != 0) {
        ESP_LOGE(TAG, "failed to obtain provisioning AP password — provisioning unavailable");
        return;
    }

    wifi_config_t ap_cfg = {
        .ap = {
            .channel        = 1,
            .max_connection = 4,
            .authmode       = WIFI_AUTH_WPA2_PSK,
            .pmf_cfg        = {.capable = false, .required = false},
        },
    };
    snprintf((char *)ap_cfg.ap.ssid, sizeof(ap_cfg.ap.ssid), "EPM-SAT-%s", node_id);
    ap_cfg.ap.ssid_len = (uint8_t)strlen((char *)ap_cfg.ap.ssid);
    strncpy((char *)ap_cfg.ap.password, ap_pass, sizeof(ap_cfg.ap.password) - 1);

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

    /* Password logged once at bring-up (ADR-031): until an actual physical
     * labeling process exists (a process gap ADR-031's Consequences
     * section already flags, not a firmware one), the serial console is
     * the only channel the person provisioning the unit has to read this. */
    ESP_LOGI(TAG, "provisioning AP up: ssid=\"%s\" password=\"%s\" (WPA2-PSK, http://192.168.4.1)",
             ap_cfg.ap.ssid, ap_pass);

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
