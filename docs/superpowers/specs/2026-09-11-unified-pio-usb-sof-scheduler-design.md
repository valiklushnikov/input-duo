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

Pico-PIO-USB becomes the sole owner of the SOF deadline and one RAM-resident
frame-service path. Ordinary repeating-timer callbacks are authoritative: each
one records its actual timestamp and sends a frame, accepting the timer's normal
roughly 999--1001 us jitter instead of applying a second deadline that can
suppress an entire fixed-phase callback. Flash-loop offers read the RP2040
hardware timer directly and enter the same service path only when the shared
deadline is due. An accepted late flash offer moves the deadline to one
millisecond after its actual timestamp and never replays missed slots as a
catch-up burst.

The application flash loop no longer owns or resets a cadence. It calls the
RAM-resident keepalive service continuously while Core 0 has XIP unavailable;
the library's shared deadline makes early flash offers no-ops. Pause cancels
the SDK repeating timer before the flash window and resume recreates it with
its first callback one millisecond later. The authoritative ordinary callback
therefore cannot race immediately behind the last accepted flash frame.

The same shared send path records the minimum and maximum actual intervals in
microseconds between frame transmissions. Two append-only `uint32_t` fields,
`sof_interval_min_us` and `sof_interval_max_us`, are added to host observation,
the configurator parser, the Diagnostics page/export, and protocol-size tests.
Zero means fewer than two frames have been transmitted. These are diagnostic
measurements, not enforcement thresholds.

The pure C scheduler state machine lives in the tracked
`firmware/common/pio_usb_sof_scheduler.h`. Pico-PIO-USB's patched production C
source and the native behavioral test both include that one file through
`duo_common`'s public include path. Native-only configure/build therefore
neither requires nor trusts the ignored `.deps` checkout, and the patch does
not carry a second copy that can drift.

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
schedule a flash frame immediately after an ordinary frame. A native behavioral
integration case also observes ordinary callbacks at 1001, 2000, and 3000 us,
with an intervening early flash offer, and requires all three ordinary frames
with measured 999--1000 us intervals. ELF contracts require both ordinary and
flash entry points to reach the same SRAM frame-service path; the only retained
source assertion requires the application flash loop to contain no SOF cadence.
Parser, serializer-size, export-label, native firmware, PIO main/reference, and
CH375 builds must remain green.

Hardware acceptance alternates configuration slots through repeated writes.
After every write, both HID roles must remain ready, `stall_signals` and
`arm_escalations` must remain unchanged, SOF/Core 1 counters must advance, and
the measured interval maximum must stay below the USB suspend threshold of
3000 us. The minimum is inspected for compressed frames; it must not reveal a
second scheduler emitting an adjacent frame.
