#include "drivers/http_captive.h"

#include <errno.h>
#include <stdlib.h>
#include <string.h>

#include "esp_http_server.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#include "drivers/http_form_parse.h"

static const char *TAG = "http_captive";

#define MAX_BODY_LEN 768 /* worst-case percent-encoded ssid+password+host+port fields, with margin */

static httpd_handle_t   s_server;
static SemaphoreHandle_t s_mutex;

static struct net_credentials s_pending;
static bool                   s_pending_valid;

enum status_state { STATUS_IDLE = 0, STATUS_TESTING, STATUS_SUCCESS, STATUS_FAILED };
static enum status_state s_status = STATUS_IDLE;

static const char s_form_html[] =
    "<!DOCTYPE html><html><head><title>EPM Satellite Setup</title>"
    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"></head>"
    "<body style=\"font-family:sans-serif;max-width:420px;margin:2em auto;padding:0 1em\">"
    "<h2>EPM Satellite Setup</h2>"
    "<form method=\"POST\" action=\"/submit\">"
    "<label>WiFi SSID<br><input name=\"ssid\" maxlength=\"32\" required></label><br><br>"
    "<label>WiFi Password<br><input name=\"password\" type=\"password\" maxlength=\"64\"></label><br><br>"
    "<label>MQTT Broker Host<br><input name=\"mqtt_host\" maxlength=\"64\" required></label><br><br>"
    "<label>MQTT Broker Port<br><input name=\"mqtt_port\" type=\"number\" min=\"1\" max=\"65535\" value=\"1883\" required></label><br><br>"
    "<button type=\"submit\">Connect</button>"
    "</form></body></html>";

static const char s_submitted_html[] =
    "<!DOCTYPE html><html><head><title>Connecting...</title>"
    "<meta http-equiv=\"refresh\" content=\"2;url=/status\"></head>"
    "<body style=\"font-family:sans-serif;max-width:420px;margin:2em auto;padding:0 1em\">"
    "<p>Connecting&hellip; the satellite's setup network stays up during this test, "
    "so if it fails you can retry without reconnecting.</p>"
    "<p><a href=\"/status\">Check status</a></p></body></html>";

static const char s_missing_field_html[] =
    "<!DOCTYPE html><html><body style=\"font-family:sans-serif\">"
    "<p>Missing required field(s). <a href=\"/\">Back</a></p></body></html>";

static const char s_bad_port_html[] =
    "<!DOCTYPE html><html><body style=\"font-family:sans-serif\">"
    "<p>Invalid MQTT port. <a href=\"/\">Back</a></p></body></html>";

static esp_err_t handle_root(httpd_req_t *req)
{
    httpd_resp_set_type(req, "text/html");
    return httpd_resp_send(req, s_form_html, HTTPD_RESP_USE_STRLEN);
}

static esp_err_t handle_submit(httpd_req_t *req)
{
    if (req->content_len == 0 || req->content_len >= MAX_BODY_LEN) {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "form body too large");
        return ESP_FAIL;
    }

    char body[MAX_BODY_LEN];
    size_t received = 0;
    while (received < req->content_len) {
        int r = httpd_req_recv(req, body + received, req->content_len - received);
        if (r == HTTPD_SOCK_ERR_TIMEOUT) {
            continue;
        }
        if (r <= 0) {
            return ESP_FAIL;
        }
        received += (size_t)r;
    }
    body[received] = '\0';

    struct net_credentials creds = {0};
    char port_str[8] = {0};

    bool have_ssid = http_form_get_value(body, "ssid", creds.wifi_ssid, sizeof(creds.wifi_ssid));
    http_form_get_value(body, "password", creds.wifi_password, sizeof(creds.wifi_password));
    bool have_host = http_form_get_value(body, "mqtt_host", creds.mqtt_host, sizeof(creds.mqtt_host));
    bool have_port = http_form_get_value(body, "mqtt_port", port_str, sizeof(port_str));

    if (!have_ssid || !have_host || !have_port || creds.wifi_ssid[0] == '\0') {
        httpd_resp_set_type(req, "text/html");
        return httpd_resp_send(req, s_missing_field_html, HTTPD_RESP_USE_STRLEN);
    }

    long port = strtol(port_str, NULL, 10);
    if (port <= 0 || port > 65535) {
        httpd_resp_set_type(req, "text/html");
        return httpd_resp_send(req, s_bad_port_html, HTTPD_RESP_USE_STRLEN);
    }
    creds.mqtt_port = (uint16_t)port;

    xSemaphoreTake(s_mutex, portMAX_DELAY);
    s_pending       = creds;
    s_pending_valid = true;
    s_status        = STATUS_TESTING;
    xSemaphoreGive(s_mutex);

    ESP_LOGI(TAG, "submission received: ssid=\"%s\" mqtt=%s:%u", creds.wifi_ssid, creds.mqtt_host,
             (unsigned)creds.mqtt_port);

    httpd_resp_set_type(req, "text/html");
    return httpd_resp_send(req, s_submitted_html, HTTPD_RESP_USE_STRLEN);
}

