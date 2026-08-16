#ifndef HAL_PROVISIONING_H_
#define HAL_PROVISIONING_H_

#include <stdbool.h>

#include "hal/hal_credentials.h"

/*
 * AP + captive-portal WiFi/MQTT provisioning - the ESP32-native
 * counterpart to the base station's nmcli-driven Hotspot + dnsmasq
 * captive portal (docs/WIFI_ONBOARDING_PLAN.md S1). Driven entirely from
 * threads/transport_task.cpp's own loop (poll-style, no dedicated
 * FreeRTOS task) so it never contends with hal_transport.h's "sole owner
 * of the WiFi connection" invariant - transport_task.cpp decides when to
 * start/stop this and when a submission is ready to attempt.
 */

/* Brings up WiFi.softAP(ap_ssid) (open, matching the base station's own
 * deliberate "open AP for a transient, physically-supervised onboarding
 * step" choice) plus a DNS server that resolves every lookup to the AP's
 * own IP and a web server on :80 serving the one-page SSID/password/
 * broker form - the DNS-wildcard + always-redirect combination is what
 * makes a joined phone/laptop auto-pop its captive-portal browser (the
 * same mechanism real airport WiFi uses), not just a page reachable by
 * typing the IP manually. broker_prefill seeds the form's MQTT broker
 * field (last-saved value on a re-provision, or the compiled default on
 * first boot). Idempotent - a second call while already active is a
 * no-op. Returns 0 or a negative errno. */
int hal_provisioning_start(const char *ap_ssid, const char *broker_prefill);

/* Pumps the DNS + web server - both non-blocking - must be called every
 * transport_task loop iteration while hal_provisioning_active(). */
void hal_provisioning_poll(void);

/* True exactly once per technician form submission: copies the
 * submitted ssid/password/broker into *out and immediately clears the
 * internal copy (including the in-RAM password) so it's handed off
 * exactly once and never retained longer than needed. */
bool hal_provisioning_take_submission(struct node_credentials *out);

/* Reports the outcome of attempting a submission taken via
 * hal_provisioning_take_submission() - drives what the portal's /status
 * poll (and therefore the page the technician is looking at) shows next.
 * error_detail may be NULL on success. */
void hal_provisioning_report_result(bool ok, const char *error_detail);

/* Tears down the web server, DNS server, and softAP. Safe to call when
 * not active. */
void hal_provisioning_stop(void);

bool hal_provisioning_active(void);

#endif /* HAL_PROVISIONING_H_ */
