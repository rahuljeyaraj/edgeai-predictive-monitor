#include "drivers/http_form_parse.h"

#include <ctype.h>
#include <stdlib.h>
#include <string.h>

void http_form_url_decode(char *s)
{
    char *o = s;

    while (*s != '\0') {
        if (*s == '+') {
            *o++ = ' ';
            s++;
        } else if (*s == '%' && isxdigit((unsigned char)s[1]) && isxdigit((unsigned char)s[2])) {
            char hex[3] = {s[1], s[2], '\0'};
            *o++ = (char)strtol(hex, NULL, 16);
            s += 3;
        } else {
            *o++ = *s++;
        }
    }
    *o = '\0';
}

bool http_form_get_value(const char *body, const char *key, char *out, size_t out_size)
{
    if (body == NULL || key == NULL || out == NULL || out_size == 0) {
        return false;
    }

    size_t key_len = strlen(key);
    const char *p = body;

    while (*p != '\0') {
        const char *amp = strchr(p, '&');
        size_t pair_len = (amp != NULL) ? (size_t)(amp - p) : strlen(p);

        if (pair_len > key_len && p[key_len] == '=' && strncmp(p, key, key_len) == 0) {
            size_t val_len = pair_len - key_len - 1;
            if (val_len >= out_size) {
                val_len = out_size - 1;
            }
            memcpy(out, p + key_len + 1, val_len);
            out[val_len] = '\0';
            http_form_url_decode(out);
            return true;
        }

        p += pair_len;
        if (*p == '&') {
            p++;
        }
    }

    return false;
}
