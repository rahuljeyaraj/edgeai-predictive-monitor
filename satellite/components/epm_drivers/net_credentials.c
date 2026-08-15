#include "drivers/net_credentials.h"

#include <errno.h>

#include "esp_log.h"
#include "nvs.h"

/*
 * NVS-backed WiFi + MQTT broker credential storage — Phase 12a Task 1.
 * See drivers/net_credentials.h for the module's scope/dependency rules.
 */

static const char *TAG = "net_credentials";

#define NVS_NAMESPACE     "epm_net"
#define NVS_KEY_SSID      "wifi_ssid"
#define NVS_KEY_PASS      "wifi_pass"
#define NVS_KEY_MQTT_HOST "mqtt_host"
#define NVS_KEY_MQTT_PORT "mqtt_port"

int net_credentials_load(struct net_credentials *out)
{
    if (out == NULL) {
        return -EINVAL;
    }

    nvs_handle_t h;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READONLY, &h);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        return -ENOENT;
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nvs_open (read) failed: 0x%x", err);
        return -EIO;
    }

    struct net_credentials tmp = {0};
    size_t len;

    len = sizeof(tmp.wifi_ssid);
    err = nvs_get_str(h, NVS_KEY_SSID, tmp.wifi_ssid, &len);
    if (err == ESP_OK) {
        len = sizeof(tmp.wifi_password);
        err = nvs_get_str(h, NVS_KEY_PASS, tmp.wifi_password, &len);
    }
    if (err == ESP_OK) {
        len = sizeof(tmp.mqtt_host);
        err = nvs_get_str(h, NVS_KEY_MQTT_HOST, tmp.mqtt_host, &len);
    }
    if (err == ESP_OK) {
        err = nvs_get_u16(h, NVS_KEY_MQTT_PORT, &tmp.mqtt_port);
    }

    nvs_close(h);

    if (err == ESP_ERR_NVS_NOT_FOUND) {
        return -ENOENT;
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "credential load failed: 0x%x", err);
        return -EIO;
    }

    *out = tmp;
    return 0;
}

int net_credentials_save(const struct net_credentials *creds)
{
    if (creds == NULL) {
        return -EINVAL;
    }

    nvs_handle_t h;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &h);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nvs_open (write) failed: 0x%x", err);
        return -EIO;
    }

    err = nvs_set_str(h, NVS_KEY_SSID, creds->wifi_ssid);
    if (err == ESP_OK) {
        err = nvs_set_str(h, NVS_KEY_PASS, creds->wifi_password);
    }
    if (err == ESP_OK) {
        err = nvs_set_str(h, NVS_KEY_MQTT_HOST, creds->mqtt_host);
    }
    if (err == ESP_OK) {
        err = nvs_set_u16(h, NVS_KEY_MQTT_PORT, creds->mqtt_port);
    }
    if (err == ESP_OK) {
        err = nvs_commit(h);
    }

    nvs_close(h);

    if (err != ESP_OK) {
        ESP_LOGE(TAG, "credential save failed: 0x%x", err);
        return -EIO;
    }

    ESP_LOGI(TAG, "credentials saved (ssid=\"%s\" mqtt=%s:%u)",
             creds->wifi_ssid, creds->mqtt_host, (unsigned)creds->mqtt_port);
    return 0;
}

int net_credentials_seed_defaults(const struct net_credentials *defaults)
{
    if (defaults == NULL) {
        return -EINVAL;
    }

    struct net_credentials existing;
    int rc = net_credentials_load(&existing);
    if (rc == 0) {
        return 0; /* already provisioned — never overwrite */
    }
    if (rc != -ENOENT) {
        return rc; /* real NVS failure, not "nothing saved" */
    }

    ESP_LOGW(TAG, "no saved credentials — seeding dev-bench defaults");
    return net_credentials_save(defaults);
}
