#include "drivers/ap_credentials.h"

#include <errno.h>
#include <stdint.h>

#include "esp_log.h"
#include "esp_random.h"
#include "nvs.h"

static const char *TAG = "ap_credentials";

#define NVS_NAMESPACE "epm_ap"
#define NVS_KEY_PASS  "ap_pass"

static const char s_hex[] = "0123456789abcdef";

int ap_credentials_get_or_create(char *out, size_t out_size)
{
    if (out == NULL || out_size < AP_CRED_PASSWORD_LEN + 1) {
        return -EINVAL;
    }

    nvs_handle_t h;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &h);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nvs_open failed: 0x%x", err);
        return -EIO;
    }

    size_t len = AP_CRED_PASSWORD_LEN + 1;
    err = nvs_get_str(h, NVS_KEY_PASS, out, &len);
    if (err == ESP_OK) {
        nvs_close(h);
        return 0;
    }
    if (err != ESP_ERR_NVS_NOT_FOUND) {
        ESP_LOGE(TAG, "nvs_get_str failed: 0x%x", err);
        nvs_close(h);
        return -EIO;
    }

    uint8_t raw[AP_CRED_PASSWORD_LEN / 2];
    esp_fill_random(raw, sizeof(raw));
    for (size_t i = 0; i < sizeof(raw); i++) {
        out[i * 2]     = s_hex[raw[i] >> 4];
        out[i * 2 + 1] = s_hex[raw[i] & 0x0f];
    }
    out[AP_CRED_PASSWORD_LEN] = '\0';

    err = nvs_set_str(h, NVS_KEY_PASS, out);
    if (err == ESP_OK) {
        err = nvs_commit(h);
    }
    nvs_close(h);

    if (err != ESP_OK) {
        ESP_LOGE(TAG, "failed to persist generated AP password: 0x%x", err);
        return -EIO;
    }

    ESP_LOGW(TAG, "generated new provisioning AP password (first bring-up ever for this unit)");
    return 0;
}
