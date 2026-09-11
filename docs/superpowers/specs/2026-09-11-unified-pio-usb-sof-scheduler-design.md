# Unified Pico-PIO-USB SOF Scheduler Design

## Problem

Configuration writes alternate between ordinary Pico-PIO-USB service and
RAM-only flash windows. The first repair kept SOF alive during long erases; the
second retained a 1 ms flash cadence across the short 256-byte page programs.
That cadence is still owned by the application, while ordinary frames are
owned by Pico-PIO-USB's repeating timer. Neither scheduler observes frames sent
by the other.

This permits two frames and two endpoint polls to occur much less than 1 ms
apart when a flash window starts just after an ordinary frame. The failure is
phase-dependent. The hardware report taken after recovery records an endpoint
failure high-water mark of two; Pico-PIO-USB retires a transfer on the third
consecutive failure, matching the intermittent all-HID fault already observed
before the power cycle.

## Selected Design

Pico-PIO-USB becomes the sole owner of the SOF deadline. Both its repeating
timer callback and `pio_usb_host_flash_keepalive()` enter one RAM-resident
`service-frame-if-due` path. That path reads the RP2040 hardware timer directly,
emits at most one frame when the shared deadline is due, services the already
queued endpoints, advances the frame number, and moves the deadline forward by
1 ms. A late call skips missed slots instead of sending a catch-up burst.

The application flash loop no longer owns or resets a cadence. It calls the
RAM-resident keepalive service continuously while Core 0 has XIP unavailable;
the library's shared deadline makes early calls no-ops. Pause and resume still
cancel and recreate the SDK repeating timer so an overdue SDK alarm cannot
burst after a flash window.

The same shared send path records the minimum and maximum actual intervals in
microseconds between frame transmissions. Two append-only `uint32_t` fields,
`sof_interval_min_us` and `sof_interval_max_us`, are added to host observation,
the configurator parser, the Diagnostics page/export, and protocol-size tests.
Zero means fewer than two frames have been transmitted. These are diagnostic
measurements, not enforcement thresholds.

## Alternatives Rejected

Keeping two schedulers and synchronising them through a new application call
would leave ownership split across the dependency boundary and allow a future
ordinary-frame call site to bypass the synchronisation again.

Resetting or restarting the USB host after every configuration write would
make a routine write visibly disconnect every input device. The pinned
Pico-PIO-USB `host_stop` path is also unusable because its cancellation flag is
never cleared.

Suppressing HID faults or increasing retry counts would mask the malformed
frame timing rather than remove it, and could leave genuinely failed devices
silently retrying forever.

## Safety and Compatibility

Every function and datum reachable while XIP is unavailable must remain in
SRAM or boot ROM. The existing recursive ELF contract remains authoritative
and is extended for the unified scheduler's state and telemetry. No TinyUSB
callback runs during flash; transfer completion continues to be dispatched by
the first ordinary task pass after XIP returns.

Host observation remains length-prefixed and append-only. Older configurators
ignore the two trailing fields; the updated parser accepts both old and new
block lengths and reports `None` for measurements an older firmware did not
publish.

## Verification

Tests must first demonstrate that the current two-scheduler implementation can
schedule a flash frame immediately after an ordinary frame. Source and ELF
contracts then require both ordinary and flash entry points to use the same
deadline and require the application flash loop to contain no SOF cadence.
Parser, serializer-size, export-label, native firmware, PIO main/reference, and
CH375 builds must remain green.

Hardware acceptance alternates configuration slots through repeated writes.
After every write, both HID roles must remain ready, `stall_signals` and
`arm_escalations` must remain unchanged, SOF/Core 1 counters must advance, and
the measured interval maximum must stay below the USB suspend threshold of
3000 us. The minimum is inspected for compressed frames; it must not reveal a
second scheduler emitting an adjacent frame.
