#ifndef MATRIX_DISPLAY_H_
#define MATRIX_DISPLAY_H_

/*
 * LED matrix display module - see matrix_display.cpp's header comment for
 * the full port rationale. Mirrors the old repo's
 * threads/matrix_display_thread.h contract: one entry point that brings
 * up the hardware, the Bridge RPC surface, and the render thread.
 */

/* Initializes the matrix, registers its Bridge providers
 * ("set_matrix_text", "set_matrix_scroll_speed"), and starts the render
 * thread (priority MATRIX_DISPLAY_THREAD_PRIORITY, see matrix_display.cpp).
 * Call once from setup(). */
void matrix_display_start(void);

#endif /* MATRIX_DISPLAY_H_ */
