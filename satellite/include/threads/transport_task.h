#ifndef TRANSPORT_TASK_H_
#define TRANSPORT_TASK_H_

/*
 * WiFi + MQTT connection owner - mirrors mcu/src/threads/
 * transport_thread.c/.h's role of owning transport_init() and the
 * receive-side dispatch, but the actual hal_transport.h contract this
 * task implements is MQTT-shaped, not UART-framed - see hal/
 * hal_transport.h's header comment for why the two aren't structurally
 * parallel despite the shared naming pattern.
 */

int transport_task_start(void);

#endif /* TRANSPORT_TASK_H_ */
