#pragma once

#include <stdbool.h>

#include "drivers/net_credentials.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * http_captive — esp_http_server-based captive portal (Phase 12b Task 3).
 *
 * GET "/" serves a plain HTML setup form (SSID / password / MQTT broker
 * host+port — no JS, no external assets, since this is served from the
 * device itself before any internet path exists). POST "/submit" parses
 * the form body and stages the result for http_captive_take_submission()
 * to hand to hal_provisioning_take_submission() (components/epm_drivers/
 * provisioning.c). GET "/status" is a check-back page fed by
 * http_captive_report_result(), so a wrong-password submission gets a
 * visible inline result without ever dropping the AP the operator is
 * connected through. OS captive-portal probe paths, and any other path,
 * redirect to "/" — the HTTP-side mirror of dns_captive.c's DNS wildcard.
 */

int http_captive_start(void);
void http_captive_stop(void);

/*
 * Non-blocking poll, consuming: if a submission has arrived since the last
 * call that returned true, copies it into *out and returns true. Mirrors
 * hal_provisioning_take_submission()'s contract exactly — this is that
 * function's real implementation, called through provisioning.c.
 */
bool http_captive_take_submission(struct net_credentials *out);

/* Feeds hal_provisioning_report_result()'s outcome into the "/status" page. */
void http_captive_report_result(bool success);

#ifdef __cplusplus
}
#endif
