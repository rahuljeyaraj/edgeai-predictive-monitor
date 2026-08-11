#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * net_credentials — NVS-backed WiFi + MQTT broker credential storage,
 * added in Phase 12a (this project's WiFi-provisioning phase).
 *
 * Pure NVS wrapper: this module knows nothing about wifi_creds.h or
 * epm_config.h (epm_drivers must not depend back on the main component's
 * config — see components/epm_drivers/link_mqtt.c's header comment for why
 * that dependency direction is forbidden). Callers that want a dev-bench
 * seed build the default struct themselves from their own compile-time
 * config and pass it to net_credentials_seed_defaults().
 *
 * Namespace: "epm_net" in the default NVS partition. nvs_flash_init() must
 * already have run (src/main.c does this before any task starts).
 */

#define NET_CRED_SSID_MAX_LEN      32 /* IEEE 802.11 SSID octet limit */
#define NET_CRED_PASSWORD_MAX_LEN  64 /* matches wifi_config_t.sta.password's buffer size */
#define NET_CRED_MQTT_HOST_MAX_LEN 64 /* hostname or dotted-quad IPv4 */

struct net_credentials {
    char     wifi_ssid[NET_CRED_SSID_MAX_LEN + 1];
    char     wifi_password[NET_CRED_PASSWORD_MAX_LEN + 1];
    char     mqtt_host[NET_CRED_MQTT_HOST_MAX_LEN + 1];
    uint16_t mqtt_port;
};

/*
 * Loads saved credentials from NVS. Returns 0 with *out fully populated if
 * something was actually saved, -ENOENT if the epm_net namespace has never
 * been written (out left untouched — callers must not treat that as "saved
 * empty credentials"), or another -errno on a real NVS failure.
 */
int net_credentials_load(struct net_credentials *out);

/*
 * Persists creds to NVS: all four fields staged into one handle and
 * committed once, so a crash or power loss between two fields can never
 * leave net_credentials_load() returning a mix of old and new values.
 * Returns 0 on success, -errno on failure.
 */
int net_credentials_save(const struct net_credentials *creds);

/*
 * Dev-bench escape hatch: writes `defaults` to NVS only if
 * net_credentials_load() reports nothing saved yet — never overwrites
 * real, already-provisioned credentials, matching the reference design's
 * "only on a genuinely first boot" rule. Returns 0 whether it seeded or
 * found existing credentials already saved; -errno on a real NVS failure.
 */
int net_credentials_seed_defaults(const struct net_credentials *defaults);

#ifdef __cplusplus
}
#endif
