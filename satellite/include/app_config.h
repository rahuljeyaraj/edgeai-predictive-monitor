#ifndef APP_CONFIG_H_
#define APP_CONFIG_H_

/*
 * Mirrors mcu/src/app_config.h's role - the one place sensor/pipeline
 * tuning constants live - plus the WiFi/MQTT node identity/connection
 * settings mcu/ doesn't need (its transport is a fixed point-to-point
 * UART link, docs/Appendix_B_Wire_Protocol_Specification.md S2; this
 * node's transport is WiFi/MQTT, S3, which needs credentials + a broker
 * address).
 */

/* ---- WiFi / MQTT ----
 *
 * Per docs/Appendix_A_Network_Selection_Rationale.md and Appendix B S3:
 * the UNO Q hosts its own 2.4GHz AP (SSID "EPM-BaseStation") and runs the
 * Mosquitto broker at 10.42.0.1. WIFI_PASSWORD has no documented value
 * (deployment-time secret, not committed anywhere in this repo) - replace
 * the placeholder below before flashing. Overridable via build_flags
 * (-D WIFI_SSID=... etc.) instead of editing this file directly, if
 * preferred, since these are per-deployment values, not firmware
 * behavior. */
#ifndef WIFI_SSID
#define WIFI_SSID "EPM-BaseStation"
#endif
#ifndef WIFI_PASSWORD
#define WIFI_PASSWORD "CHANGE_ME"
#endif
#ifndef MQTT_BROKER_HOST
#define MQTT_BROKER_HOST "10.42.0.1"
#endif
#ifndef MQTT_BROKER_PORT
#define MQTT_BROKER_PORT 1883
#endif

/* Per-sensor enable/disable - mirrors mcu/'s MIC_SENSOR_ENABLED/
 * ACCEL_SENSOR_ENABLED exactly (app_config.h there). 0 disables that
 * sensor's task entirely (the task still exists but its own _start()
 * returns immediately without touching the HAL) - fuser_task.cpp reads
 * the same constants to decide which channels to publish. */
#define MIC_SENSOR_ENABLED   1
#define ACCEL_SENSOR_ENABLED 1

/* FFT bin counts (unique bins, excl. 0Hz) - both fixed at 512 to match
 * mpu/registry/registry.py's INPUT_DIM_BY_CHANNEL (512 for both MIC and
 * ACCEL, S4.2 "drives 512 vs 1024 model dim"). This fixes the FFT window
 * size (FFT_LEN = bin_count * 2, both channels), which in turn fixes the
 * fft_size field in the outgoing SPECTRUM message's
 * spectrum_fused_payload_header (frame_codec/spectrum_codec.h) - the
 * receiver (mpu/common/wire_protocol.py's decode_spectrum_fused_payload())
 * reads fft_size straight off the wire rather than hardcoding it, but
 * mpu/registry/registry.py's fixed per-channel model input dimension
 * still requires bin_count to equal 512 here. Every bin is sent, dense -
 * see spectrum_build_fused_payload(), there's no peak-selection/
 * truncation step anymore. */
#define MIC_FFT_BIN_COUNT   512
#define ACCEL_FFT_BIN_COUNT 512

/* Fuser epoch: how often the fuser task drains both sensors' latest FFT
 * result and publishes one fused SPECTRUM MQTT message - the WiFi/MQTT
 * counterpart to mcu/'s FUSER_EPOCH_MS (fuser_thread.c). Matches
 * mpu/tools/satellite_node_sim.py's DEFAULT_PUBLISH_INTERVAL_S (0.2s)
 * exactly, so a real node's publish cadence is indistinguishable from the
 * simulator's on the dashboard. At MIC_FFT_BIN_COUNT=ACCEL_FFT_BIN_COUNT=
 * 512 this is a ~4.1KB binary SPECTRUM message every 200ms (~20.5KB/s,
 * ~164kbps) - well within WiFi capacity, and an order of magnitude
 * smaller than the JSON-peaks envelope an earlier revision of this
 * firmware sent at the same bin count (~32KB/epoch). */
#define FUSER_EPOCH_MS 200

#endif /* APP_CONFIG_H_ */
