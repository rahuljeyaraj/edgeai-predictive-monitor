#pragma once

#ifdef __cplusplus
extern "C" {
#endif

/*
 * dns_captive — minimal DNS wildcard responder (Phase 12b Task 2).
 *
 * Answers every A-record query with the provisioning AP's own IP
 * (192.168.4.1, ESP-IDF's default AP subnet — see hal_provisioning.h's
 * contract), so a client's OS connectivity-check domain resolves to us and
 * the captive-portal browser auto-opens. Ported from the well-known
 * ESP-IDF-native pattern in examples/protocols/http_server/captive_portal's
 * dns_server.c (raw UDP socket on port 53, just enough header parsing to
 * answer every query), scoped down to only the A-record wildcard response.
 */

/* Starts the responder task. Safe to call once per hal_provisioning_start(). */
void dns_captive_start(void);

/* Closes the socket and lets the responder task self-delete. Safe to call
 * even if dns_captive_start() was never called or already stopped. */
void dns_captive_stop(void);

#ifdef __cplusplus
}
#endif