static esp_err_t handle_status(httpd_req_t *req)
{
    xSemaphoreTake(s_mutex, portMAX_DELAY);
    enum status_state st = s_status;
    xSemaphoreGive(s_mutex);

    const char *page;
    switch (st) {
    case STATUS_TESTING:
        page = "<!DOCTYPE html><html><head><title>Status</title>"
               "<meta http-equiv=\"refresh\" content=\"2;url=/status\"></head>"
               "<body style=\"font-family:sans-serif\"><p>Still testing&hellip;</p></body></html>";
        break;
    case STATUS_SUCCESS:
        page = "<!DOCTYPE html><html><head><title>Status</title></head>"
               "<body style=\"font-family:sans-serif\"><p>Connected. This setup network will now shut down.</p></body></html>";
        break;
    case STATUS_FAILED:
        page = "<!DOCTYPE html><html><head><title>Status</title></head>"
               "<body style=\"font-family:sans-serif\"><p>Could not connect with those credentials. "
               "<a href=\"/\">Try again</a></p></body></html>";
        break;
    default:
        page = "<!DOCTYPE html><html><head><title>Status</title></head>"
               "<body style=\"font-family:sans-serif\"><p>No submission yet. "
               "<a href=\"/\">Go to setup form</a></p></body></html>";
        break;
    }

    httpd_resp_set_type(req, "text/html");
    return httpd_resp_send(req, page, HTTPD_RESP_USE_STRLEN);
}

static esp_err_t handle_redirect_to_root(httpd_req_t *req)
{
    httpd_resp_set_status(req, "302 Found");
    httpd_resp_set_hdr(req, "Location", "/");
    httpd_resp_send(req, NULL, 0);
    return ESP_OK;
}

static const httpd_uri_t s_uri_root     = {.uri = "/", .method = HTTP_GET, .handler = handle_root};
static const httpd_uri_t s_uri_submit   = {.uri = "/submit", .method = HTTP_POST, .handler = handle_submit};
static const httpd_uri_t s_uri_status   = {.uri = "/status", .method = HTTP_GET, .handler = handle_status};
static const httpd_uri_t s_uri_catchall = {.uri = "/*", .method = HTTP_GET, .handler = handle_redirect_to_root};

/* OS captive-portal connectivity-check paths — matches the equivalent list
 * in the reference implementation's transport_task.cpp/WIFI_ONBOARDING_PLAN.md
 * so known-working OS behavior (auto-opening the portal) is preserved rather
 * than re-derived from scratch. */
static const char *const s_probe_paths[] = {
    "/generate_204",              /* Android */
    "/gen_204",                   /* Android (older) */
    "/hotspot-detect.html",       /* Apple */
    "/library/test/success.html", /* Apple (older) */
    "/ncsi.txt",                  /* Windows */
    "/connecttest.txt",           /* Windows */
};

int http_captive_start(void)
{
    if (s_mutex == NULL) {
        s_mutex = xSemaphoreCreateMutex();
        if (s_mutex == NULL) {
            return -ENOMEM;
        }
    }

    xSemaphoreTake(s_mutex, portMAX_DELAY);
    s_pending_valid = false;
    s_status        = STATUS_IDLE;
    xSemaphoreGive(s_mutex);

    httpd_config_t config  = HTTPD_DEFAULT_CONFIG();
    config.uri_match_fn    = httpd_uri_match_wildcard;
    config.max_uri_handlers = 4 + (int)(sizeof(s_probe_paths) / sizeof(s_probe_paths[0]));

    esp_err_t err = httpd_start(&s_server, &config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "httpd_start failed: 0x%x", err);
        s_server = NULL;
        return -EIO;
    }

    httpd_register_uri_handler(s_server, &s_uri_root);
    httpd_register_uri_handler(s_server, &s_uri_submit);
    httpd_register_uri_handler(s_server, &s_uri_status);

    for (size_t i = 0; i < sizeof(s_probe_paths) / sizeof(s_probe_paths[0]); i++) {
        httpd_uri_t probe = {
            .uri     = s_probe_paths[i],
            .method  = HTTP_GET,
            .handler = handle_redirect_to_root,
        };
        httpd_register_uri_handler(s_server, &probe);
    }

    /* Catch-all last: esp_http_server checks registered handlers in
     * registration order, so the exact-path handlers above must win before
     * this wildcard is ever consulted. */
    httpd_register_uri_handler(s_server, &s_uri_catchall);

    return 0;
}

void http_captive_stop(void)
{
    if (s_server != NULL) {
        httpd_stop(s_server);
        s_server = NULL;
    }
}

bool http_captive_take_submission(struct net_credentials *out)
{
    if (out == NULL || s_mutex == NULL) {
        return false;
    }

    bool got = false;
    xSemaphoreTake(s_mutex, portMAX_DELAY);
    if (s_pending_valid) {
        *out            = s_pending;
        s_pending_valid = false;
        got             = true;
    }
    xSemaphoreGive(s_mutex);
    return got;
}

void http_captive_report_result(bool success)
{
    if (s_mutex == NULL) {
        return;
    }

    xSemaphoreTake(s_mutex, portMAX_DELAY);
    s_status = success ? STATUS_SUCCESS : STATUS_FAILED;
    xSemaphoreGive(s_mutex);
}
