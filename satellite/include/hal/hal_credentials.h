#ifndef HAL_CREDENTIALS_H_
#define HAL_CREDENTIALS_H_

#include <stdbool.h>
#include <stdint.h>

/*
 * Persisted WiFi/MQTT identity - the ESP32 NVS (via Preferences.h)
 * counterpart to the base station's NetworkManager-owned
 * *.nmconnection persistence (docs/WIFI_ONBOARDING_PLAN.md S1's
 * "Implementation notes"). Written only after a confirmed-good STA join
 * (threads/transport_task.cpp's STA_TESTING state) - an unverified
 * provisioning-portal submission is never persisted.
 */

#define CREDS_SSID_MAX_LEN   32
#define CREDS_PASS_MAX_LEN   64
#define CREDS_BROKER_MAX_LEN 64

struct node_credentials {
	char wifi_ssid[CREDS_SSID_MAX_LEN + 1];
	char wifi_password[CREDS_PASS_MAX_LEN + 1];
	char mqtt_broker_host[CREDS_BROKER_MAX_LEN + 1];
	uint16_t mqtt_broker_port;
};

/* Loads the saved credentials into *out (zeroed first either way).
 * Returns false if nothing has been saved yet (first boot / cleared). */
bool hal_credentials_load(struct node_credentials *out);

/* Persists creds, overwriting whatever was saved before. Returns 0 or a
 * negative errno. */
int hal_credentials_save(const struct node_credentials *creds);

/* Erases whatever's saved, so the next hal_credentials_load() returns
 * false exactly like a never-provisioned node. Safe to call when nothing
 * is saved. Returns 0 or a negative errno. */
int hal_credentials_clear(void);

#endif /* HAL_CREDENTIALS_H_ */
