#include "drivers/node_id.h"

#include <stdint.h>
#include <stdio.h>

#include "esp_wifi.h"

void node_id_derive(char *out, size_t out_size)
{
    uint8_t mac[6] = {0};

    esp_wifi_get_mac(WIFI_IF_STA, mac);
    snprintf(out, out_size, "%02x%02x%02x", mac[3], mac[4], mac[5]);
}
