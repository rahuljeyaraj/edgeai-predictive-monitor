#include <errno.h>
#include <string.h>

#include <Preferences.h>

#include "hal/hal_credentials.h"

/*
 * Implements hal_credentials.h using ESP32 NVS (Preferences.h) - the
 * bundled arduino-esp32 core wrapper around the same nvs_ flash
 * partition WiFiManager-style provisioning libraries use, no extra
 * platformio.ini lib_deps needed.
 */

#define NVS_NAMESPACE "epm_net"
#define NVS_KEY_SSID   "ssid"
#define NVS_KEY_PASS   "pass"
#define NVS_KEY_BROKER "broker"
#define NVS_KEY_PORT   "port"

bool hal_credentials_load(struct node_credentials *out)
{
	memset(out, 0, sizeof(*out));

	Preferences prefs;

	if (!prefs.begin(NVS_NAMESPACE, /*readOnly=*/true)) {
		return false;
	}

	bool have_ssid = prefs.isKey(NVS_KEY_SSID);

	if (have_ssid) {
		prefs.getString(NVS_KEY_SSID, out->wifi_ssid, sizeof(out->wifi_ssid));
		prefs.getString(NVS_KEY_PASS, out->wifi_password, sizeof(out->wifi_password));
		prefs.getString(NVS_KEY_BROKER, out->mqtt_broker_host, sizeof(out->mqtt_broker_host));
		out->mqtt_broker_port = (uint16_t)prefs.getUInt(NVS_KEY_PORT, 1883);
	}

	prefs.end();

	return have_ssid && out->wifi_ssid[0] != '\0';
}

int hal_credentials_save(const struct node_credentials *creds)
{
	Preferences prefs;

	if (!prefs.begin(NVS_NAMESPACE, /*readOnly=*/false)) {
		return -EIO;
	}

	prefs.putString(NVS_KEY_SSID, creds->wifi_ssid);
	prefs.putString(NVS_KEY_PASS, creds->wifi_password);
	prefs.putString(NVS_KEY_BROKER, creds->mqtt_broker_host);
	prefs.putUInt(NVS_KEY_PORT, creds->mqtt_broker_port);

	prefs.end();

	return 0;
}
