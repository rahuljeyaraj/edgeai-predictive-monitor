# Task: Redo Milestone 1 & Milestone 2 (edgeai-unoq)

## Why this redo is needed

Milestones 1 (USART1 debug logging) and 2 (LPUART1 MCU↔MPU communication)
were already implemented once, but **without** the repository file structure
defined in `MCU_Software_Architecture.md` §3. Specifically, `uart_link.c/h`
and `wire_protocol.c/h` were left in their original flat location instead of
being relocated into `drivers/` and `frame_codec/`, and `hal/hal_transport.h`
was never created. This redo exists to fix that gap before any further
milestone builds on top of it.

**Read `MCU_Software_Architecture.md` in full before starting — §3
(Repository file structure) and §9 (Implementation milestones, rows 1–2)
are both directly relevant and authoritative for this task.**

## Scope — what this task covers

Only Milestones 1 and 2. Do not start Milestone 3 or any later milestone in
this task, even if it seems like a small next step. Stop once Milestone 2's
verification (below) passes.

## Step 1 — Establish the folder skeleton

Create these folders at the `edgeai-unoq` repo root if they don't already
exist:

```
hal/
drivers/
frame_codec/
threads/
```

Per §3, `app_config.h` and `main.c` stay at the repo root.

## Step 2 — Relocate existing files

- Move the current `uart_link.c` → `drivers/uart_link.c`
- Move the current `uart_link.h` → `drivers/uart_link.h`
  - This header stays **private** to `uart_link.c` (DMA buffer sizes,
    static parser state, etc.) — it is not the public contract other code
    calls against. Nothing outside `drivers/uart_link.c` should `#include`
    it after this redo.
- Move the current `wire_protocol.c` → `frame_codec/wire_protocol.c`
- Move the current `wire_protocol.h` → `frame_codec/wire_protocol.h`

Update all `#include` paths in these files (and anywhere that includes
them) to reflect the new locations. Don't change any logic inside
`wire_protocol.c/h` — it's correct as-is and explicitly unchanged by the
architecture doc.

## Step 3 — Create the transport HAL contract

Create `hal/hal_transport.h` with the generic transport interface:

```c
int transport_init(void);
int transport_send(uint8_t type, const uint8_t *payload, uint16_t payload_len);
int transport_recv(struct wp_frame *frame, k_timeout_t timeout);
```

Modify `drivers/uart_link.c` so its existing `uart_link_init()`,
`uart_link_send()`, and `uart_link_recv()` logic is wrapped by (or renamed
to back) `transport_init()`, `transport_send()`, and `transport_recv()` —
i.e. `drivers/uart_link.c` becomes the implementation of
`hal/hal_transport.h`. Keep all existing DMA/async-callback/ping-pong logic
exactly as-is; this step is a wrapping/renaming exercise, not a rewrite of
the UART behavior.

## Step 4 — Create the transport thread

Create `threads/transport_thread.c` and `threads/transport_thread.h`. For
Milestone 2's scope, this thread only needs to:

- Call `transport_init()` once at startup.
- Be ready to call `transport_recv()` in a loop (full frame dispatch logic
  comes in Milestone 3+ — for this redo, receiving and logging a frame's
  `type` and `len` is sufficient; no need to implement the RGB/matrix
  command dispatch yet, since those threads don't exist until Milestones 3
  and 4).

## Step 5 — Re-verify Milestone 1 (USART1 logging)

Confirm logging still works correctly after the relocation — nothing in
Milestone 1's logging path should have changed behavior, only Milestone 2's
files moved. Verification: logs visible on host PC terminal (PuTTY/minicom)
via the USB-UART dongle, same as before.

## Step 6 — Re-verify Milestone 2 (LPUART1 MCU↔MPU communication)

Send a known test frame between MCU and MPU through the new
`hal_transport.h`-wrapped path (not by calling `uart_link_send()`/
`uart_link_recv()` directly from test code — go through `transport_send()`/
`transport_recv()` to confirm the HAL wrapping actually works end-to-end).

Verification:
- CRC validates correctly on a round-tripped frame.
- Payload content matches exactly what was sent.
- No regression in throughput — should still hit the ~114 KB/s DMA-backed
  ceiling documented in §2 of the architecture doc.

## Do not do in this task

- Do not create `hal/hal_accel.h`, `hal/hal_audio.h`,
  `hal/hal_display_rgb.h`, `hal/hal_display_matrix.h`, or any driver files
  for sensors/displays — those belong to Milestones 3–8.
- Do not create `frame_codec/frame_types.h` yet — the first payload struct
  (`display_rgb_payload`) isn't needed until Milestone 3.
- Do not modify CRC logic, frame envelope layout, or any wire-format detail
  in `wire_protocol.c/h` — only its file location changes.

## Done when

- Folder structure matches §3 of `MCU_Software_Architecture.md` exactly for
  the files this task touches.
- Milestone 1 and Milestone 2 verification steps above both pass.
- No leftover flat-location copies of `uart_link.*` or `wire_protocol.*`
  remain anywhere in the repo (old paths fully removed, not just
  duplicated).
