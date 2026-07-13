#ifndef RGB_DISPLAY_H_
#define RGB_DISPLAY_H_

/*
 * External WS2812B RGB LED ring - see rgb_display.cpp's header comment for
 * the full port rationale. Mirrors the old repo's threads/rgb_display_thread.h
 * contract: one entry point that brings up the hardware, the Bridge RPC
 * surface, and the render thread.
 */

/* Initializes the ring, registers its Bridge provider ("set_rgb"), and
 * starts the render thread (priority RGB_DISPLAY_THREAD_PRIORITY, see
 * rgb_display.cpp). Call once from setup(). */
void rgb_display_start(void);

#endif /* RGB_DISPLAY_H_ */
