/*
 * Sensor fusion / transport (port of the old repo's fuser_thread.c).
 * Reads the latest full-resolution mic + accel spectra, packs them into one
 * self-describing frame, and PUSHES it to the MPU over Bridge as a sequence of
 * binary chunks (Bridge.notify("spec_chunk", <msgpack bin>)) - not the old
 * poll/response model, and not the 32-bucket downsampled Bridge providers the
 * samplers expose for their standalone tests. See fuser.cpp's header comment
 * for the full rationale (why push, why chunked, why float32/full-res).
 *
 * NOTE: currently at the BRING-UP PROBE stage - fuser_start() only exercises
 * the binary-notify transport primitive (fixed synthetic payload) so the
 * MCU->Python msgpack-bin path can be proven on hardware before the real frame
 * assembly + sampler-full-bin plumbing is wired in. See fuser.cpp.
 */
#pragma once

void fuser_start(void);
