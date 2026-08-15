#pragma once

#include <stdbool.h>

#include "drivers/net_credentials.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * hal_provisioning — contract for the captive-portal credential-submission
 * source consumed by src/threads/wifi_provision_task.c's
 * PROVISIONING/STA_TESTING states.
 *
 * Phase 12a defined this contract and shipped a no-op placeholder
 * (provisioning_stub.c) so the state machine was independently testable.
 * Phase 12b implements it for real, in components/epm_drivers/
 * provisioning.c — esp_http_server (drivers/http_captive.c) + a DNS
 * wildcard responder (drivers/dns_captive.c), matching the reference
 * satellite's hal_provisioning.h design fetched live during Phase 12a's
 * scoping.
 */

/* Starts the provisioning AP + portal. Safe to call when already active. */
void hal_provisioning_start(void);

/* Stops the provisioning AP + portal (e.g. once STA_TESTING succeeds). */
void hal_provisioning_stop(void);

/* True while the AP + portal are up. */
bool hal_provisioning_active(void);

/*
 * Non-blocking poll: if a form submission has arrived since the last call
 * that returned true, copies it into *out and returns true, consuming it —
 * a second call returns false until another submission arrives. Never
 * blocks; safe to call from a polling loop.
 */
bool hal_provisioning_take_submission(struct net_credentials *out);

/*
 * Reports the outcome of testing a submitted credential (the STA_TESTING
 * state's bounded join attempt) back to the portal, so it can show an
 * inline error on failure without dropping the AP the caller is still
 * connected through.
 */
void hal_provisioning_report_result(bool success);

#ifdef __cplusplus
}
#endif
