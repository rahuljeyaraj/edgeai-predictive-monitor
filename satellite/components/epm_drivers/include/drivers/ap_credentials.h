#pragma once

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * ap_credentials — NVS-backed provisioning-AP password (Phase 12b,
 * docs/decisions/ADR-031's Option C).
 *
 * Deliberately a sibling of drivers/net_credentials.c, not folded into it:
 * that module owns WiFi/MQTT credentials the operator submits through the
 * portal; this one owns the AP's own access password, generated locally on
 * the device and never submitted by anyone. Namespace: "epm_ap", separate
 * from net_credentials.c's "epm_net".
 */

#define AP_CRED_PASSWORD_LEN 32 /* 16 random bytes, hex-encoded — WPA2-valid (>=8 chars), human-typeable */

/*
 * Returns the provisioning AP's WPA2-PSK password: generated once via
 * esp_fill_random() (a true RNG, not a formula — see ADR-031) on the first
 * call ever, persisted in NVS, and reused unchanged on every later
 * call/boot so a password written down at first bring-up stays correct for
 * the unit's whole life. out must be at least AP_CRED_PASSWORD_LEN+1
 * bytes. Returns 0 on success, -errno on failure.
 */
int ap_credentials_get_or_create(char *out, size_t out_size);

#ifdef __cplusplus
}
#endif
