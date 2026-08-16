#ifndef FUSER_TASK_H_
#define FUSER_TASK_H_

/*
 * Periodic sample-and-hold + publish task - drains both sensor queues on a
 * fixed epoch, holds the last value for whichever didn't have anything new,
 * and publishes one generic section-list telemetry frame
 * (frame_codec/spectrum_codec.h, docs/SENSOR_TELEMETRY_FRAME_PLAN.md S3/S6)
 * carrying a SPECTRUM section per enabled channel (mic, accel_x, accel_y,
 * accel_z) plus one SCALAR_SET section of that channel set's scalar tiles -
 * the same per-axis+scalar wire shape the base station's own SPI link now
 * sends, published raw as the MQTT data-topic body with no extra envelope.
 */

int fuser_task_start(void);

#endif /* FUSER_TASK_H_ */
