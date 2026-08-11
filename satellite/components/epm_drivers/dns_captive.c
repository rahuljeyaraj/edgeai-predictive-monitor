#include "drivers/dns_captive.h"

#include <errno.h>
#include <string.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/sockets.h"

static const char *TAG = "dns_captive";

#define DNS_PORT        53
#define DNS_MAX_LEN     512
#define DNS_TASK_STACK  4096
#define DNS_TASK_PRIO   4

/* IDF's default provisioning-AP address (esp_netif_create_default_wifi_ap()'s
 * default subnet) — hardcoded per hal_provisioning.h's documented contract
 * rather than queried at runtime, since the task explicitly keeps that
 * default rather than changing it. Raw octets, not a host/network-order
 * integer: DNS RDATA for an A record is just these 4 bytes in wire order. */
static const uint8_t s_ap_ip_bytes[4] = {192, 168, 4, 1};

#pragma pack(push, 1)
struct dns_header {
    uint16_t id;
    uint16_t flags;
    uint16_t qdcount;
    uint16_t ancount;
    uint16_t nscount;
    uint16_t arcount;
};

struct dns_answer {
    uint16_t name_ptr; /* 0xC00C: pointer back to the question's QNAME at offset 12 */
    uint16_t type;     /* 1 = A */
    uint16_t class;    /* 1 = IN */
    uint32_t ttl;
    uint16_t rdlength;
    uint8_t  rdata[4];
};
#pragma pack(pop)

static TaskHandle_t s_task;
static int s_sock = -1;

static void dns_task_fn(void *arg)
{
    (void)arg;
    static uint8_t rx[DNS_MAX_LEN];
    static uint8_t tx[DNS_MAX_LEN];

    while (1) {
        struct sockaddr_in from;
        socklen_t fromlen = sizeof(from);
        int len = recvfrom(s_sock, rx, sizeof(rx), 0, (struct sockaddr *)&from, &fromlen);
        if (len < (int)sizeof(struct dns_header)) {
            if (len < 0) {
                break; /* socket closed by dns_captive_stop() or a real error */
            }
            continue;
        }

        const struct dns_header *hdr_in = (const struct dns_header *)rx;
        if ((ntohs(hdr_in->flags) & 0x8000) != 0 || ntohs(hdr_in->qdcount) != 1) {
            continue; /* not a query, or more than the one question we handle */
        }

        /* Question section starts right after the 12-byte header: a
         * length-prefixed QNAME terminated by a zero-length label, then
         * QTYPE(2) + QCLASS(2). We don't need to inspect any of it beyond
         * finding where it ends — every query gets the same wildcard A
         * answer regardless of QTYPE/QNAME. */
        int qpos = (int)sizeof(struct dns_header);
        while (qpos < len && rx[qpos] != 0) {
            qpos += rx[qpos] + 1;
        }
        qpos += 1; /* terminating zero-length label */
        int qend = qpos + 4; /* QTYPE + QCLASS */
        if (qend > len || qend > (int)sizeof(tx)) {
            continue;
        }

        memcpy(tx, rx, (size_t)qend);
        struct dns_header *hdr_out = (struct dns_header *)tx;
        hdr_out->flags   = htons(0x8180); /* response, recursion available, no error */
        hdr_out->qdcount = htons(1);
        hdr_out->ancount = htons(1);
        hdr_out->nscount = 0;
        hdr_out->arcount = 0;

        struct dns_answer ans = {
            .name_ptr = htons(0xC00C),
            .type     = htons(1),
            .class    = htons(1),
            .ttl      = htonl(60),
            .rdlength = htons(sizeof(s_ap_ip_bytes)),
        };
        memcpy(ans.rdata, s_ap_ip_bytes, sizeof(s_ap_ip_bytes));

        if ((size_t)qend + sizeof(ans) > sizeof(tx)) {
            continue;
        }
        memcpy(tx + qend, &ans, sizeof(ans));

        sendto(s_sock, tx, (size_t)qend + sizeof(ans), 0, (struct sockaddr *)&from, fromlen);
    }

    s_task = NULL;
    vTaskDelete(NULL);
}

void dns_captive_start(void)
{
    if (s_task != NULL) {
        return; /* already running */
    }

    s_sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (s_sock < 0) {
        ESP_LOGE(TAG, "socket() failed: errno %d", errno);
        return;
    }

    struct sockaddr_in addr = {
        .sin_family      = AF_INET,
        .sin_port        = htons(DNS_PORT),
        .sin_addr.s_addr = htonl(INADDR_ANY),
    };
    if (bind(s_sock, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        ESP_LOGE(TAG, "bind() failed: errno %d", errno);
        close(s_sock);
        s_sock = -1;
        return;
    }

    if (xTaskCreate(dns_task_fn, "dns_captive", DNS_TASK_STACK, NULL, DNS_TASK_PRIO, &s_task) != pdPASS) {
        ESP_LOGE(TAG, "xTaskCreate failed");
        close(s_sock);
        s_sock = -1;
        s_task = NULL;
    }
}

void dns_captive_stop(void)
{
    if (s_sock >= 0) {
        /* Closing the socket while dns_task_fn() is blocked in recvfrom()
         * on it unblocks that call with an error — the standard ESP-IDF
         * pattern for tearing down this kind of socket task (same shape as
         * the upstream dns_server.c example this file is ported from). The
         * task then self-deletes; no join needed here. */
        close(s_sock);
        s_sock = -1;
    }
}
