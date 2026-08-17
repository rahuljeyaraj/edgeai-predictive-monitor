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
 * Runtime-provisioned as of docs/WIFI_ONBOARDING_PLAN.md S2: a node with
 * no saved NVS credentials starts its own AP (PROVISIONING_AP_SSID_PREFIX
 * + this node's MAC-derived id) and serves a one-page captive-portal form
 * asking for SSID/password/MQTT broker address - see
 * hal/hal_provisioning.h and hal/hal_credentials.h. WIFI_SSID/
 * WIFI_PASSWORD below are NOT the normal provisioning path; they're a
 * dev-bench shortcut only: if both are overridden via build_flags away
 * from their placeholder defaults, threads/transport_task.cpp auto-seeds
 * NVS with them on first boot and skips the portal entirely - saves the
 * AP+form dance on this project's 2 real bench boards during frequent
 * reflash cycles. A real deployment build passes neither flag, so this is
 * a no-op in the field. MQTT_BROKER_HOST is the portal form's broker-field
 * prefill default (mDNS name, not a raw IP - overridable per-field at
 * provisioning time since mDNS can be VLAN-blocked on factory WiFi,
 * S4). */
#ifndef WIFI_SSID
#define WIFI_SSID "CHANGE_ME"
#endif
#ifndef WIFI_PASSWORD
#define WIFI_PASSWORD "CHANGE_ME"
#endif
#ifndef MQTT_BROKER_HOST
#define MQTT_BROKER_HOST "epm-base.local"
#endif
#ifndef MQTT_BROKER_PORT
#define MQTT_BROKER_PORT 1883
#endif

/* AP SSID this node broadcasts while unprovisioned/re-provisioning:
 * PROVISIONING_AP_SSID_PREFIX + the node's MAC-derived id (e.g.
 * "EPM-SAT-a1b2c3"), per docs/WIFI_ONBOARDING_PLAN.md S2. */
#define PROVISIONING_AP_SSID_PREFIX "EPM-SAT-"

/* Per-sensor enable/disable - mirrors mcu/'s MIC_SENSOR_ENABLED/
 * ACCEL_SENSOR_ENABLED exactly (app_config.h there). 0 disables that
 * sensor's task entirely (the task still exists but its own _start()
 * returns immediately without touching the HAL) - fuser_task.cpp reads
 * the same constants to decide which channels to publish. */
#define MIC_SENSOR_ENABLED   0
#define ACCEL_SENSOR_ENABLED 0

/* FFT bin counts (unique bins, excl. 0Hz) - the native FFT resolution used
 * for both the on-device spectrum and the scalar tile's time-domain window
 * (FFT_LEN = bin_count * 2, both channels). This does NOT have to match
 * MODEL_SPECTRUM_BINS below or any fixed model dimension -
 * base-station/python/pipeline/manager.py's _infer_sensor_config_and_dim()
 * commits a node's input_dim from whatever bin count its own first frame
 * reports, so a satellite node's wire bin count is free to differ from the
 * base station's own. Kept dense at 512 here (rather than lowered to
 * MODEL_SPECTRUM_BINS directly) so the time-domain window feeding
 * compute_scalars() stays reasonably long. */
#define MIC_FFT_BIN_COUNT   512
#define ACCEL_FFT_BIN_COUNT 512

/* Per-channel bin count actually put on the wire (fuser_task.cpp
 * average-pools each MIC_FFT_BIN_COUNT/ACCEL_FFT_BIN_COUNT spectrum down to
 * this many buckets before encoding) - mirrors base-station/sketch/
 * app_config.h's FUSER_MODEL_SPECTRUM_BINS and
 * python/tools/satellite_node_sim.py's DEFAULT_BIN_COUNT, keeping a real
 * node's per-frame payload size (and therefore MQTT bandwidth) the same
 * order of magnitude as the rest of the fleet. */
#define MODEL_SPECTRUM_BINS 128

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
