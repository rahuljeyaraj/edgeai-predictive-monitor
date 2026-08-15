#pragma once

#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * http_form_parse — pure application/x-www-form-urlencoded parsing, no
 * ESP-IDF/network dependencies. Split out of drivers/http_captive.c so
 * this logic is host-testable (tests/host/test_http_form_parse.c) even
 * though the HTTP plumbing that calls it isn't.
 */

/* Decodes a urlencoded string in place: '+' -> ' ', "%XX" -> byte XX.
 * Malformed "%" sequences (not followed by two hex digits) are copied
 * through unchanged rather than decoded. */
void http_form_url_decode(char *s);

/*
 * Finds `key=value` in an already-received x-www-form-urlencoded body
 * (fields separated by '&') and writes the decoded value into out
 * (NUL-terminated, truncated to fit out_size if necessary). Returns true
 * if key was found, false otherwise (out left untouched on failure).
 */
bool http_form_get_value(const char *body, const char *key, char *out, size_t out_size);

#ifdef __cplusplus
}
#endif
