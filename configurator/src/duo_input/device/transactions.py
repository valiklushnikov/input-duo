"""Framing, sequencing and payload parsing for the host side of the CDC link.

This module is deliberately free of Qt and of any I/O: it is the pure part of
the device layer, which keeps :mod:`duo_input.device.service` small enough to
reason about.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import IntEnum, StrEnum

from duo_input.domain.models import Trigger, TriggerSource

# ErrorCode is defined exactly once, in protocol/schema.json, and reaches every
# side of the link through generated code. Importing it here from the emulator -
# which is test-support, not production - is how the host and the firmware would
# come to disagree about an error code without either of them changing.
from duo_input.generated.protocol import (
    CdcMessageType,
    CAPTURE_EVENT_PAYLOAD_BYTES,
    ErrorCode,
    InputBackend,
    TriggerKind,
)
from duo_input.protocol.frame import CdcFrame, FrameError, decode_cdc_frame

_FRAME_DELIMITER = 0

# A legal CDC frame is at most 10 header + 1024 payload + 4 CRC bytes, plus COBS
# overhead and the delimiter (~1044 bytes). Anything retained beyond this cap can
# never become a frame, so the buffer is discarded instead of growing without end.
MAX_PENDING_FRAME_BYTES = 2048

_DEVICE_INFO = struct.Struct("<BBBIIB32s")
_STATUS = struct.Struct("<BBBBI")
_CONFIG_INFO = struct.Struct("<BII32s")
_DIAGNOSTICS = struct.Struct("<BIIIII")
_LINK_STATE = struct.Struct("<BBIII")
_ENDPOINT_REPORT = struct.Struct("<BH")
_DROPPED_COMMANDS = struct.Struct("<I")
_RUNTIME_FAULT = struct.Struct("<B")
_LATENCY_HEAD = struct.Struct("<II")
_U32 = struct.Struct("<I")
_PERIPHERAL = struct.Struct("<BBBHHBH32s")
_CHUNK_ACK = struct.Struct("<BI")
_CAPTURE_EVENT_LEGACY = struct.Struct("<BBB")
_CAPTURE_EVENT_FULL = struct.Struct("<BBBHHB")
_HID_DESCRIPTOR_CAPTURE = struct.Struct("<BBBHHBHH")
_HID_REPORT_SETS = struct.Struct("<BBB")
_HID_REPORT_SOURCE = struct.Struct("<BHHBBBB")
_HID_REPORT_ENTRY = struct.Struct("<BBB")
assert _CAPTURE_EVENT_FULL.size == CAPTURE_EVENT_PAYLOAD_BYTES
assert _HID_DESCRIPTOR_CAPTURE.size == 12


class PayloadError(ValueError):
    """A reply carried a payload that does not match the frozen v1 layout."""


class FrameOverflowError(ValueError):
    """The stream grew past any legal frame length without a delimiter."""


class FailureReason(StrEnum):
    """Stable, never-localised identifiers for a failed device operation."""

    NOT_CONNECTED = "not_connected"
    BUSY = "busy"
    INVALID_PACKAGE = "invalid_package"
    LINK_LOST = "link_lost"
    TIMEOUT = "timeout"
    BAD_FRAME = "bad_frame"
    BAD_PAYLOAD = "bad_payload"
    SEQUENCE_MISMATCH = "sequence_mismatch"
    UNEXPECTED_REPLY = "unexpected_reply"
    PROTOCOL_MISMATCH = "protocol_mismatch"
    DEVICE_ERROR = "device_error"
    ACK_MISMATCH = "ack_mismatch"
    READBACK_MISMATCH = "readback_mismatch"
    ABORTED = "aborted"


@dataclass(frozen=True)
class OperationFailure:
    """Why a device operation ended without doing what was asked."""

    operation: str
    reason: FailureReason
    error_code: ErrorCode | None = None
    detail: str = ""


@dataclass(frozen=True)
class OperationResult:
    """The value produced by a device operation that ran to completion."""

    operation: str
    value: object = None


@dataclass(frozen=True)
class DeviceInfo:
    protocol_major: int
    protocol_minor: int
    capabilities: int
    active_generation: int
    active_profile: int
    active_hash: bytes


@dataclass(frozen=True)
class DeviceStatus:
    active_profile: int
    capture_active: bool
    staging_active: bool
    release_all_count: int


@dataclass(frozen=True)
class HidDescriptorCapture:
    present: bool
    truncated: bool
    vendor_id: int
    product_id: int
    interface_number: int
    original_size: int
    descriptor: bytes

    @property
    def captured_size(self) -> int:
        return len(self.descriptor)


class HidReportRole(IntEnum):
    KEYBOARD = 1
    CONSUMER = 2
    MOUSE = 3


class HidReportRejectionReason(IntEnum):
    TRUNCATED = 1
    NO_MOUSE_REPORT = 2
    UNSUPPORTED_LAYOUT = 3
    NO_KEYBOARD_REPORT = 4
    AMBIGUOUS_KEYBOARD_REPORT = 5
    MALFORMED_GLOBAL_STATE = 6
    AMBIGUOUS_REPORT_SET = 7


@dataclass(frozen=True)
class HidReportEntry:
    role: HidReportRole
    report_id: int
    minimum_body_bytes: int


@dataclass(frozen=True)
class RejectedHidReportEntry:
    role: HidReportRole
    report_id: int
    reason: HidReportRejectionReason


@dataclass(frozen=True)
class HidReportSource:
    device_address: int
    vendor_id: int
    product_id: int
    interface_number: int
    accepted: tuple[HidReportEntry, ...]
    rejected: tuple[RejectedHidReportEntry, ...]
    rejected_overflow: int


@dataclass(frozen=True)
class HidReportSets:
    sources: tuple[HidReportSource, ...]


@dataclass(frozen=True)
class ActiveConfigInfo:
    generation: int
    size: int
    digest: bytes


@dataclass(frozen=True)
class LatencyHistogram:
    """One stream of input, timed by the device and counted into buckets.

    The device cannot keep every sample and cannot send a percentile it did not
    compute, so it sends counts. A count answers "p95 <= 20 ms" exactly, as long
    as 20 ms is one of the edges; it does not answer "what is the p95" with a
    number, and this class refuses to invent one. Everything here brackets the
    answer between two edges the device actually reported.

    ``edges_us`` are the inclusive upper bounds of every bucket but the last,
    and they arrive from the device rather than being held here, so a host and a
    firmware cannot drift into disagreeing about what a bucket means.
    """

    edges_us: tuple[int, ...]
    buckets: tuple[int, ...]
    count: int
    max_us: int

    def at_or_below(self, bound_us: int) -> int:
        """How many samples were at or below ``bound_us``.

        Only a bucket edge has an exact answer. Anything else would need a
        distribution the device never reported, so it is refused.
        """
        total = 0
        for index, edge in enumerate(self.edges_us):
            total += self.buckets[index]
            if edge == bound_us:
                return total
        raise ValueError(
            f"{bound_us} us is not one of the bucket edges the device reported "
            f"({', '.join(str(edge) for edge in self.edges_us)}); "
            "there is no exact answer for a bound inside a bucket"
        )

    def p95_upper_bound_us(self) -> int | None:
        """The tightest edge the p95 is known to be at or below.

        ``None`` means the p95 lies in the overflow bucket - above the last edge
        - and this data cannot say where. That is a real answer and it is not a
        pass; ``max_us`` is the only other thing known about it.

        Nearest rank, matching the runner: p95 <= E exactly when at least
        ceil(0.95 * count) samples were at or below E.
        """
        if self.count == 0:
            raise ValueError("no samples were measured, so there is no percentile")
        needed = -(-self.count * 95 // 100)
        total = 0
        for index, edge in enumerate(self.edges_us):
            total += self.buckets[index]
            if total >= needed:
                return edge
        return None

    def within(self, bound_us: int) -> bool:
        """Did the p95 meet ``bound_us``? Exact, for a bound that is an edge."""
        return self.count > 0 and self.at_or_below(bound_us) >= -(-self.count * 95 // 100)


#: What enumeration made of a port, by the number the firmware sends.
_PERIPHERAL_KINDS = {0: "unknown", 1: "keyboard", 2: "mouse"}


@dataclass(frozen=True)
class PeripheralPort:
    """One of U1's own USB ports and whatever is on it.

    A peripheral here is on U1's bus and not on either computer's, so no host
    can enumerate it and no operator transcription of it is a measurement. Every
    field is what the device reported about the device it found.
    """

    attached: bool
    ready: bool
    #: "none", "unknown", "keyboard" or "mouse". "none" when nothing is
    #: attached, which is different from a device that enumerated as nothing
    #: recognisable.
    kind: str
    vendor_id: int
    product_id: int
    #: Buttons this mouse declared in its own report descriptor. ``None`` when
    #: it declared none - a boot-protocol mouse is read under an assumed layout,
    #: and the assumption is not something the device said.
    buttons: int | None
    report_descriptor_bytes: int
    #: Hex SHA-256 of the report descriptor, or ``None`` when none was read.
    #: Not the hash of an empty buffer, which every such device would share.
    descriptor_hash: str | None


#: Which input backend the firmware named, by the number it sent. The names are
#: the protocol's own (protocol/schema.json input_backends), so a device and a
#: report cannot disagree about what a number means.
_INPUT_BACKENDS = {
    int(InputBackend.CH375): InputBackend.CH375.name,
    int(InputBackend.PIO_USB): InputBackend.PIO_USB.name,
    int(InputBackend.PIO_USB_REFERENCE): InputBackend.PIO_USB_REFERENCE.name,
}

#: The backend counters, in the fixed order the wire carries them. Append only:
#: firmware may send fewer than this (it stops where its own list stops) or
#: more (a later firmware appended one), and neither is an error.
_BACKEND_COUNTER_FIELDS = (
    "ignored_interfaces",
    "ignored_role_already_claimed",
    "event_overflows",
    "detach_overflows",
    "stale_events_discarded",
    "arm_failures",
    "arm_escalations",
    "stall_signals",
    "duplicate_mounts",
    "device_overflows",
    "interface_overflows",
    "callback_overflows",
)


@dataclass(frozen=True)
class InputBackendReport:
    """Which host stack read U1's own USB ports, and what it counted.

    U1's two input channels can be read by the CH375 pair or by the single PIO
    USB host. The two fail in entirely different ways, so a diagnostic that
    does not name the backend cannot be acted on months later.
    """

    #: ``"CH375"``, ``"PIO_USB"``, ``"unknown"`` (the firmware named a backend
    #: this configurator does not know, or named none at all), or
    #: ``"unreadable"`` (a block was there and did not parse). The last two are
    #: deliberately different from ``DeviceDiagnostics.backend`` being ``None``,
    #: which means the firmware sent no block whatsoever.
    name: str
    #: Why the block did not parse. Set only when ``name`` is ``"unreadable"``.
    unreadable_reason: str | None = None

    # Every counter below is a reason input did not arrive, and none of them
    # has any other outward sign. ``None`` means this firmware did not send
    # that counter - never that it counted zero.
    #: Interfaces that ended up with no logical role, for any reason.
    ignored_interfaces: int | None = None
    #: How many of those only because the role was already held. V1 accepts one
    #: logical keyboard and one logical mouse, so a second keyboard lands here -
    #: a spare device on the bench. The remainder is "nothing could classify
    #: it", which is a broken one.
    ignored_role_already_claimed: int | None = None
    event_overflows: int | None = None
    detach_overflows: int | None = None
    stale_events_discarded: int | None = None
    arm_failures: int | None = None
    arm_escalations: int | None = None
    stall_signals: int | None = None
    duplicate_mounts: int | None = None
    device_overflows: int | None = None
    interface_overflows: int | None = None
    callback_overflows: int | None = None

    def counters(self) -> dict[str, int]:
        """Every counter this firmware actually sent, in wire order.

        The ones it did not send are absent rather than zero: a report that
        prints zero for a figure nothing measured is a report that invents a
        measurement.
        """
        return {
            name: value
            for name in _BACKEND_COUNTER_FIELDS
            if (value := getattr(self, name)) is not None
        }


#: Bit positions in the host block's init_flags byte, in the firmware's order
#: (firmware/u1_main/pio_usb/backend.hpp kHostInit*).
_HOST_ALREADY_ACTIVE = 1 << 0
_HOST_CONFIGURED = 1 << 1
_HOST_INITIALIZED = 1 << 2
_HOST_INITED = 1 << 3

#: Bit positions in the host block's root_port_state byte, in the firmware's
#: order (firmware/u1_main/pio_usb/backend.hpp kRootPort*), which is itself
#: Pico-PIO-USB's own root_port_t field order.
_ROOT_INITIALIZED = 1 << 0
_ROOT_CONNECTED = 1 << 1
_ROOT_SUSPENDED = 1 << 2
_ROOT_FULLSPEED = 1 << 3

#: The host block's fields, little-endian, behind its one-byte length:
#: init flags, the two clock readings, the SOF count, the packed root-port
#: state, the attach count and Core 1's pass count.
#:
#: Only the BASE is a struct. Everything appended behind it is read one field
#: at a time by ``_parse_host_observation`` so that a firmware which stops
#: partway through - the whole point of the length byte - is still readable.
#: A whole-block struct used to sit here beside this one and was never used by
#: anything; a round-5 mutation sweep shortened it by a field and the entire
#: configurator suite still passed, which is what a constant that guards
#: nothing looks like. It was deleted rather than given a test.
_HOST_OBSERVATION_BASE = struct.Struct("<BIIIBHI")

#: Bit positions inside the host block's ``ep_slot_map`` byte, in the
#: firmware's order (firmware/u1_main/pio_usb/backend.hpp kEpSlot*).
EP_SLOT_ENDPOINT_MASK = 0x07
EP_SLOT_DIRECTION_IN = 1 << 3
EP_SLOT_OPEN = 1 << 4
EP_SLOT_ADDRESS_SHIFT = 5
EP_SLOT_ADDRESS_MASK = 0x07
#: How many pool slots ``ep_slot_map`` describes, one byte each.
EP_SLOT_COUNT = 4

#: Bits in each byte of ``ep_transfer_flags``. Direction and PID describe the
#: active transfer only when EP_XFER_HAS_TRANSFER is set.
EP_XFER_OPEN = 1 << 0
EP_XFER_HAS_TRANSFER = 1 << 1
EP_XFER_HOST_OUT = 1 << 2
EP_XFER_DATA1 = 1 << 3
EP_XFER_NEED_PRE = 1 << 4
EP_XFER_SETUP_STAGED = 1 << 5
EP_XFER_STALLED = 1 << 6
EP_XFER_ABORTED = 1 << 7

#: Shifts inside the host block's packed ``host_event_counts`` word.
HOST_EVENT_REMOVE_SHIFT = 8
HOST_EVENT_XFER_SHIFT = 16

#: ``enum_progress_mask``: bit ``a - 1`` is configured, bit ``8 + a - 1`` says
#: the device descriptor was read. Addresses 1-4 are devices; 5 is the hub.
ENUM_DESCRIPTOR_SHIFT = 8
ENUM_HIGHEST_ADDRESS = 5


@dataclass(frozen=True)
class HostObservation:
    """What U1's own USB host stack and its raw root port are doing.

    Every counter in :class:`InputBackendReport` is a reason a peripheral that
    enumerated was not read. None of them says anything when nothing enumerates
    at all, and none distinguishes a host stack that never started from one
    that started and saw an empty bus - or from a U1 whose input core stopped
    before it could count anything. These fields are read from below all of
    that, and they are what make those three cases three different readings.
    """

    #: ``"reported"`` - the fields below are real readings.
    #: ``"none"`` - the firmware carries this block and has no host stack to
    #: observe. The CH375 image is the case: it has no TinyUSB host, no root
    #: port and no input-core backend loop, so every field below is ``None``
    #: rather than zero, because zero would be a measurement of hardware that
    #: is not there.
    #: ``"unreadable"`` - a block was there and did not parse.
    state: str
    #: Why the block did not parse. Set only when ``state`` is
    #: ``"unreadable"``.
    unreadable_reason: str | None = None

    #: The host stack was ALREADY up when the input core reached its own
    #: bring-up. The smoking gun for the defect this block was added for:
    #: after it, the configure and init calls below are no-ops that still
    #: report success, so ``host_configured`` and ``host_initialized`` being
    #: true means nothing while this is true.
    host_already_active: bool | None = None
    host_configured: bool | None = None
    host_initialized: bool | None = None
    #: The host stack reported itself initialised after those calls.
    host_inited: bool | None = None

    #: The system clock when U1's input core began. Current PIO firmware sets
    #: and settles 120 MHz in main() before any peripheral or Core 1 starts, so
    #: 120 MHz here confirms the reference ordering took effect. The Python
    #: attribute keeps the historical wire name for compatibility.
    clock_hz_before_core1_change: int | None = None
    #: The system clock now. This IS the divider clock whenever
    #: ``host_already_active`` is false, because the dividers are computed once
    #: and the input core brings the host up after main()'s clock change. When
    #: ``host_already_active`` is true the host came up elsewhere, on a clock
    #: this reply never saw, and no clock reading here says anything about the
    #: dividers.
    clock_hz_now: int | None = None

    #: The root port's free-running frame counter. Zero and static means the
    #: bus is not being driven at all; climbing while every counter above is
    #: still zero means it is being driven and nothing on it answers. Read it
    #: twice about a second apart to tell those apart.
    sof_frame_count: int | None = None

    root_port_initialized: bool | None = None
    #: Something is pulling D+ up right now.
    root_port_connected: bool | None = None
    root_port_suspended: bool | None = None
    root_port_fullspeed: bool | None = None
    #: Disconnected-to-connected transitions since the device booted: whether
    #: U1 ever saw anything attach at all, independently of whether it could
    #: then talk to it.
    #:
    #: A LOWER BOUND, not a total. Nothing below the host stack reports an
    #: attach edge, so the device polls the line once per input-core pass; an
    #: attach and detach that both fall between two passes leaves no trace.
    #: Zero is strong evidence that nothing ever attached, not proof of it.
    root_port_connects: int | None = None

    #: Passes of U1's input core loop. Unchanged across two reads twenty
    #: seconds apart means that core stopped - which is a different fault from
    #: a silent bus and has to be told apart from one.
    core1_passes: int | None = None
    mount_events: int | None = None
    umount_events: int | None = None
    hid_mount_events: int | None = None
    ep_slots_opened: int | None = None
    ep_max_failed_count: int | None = None
    max_pass_gap_us: int | None = None
    max_sof_gap: int | None = None
    root_port_resets: int | None = None
    #: Configured-hub transitions. TinyUSB excludes hub addresses from its
    #: application mount callback, so the device polls tuh_mounted() instead.
    #: This is a saturating lower bound.
    hub_mount_events: int | None = None

    #: Whose endpoint sits in each of the first four host endpoint-pool slots,
    #: one byte per slot with slot 0 in the low byte: device address in bits
    #: 7-5, an OPEN bit in bit 4, direction (1 = IN) in bit 3 and endpoint
    #: number in bits 2-0. A byte of zero means the slot is closed.
    #:
    #: The open bit matters: address 0's control endpoint has address,
    #: direction and endpoint number all zero, and without it that endpoint
    #: would read as an empty slot. A LIVE reading, not a high-water mark -
    #: ``ep_slots_opened`` above is the high-water count.
    ep_slot_map: int | None = None
    #: Every event the device's host stack has queued since boot: accepted
    #: attaches in bits 0-7, removals in bits 8-15, completed transfers in
    #: bits 16-31, each saturating. An event the host stack's own queue
    #: dropped never reaches this counter, by construction.
    host_event_counts: int | None = None
    #: How far each device address got, sticky. For address ``a`` in 1..5, bit
    #: ``a - 1`` says it reached the configured state and bit ``8 + a - 1``
    #: says its device descriptor was read. All-zero for an address nothing
    #: was plugged into is NORMAL.
    enum_progress_mask: int | None = None
    #: Input-core passes that blocked for more than 20 ms, saturating.
    #:
    #: NOT a fault reading. A healthy board produces several: enumerating the
    #: root port blocks for about half a second and each device behind a hub
    #: for another 450 ms. Zero would mean no enumeration was ever attempted.
    long_pass_count: int | None = None
    #: The total of those blocked passes in whole milliseconds, saturating.
    long_pass_total_ms: int | None = None
    #: The lowest stack pointer the input core was seen at while its host
    #: stack was queueing an event. ZERO MEANS NO SAMPLE - no host event has
    #: ever been queued - and is not a stack that reached address zero.
    core1_min_sp: int | None = None
    #: Live Pico-PIO-USB transfer state for pool slots 0-3, one byte per
    #: slot, slot 0 in the low byte. ``EP_XFER_OPEN`` distinguishes an idle
    #: address-0 endpoint from a closed slot; ``EP_XFER_HAS_TRANSFER`` then
    #: says whether SETUP/DATA/status is still outstanding.
    ep_transfer_flags: int | None = None
    #: Saturated transfer-completion total at the latest accepted attach.
    #: Subtract from ``host_event_counts`` bits 16-31 to count control stages
    #: completed after that attach. At saturation, the delta is no longer
    #: informative.
    xfer_completions_at_attach: int | None = None
    #: Synthetic address-0 duplicate-attach recovery requests submitted,
    #: saturating.
    #:
    #: ZERO IS NOT A HEALTH VERDICT: it also covers a watchdog that is
    #: inapplicable, safety-suppressed for a mounted child, or capped. A rising
    #: value proves the bounded recovery request repeated, not why the status
    #: stage was retried.
    enum_stall_recoveries: int | None = None
    #: Shortest and longest actual intervals between accepted SOF frame-service
    #: invocations. Both are zero until the second frame establishes the first
    #: interval, and ``None`` when reading an older host-block shape.
    sof_interval_min_us: int | None = None
    sof_interval_max_us: int | None = None
    #: That subtraction, done here rather than at a bench.
    #:
    #: DERIVED, not a wire field: the device sends the two numbers above and
    #: this is their difference, which is the reading anybody actually wants.
    #: ``None`` only when one of the two is absent, i.e. when the firmware
    #: predates them. It is still a raw difference - whether it can be trusted
    #: depends on the completion total not having saturated and on an attach
    #: having been accepted at all, and the exported report says so in the row
    #: rather than leaving a reader to notice.
    xfer_completions_since_attach: int | None = None


@dataclass(frozen=True)
class DeviceDiagnostics:
    bad_crc: int
    disconnect: int
    timeout: int
    bad_sequence: int
    aborted_staging: int

    # What the link to the second board is doing. ``None`` when the firmware
    # predates these fields.
    #
    # Everything above counts failures, and a link that never started produces
    # none of them - so a device whose second board is absent reports the same
    # five zeros as one that is working perfectly. These say what is happening
    # instead of what went wrong.
    endpoint_answering: bool | None = None
    endpoint_mounted: bool | None = None
    link_frames_sent: int | None = None
    link_crc_errors: int | None = None
    link_echoed_frames: int | None = None

    # What U2 saw when the link died. U2 releases every key 100 ms after U1
    # goes quiet, and nothing can watch that happen: the link that would carry
    # the news is the one that went silent. U2 remembers and reports it once
    # the link is back.
    endpoint_drops: int | None = None
    endpoint_release_ms: int | None = None

    # Commands U1's input core produced that its output core would not take.
    # Nonzero means a press, a release or a macro step never reached the
    # computer it was for, so what is held there no longer matches what the
    # operator did. There is no other outward sign of it.
    dropped_commands: int | None = None

    # What U1's output runtime is doing about its queue at this instant. Zero
    # is no fault; 1 is a queue that is refusing commands now. The counter
    # above is cumulative and never goes down, so it cannot distinguish a burst
    # that has passed from one that is still going on. ``None`` when the
    # firmware predates the field.
    runtime_fault: int | None = None

    # How long U1 itself took, counted into buckets by the device.
    #
    # This is the interval inside U1 - a peripheral report reaching its input
    # core, against the command that report produced being applied on its
    # output core - and nothing else. It is not the journey from a finger to a
    # far screen: neither end of that is visible to a board that sees only its
    # own clock, and anything presenting these figures as end-to-end latency is
    # presenting them as something they are not.
    #
    # ``None`` when the firmware predates the block, which includes the
    # emulator: it has no input pipeline and no peripheral to time.
    keyboard_latency: LatencyHistogram | None = None
    mouse_latency: LatencyHistogram | None = None

    # The two peripheral ports, in the order the firmware reports them: the
    # keyboard channel and then the mouse channel. ``None`` when the firmware
    # predates the block; a port with nothing on it is still a row, because an
    # empty port is a fact about the run rather than an absence to infer.
    peripherals: tuple[PeripheralPort, ...] | None = None

    # Which backend read those two ports, appended after them. ``None`` when
    # the firmware predates the block - which is a valid old payload and not a
    # parse error; see ``_parse_backend``. A firmware that sent a block this
    # cannot read reports ``name == "unreadable"`` instead, because a broken
    # block and no block at all are different facts about the device.
    backend: InputBackendReport | None = None

    # What that backend's host stack and root port are doing, appended after
    # it. ``None`` when the firmware predates the block - a valid older payload
    # and not a parse error, the same way ``backend`` is. A firmware that sent
    # a block this cannot read reports ``state == "unreadable"``, and one that
    # has no host stack to observe reports ``state == "none"``.
    host_observation: HostObservation | None = None

    # The reference target's own counters, appended after the host block for
    # the same reason it was appended after the backend block. ``None`` means
    # the firmware predates Task 5 - a valid older payload, not a parse error.
    reference_counters: ReferenceCounters | None = None
    input_sources: tuple[InputSource, ...] | None = None
    rejected_interfaces: int | None = None


@dataclass(frozen=True)
class InputSource:
    vendor_id: int
    product_id: int
    interface_number: int
    kind: str
    device_address: int
    product_name: str = ""
    reports: int | None = None
    decoded_events: int | None = None
    last_report_size: int | None = None
    last_report: bytes = b""
    layout_source: int | None = None
    report_id: int | None = None
    minimum_body_bytes: int | None = None
    keyboard_error: int | None = None
    consumer_error: int | None = None


def _parse_input_sources(block: bytes) -> tuple[tuple[InputSource, ...] | None, int | None]:
    if not block:
        return None, None
    if len(block) < 8:
        raise PayloadError("input source inventory is truncated")
    version, size, count, rejected = struct.unpack_from("<BHBI", block)
    if version != 1:
        return None, None
    record = struct.Struct("<HHBBB48s")
    if count > 8 or size != 8 + count * record.size or len(block) < size:
        raise PayloadError("input source inventory has the wrong size")
    sources = []
    for offset in range(8, size, record.size):
        vid, pid, interface, kind, address, name = record.unpack_from(block, offset)
        sources.append(InputSource(vid, pid, interface, {1: "keyboard", 2: "mouse", 3: "consumer"}.get(kind, "unknown"), address, name.split(b"\0", 1)[0].decode("utf-8", errors="replace")))
    tail = block[size:]
    if tail:
        if len(tail) < 4:
            raise PayloadError("source decoding diagnostics are truncated")
        version, trace_size, trace_count = struct.unpack_from("<BHB", tail)
        if version == 1:
            record = struct.Struct("<IIB9sBBBBB")
            if trace_count != count or trace_size != 4 + count * record.size or len(tail) < trace_size:
                raise PayloadError("source decoding diagnostics have the wrong size")
            for index in range(count):
                reports, decoded, length, raw, origin, report_id, minimum, keyboard_error, consumer_error = record.unpack_from(tail, 4 + index * record.size)
                sources[index] = replace(sources[index], reports=reports, decoded_events=decoded,
                    last_report_size=length, last_report=raw[:min(length, 9)], layout_source=origin,
                    report_id=report_id, minimum_body_bytes=minimum, keyboard_error=keyboard_error, consumer_error=consumer_error)
    return tuple(sources), rejected


@dataclass(frozen=True)
class Transaction:
    """One in-flight request and the continuation that consumes its reply."""

    operation: str
    request_type: CdcMessageType
    reply_type: CdcMessageType
    sequence: int
    payload: bytes
    on_reply: Callable[[bytes], None]
    ignore_device_error: bool = False


class SequenceGenerator:
    """Allocates u16 request sequence numbers and follows the device's counter."""

    def __init__(self, start: int = 1) -> None:
        if not isinstance(start, int) or not 0 <= start <= 0xFFFF:
            raise ValueError("sequence start must be a u16")
        self._next = start

    def next(self) -> int:
        value = self._next
        self._next = (value + 1) & 0xFFFF
        return value

    def align_after(self, sequence: int) -> None:
        """Continue after a sequence the device chose (e.g. a capture event)."""
        if not isinstance(sequence, int) or not 0 <= sequence <= 0xFFFF:
            raise ValueError("sequence must be a u16")
        self._next = (sequence + 1) & 0xFFFF


@dataclass(frozen=True)
class FrameScan:
    """What one read of the byte stream yielded.

    ``frames`` are in arrival order. ``discarded`` holds one decoder message
    per candidate that turned out not to be a frame - kept rather than thrown
    away because it is the only description of what the device actually sent,
    and every hardware gate in this project has been read off exactly that
    sentence.
    """

    frames: tuple[CdcFrame, ...] = ()
    discarded: tuple[str, ...] = ()


class FrameAssembler:
    """Reassembles a byte stream into frames, retaining partial bytes.

    ``readyRead`` hands over whatever the driver happened to buffer, so a frame
    may be split across any number of reads and several frames may arrive in
    one. Everything after the last delimiter is kept for the next read.

    The stream is not frames alone. The reference target writes its plain-text
    trace to the same CDC endpoint as the protocol, a board that rebooted
    mid-reply leaves a half frame behind, and neither is worth losing a good
    frame over. So each delimited candidate is decoded here: the ones that
    decode are frames, and the ones that do not are dropped and named, with
    the scan continuing into the next candidate either way.

    Deciding *where a frame ends* is this class's job. Deciding *whether the
    bytes inside one are a legal frame* stays with the strict decoder in
    :mod:`duo_input.protocol.frame`, which is why the candidate goes to it
    whole and is believed unconditionally.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    @property
    def pending(self) -> int:
        """Number of retained bytes that do not yet form a complete frame."""
        return len(self._buffer)

    def clear(self) -> None:
        self._buffer.clear()

    def push(self, data: bytes) -> FrameScan:
        """Append ``data`` and return the frames it completed.

        Raises :class:`FrameOverflowError`, having discarded the buffer, when
        the retained bytes can no longer become a legal frame - a stream of
        junk carrying no delimiter at all must not grow this buffer for ever.
        """
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("data must be bytes-like")
        self._buffer.extend(data)
        frames: list[CdcFrame] = []
        discarded: list[str] = []
        while True:
            try:
                end = self._buffer.index(_FRAME_DELIMITER)
            except ValueError:
                break
            candidate = bytes(self._buffer[: end + 1])
            del self._buffer[: end + 1]
            if end == 0:
                # Two delimiters in a row carry no bytes, so there is nothing
                # here to be a damaged frame. The reference target reaches
                # this on purpose: it ends every trace line with a delimiter
                # and writes one more when it clears whatever was still in
                # flight as a conversation begins. Reporting it as a discard
                # would let a device that closed its trace politely fail the
                # very request it was making room for.
                continue
            try:
                frames.append(decode_cdc_frame(candidate))
            except FrameError as error:
                discarded.append(str(error))
        if len(self._buffer) > MAX_PENDING_FRAME_BYTES:
            retained = len(self._buffer)
            self._buffer.clear()
            raise FrameOverflowError(
                f"discarded {retained} undelimited bytes, "
                f"cap is {MAX_PENDING_FRAME_BYTES}"
            )
        return FrameScan(tuple(frames), tuple(discarded))


def reply_error(payload: bytes) -> ErrorCode:
    """Every reply payload starts with the device's error code."""
    if not payload:
        raise PayloadError("reply payload is empty")
    try:
        return ErrorCode(payload[0])
    except ValueError as error:
        raise PayloadError("unknown device error code") from error


def parse_device_info(payload: bytes) -> DeviceInfo:
    if len(payload) != _DEVICE_INFO.size:
        raise PayloadError("DEVICE_INFO payload has the wrong size")
    _, major, minor, capabilities, generation, profile, digest = _DEVICE_INFO.unpack(payload)
    return DeviceInfo(major, minor, capabilities, generation, profile, digest)


def parse_status(payload: bytes) -> DeviceStatus:
    if len(payload) != _STATUS.size:
        raise PayloadError("GET_STATUS payload has the wrong size")
    _, profile, capture, staging, release_all = _STATUS.unpack(payload)
    return DeviceStatus(profile, bool(capture), bool(staging), release_all)


def parse_config_info(payload: bytes) -> ActiveConfigInfo:
    if len(payload) != _CONFIG_INFO.size:
        raise PayloadError("config info payload has the wrong size")
    _, generation, size, digest = _CONFIG_INFO.unpack(payload)
    return ActiveConfigInfo(generation, size, digest)


def parse_hid_descriptor_capture(payload: bytes) -> HidDescriptorCapture:
    if len(payload) < _HID_DESCRIPTOR_CAPTURE.size:
        raise PayloadError("GET_HID_DESCRIPTOR_CAPTURE payload is too short")
    (
        _error,
        version,
        flags,
        vendor_id,
        product_id,
        interface_number,
        original_size,
        captured_size,
    ) = _HID_DESCRIPTOR_CAPTURE.unpack_from(payload)
    descriptor = bytes(payload[_HID_DESCRIPTOR_CAPTURE.size :])

    if version != 1:
        raise PayloadError("GET_HID_DESCRIPTOR_CAPTURE version is unsupported")
    if flags & ~0x03:
        raise PayloadError("GET_HID_DESCRIPTOR_CAPTURE flags are invalid")
    if captured_size > 256 or len(descriptor) != captured_size:
        raise PayloadError("GET_HID_DESCRIPTOR_CAPTURE size is inconsistent")

    present = bool(flags & 0x01)
    truncated = bool(flags & 0x02)
    if not present:
        if truncated or vendor_id or product_id or interface_number or original_size or captured_size:
            raise PayloadError("absent HID descriptor capture carries data")
    elif original_size == 0 or captured_size == 0:
        raise PayloadError("present HID descriptor capture is empty")
    elif truncated != (original_size > captured_size):
        raise PayloadError("HID descriptor truncation flag disagrees with its sizes")

    return HidDescriptorCapture(
        present,
        truncated,
        vendor_id,
        product_id,
        interface_number,
        original_size,
        descriptor,
    )


def parse_hid_report_sets(payload: bytes) -> HidReportSets:
    if len(payload) < _HID_REPORT_SETS.size:
        raise PayloadError("GET_HID_REPORT_SETS payload is too short")
    _error, version, source_count = _HID_REPORT_SETS.unpack_from(payload)
    if version != 1:
        raise PayloadError("GET_HID_REPORT_SETS version is unsupported")
    if source_count > 8:
        raise PayloadError("GET_HID_REPORT_SETS source count exceeds eight")

    sources: list[HidReportSource] = []
    identities: set[tuple[int, int, int]] = set()
    at = _HID_REPORT_SETS.size
    for _ in range(source_count):
        if len(payload) - at < _HID_REPORT_SOURCE.size:
            raise PayloadError("GET_HID_REPORT_SETS source count exceeds payload")
        values = _HID_REPORT_SOURCE.unpack_from(payload, at)
        at += _HID_REPORT_SOURCE.size
        (device_address, vendor_id, product_id, interface_number,
         accepted_count, rejected_count, rejected_overflow) = values
        if accepted_count > 8:
            raise PayloadError("GET_HID_REPORT_SETS accepted count exceeds eight")
        if rejected_count > 8:
            raise PayloadError("GET_HID_REPORT_SETS rejected count exceeds eight")
        required = (accepted_count + rejected_count) * _HID_REPORT_ENTRY.size
        if len(payload) - at < required:
            raise PayloadError("GET_HID_REPORT_SETS entry count exceeds payload")

        accepted: list[HidReportEntry] = []
        for _ in range(accepted_count):
            raw_role, report_id, minimum_body_bytes = _HID_REPORT_ENTRY.unpack_from(payload, at)
            at += _HID_REPORT_ENTRY.size
            try:
                role = HidReportRole(raw_role)
            except ValueError as error:
                raise PayloadError("GET_HID_REPORT_SETS accepted role is invalid") from error
            accepted.append(HidReportEntry(role, report_id, minimum_body_bytes))

        rejected: list[RejectedHidReportEntry] = []
        for _ in range(rejected_count):
            raw_role, report_id, raw_reason = _HID_REPORT_ENTRY.unpack_from(payload, at)
            at += _HID_REPORT_ENTRY.size
            try:
                role = HidReportRole(raw_role)
            except ValueError as error:
                raise PayloadError("GET_HID_REPORT_SETS rejected role is invalid") from error
            try:
                reason = HidReportRejectionReason(raw_reason)
            except ValueError as error:
                raise PayloadError("GET_HID_REPORT_SETS rejected reason is invalid") from error
            rejected.append(RejectedHidReportEntry(role, report_id, reason))

        identity = (vendor_id, product_id, interface_number)
        if identity in identities:
            raise PayloadError("GET_HID_REPORT_SETS carries a duplicate source identity")
        identities.add(identity)
        sources.append(HidReportSource(
            device_address, vendor_id, product_id, interface_number,
            tuple(accepted), tuple(rejected), rejected_overflow,
        ))

    if at != len(payload):
        raise PayloadError("GET_HID_REPORT_SETS has an unexpected tail")
    return HidReportSets(tuple(sources))


def parse_diagnostics(payload: bytes) -> DeviceDiagnostics:
    """Read the counters, and the link state when the firmware reports it.

    The link fields were appended after the counters rather than mixed in with
    them, so a reply that carries only the counters is still a reply this can
    read. Refusing it would turn an older device into an unreachable one over
    a field it never claimed to have.
    """
    counters = payload[: _DIAGNOSTICS.size]
    if len(counters) != _DIAGNOSTICS.size:
        raise PayloadError("GET_DIAGNOSTICS payload has the wrong size")
    _, bad_crc, disconnect, timeout, bad_sequence, aborted = _DIAGNOSTICS.unpack(counters)

    rest = payload[_DIAGNOSTICS.size :]
    if not rest:
        return DeviceDiagnostics(bad_crc, disconnect, timeout, bad_sequence, aborted)

    link = rest[: _LINK_STATE.size]
    if len(link) != _LINK_STATE.size:
        raise PayloadError("GET_DIAGNOSTICS payload has the wrong size")
    answering, mounted, frames_sent, link_crc, echoed = _LINK_STATE.unpack(link)

    endpoint = rest[_LINK_STATE.size : _LINK_STATE.size + _ENDPOINT_REPORT.size]
    if endpoint and len(endpoint) != _ENDPOINT_REPORT.size:
        raise PayloadError("GET_DIAGNOSTICS payload has the wrong size")
    drops, release_ms = _ENDPOINT_REPORT.unpack(endpoint) if endpoint else (None, None)

    tail = rest[_LINK_STATE.size + _ENDPOINT_REPORT.size :]
    dropped = tail[: _DROPPED_COMMANDS.size]
    if dropped and len(dropped) != _DROPPED_COMMANDS.size:
        raise PayloadError("GET_DIAGNOSTICS payload has the wrong size")
    (dropped_commands,) = _DROPPED_COMMANDS.unpack(dropped) if dropped else (None,)

    fault = tail[_DROPPED_COMMANDS.size : _DROPPED_COMMANDS.size + _RUNTIME_FAULT.size]
    if fault and len(fault) != _RUNTIME_FAULT.size:
        raise PayloadError("GET_DIAGNOSTICS payload has the wrong size")
    (runtime_fault,) = _RUNTIME_FAULT.unpack(fault) if fault else (None,)

    rest = tail[_DROPPED_COMMANDS.size + _RUNTIME_FAULT.size :]
    latency_bytes = _latency_block_size(rest)
    keyboard_latency, mouse_latency = _parse_latency(rest[:latency_bytes])
    peripherals, appended = _parse_peripherals(rest[latency_bytes:])
    backend, after_backend = _parse_backend(appended)
    host_observation, after_host = _parse_host_observation(after_backend)
    reference_counters = _parse_reference_counters(after_host)
    after_reference = after_host[1 + after_host[0]:] if after_host else b""
    input_sources, rejected_interfaces = _parse_input_sources(after_reference)

    return DeviceDiagnostics(
        bad_crc,
        disconnect,
        timeout,
        bad_sequence,
        aborted,
        endpoint_answering=bool(answering),
        endpoint_mounted=bool(mounted),
        link_frames_sent=frames_sent,
        link_crc_errors=link_crc,
        link_echoed_frames=echoed,
        endpoint_drops=drops,
        endpoint_release_ms=release_ms,
        dropped_commands=dropped_commands,
        runtime_fault=runtime_fault,
        keyboard_latency=keyboard_latency,
        mouse_latency=mouse_latency,
        peripherals=peripherals,
        backend=backend,
        host_observation=host_observation,
        reference_counters=reference_counters,
        input_sources=input_sources,
        rejected_interfaces=rejected_interfaces,
    )


def _parse_latency(
    block: bytes,
) -> tuple[LatencyHistogram | None, LatencyHistogram | None]:
    """Read the two histograms, or report that the firmware sent none.

    A short block is refused rather than half-read: a histogram missing its
    last buckets would still answer questions, and every answer would be
    computed over samples the device never sent.
    """
    if not block:
        return (None, None)

    bucket_count = block[0]
    if bucket_count < 2:
        raise PayloadError(
            "GET_DIAGNOSTICS latency block claims fewer than two buckets"
        )

    edge_bytes = 4 * (bucket_count - 1)
    stream_bytes = _LATENCY_HEAD.size + 4 * bucket_count
    if len(block) != 1 + edge_bytes + 2 * stream_bytes:
        raise PayloadError("GET_DIAGNOSTICS latency block has the wrong size")

    edges = tuple(
        _U32.unpack_from(block, 1 + 4 * index)[0] for index in range(bucket_count - 1)
    )

    histograms: list[LatencyHistogram] = []
    at = 1 + edge_bytes
    for _ in range(2):
        count, max_us = _LATENCY_HEAD.unpack_from(block, at)
        buckets = tuple(
            _U32.unpack_from(block, at + _LATENCY_HEAD.size + 4 * index)[0]
            for index in range(bucket_count)
        )
        histograms.append(LatencyHistogram(edges, buckets, count, max_us))
        at += stream_bytes

    return (histograms[0], histograms[1])


def _latency_block_size(rest: bytes) -> int:
    """How much of ``rest`` the latency block occupies.

    The block leads with its own bucket count, so its length is readable from
    the first byte. That is what lets a later block be appended behind it
    without either end holding a hard-coded offset that the other can change.
    """
    if not rest:
        return 0
    bucket_count = rest[0]
    if bucket_count < 2:
        raise PayloadError(
            "GET_DIAGNOSTICS latency block claims fewer than two buckets"
        )
    return 1 + 4 * (bucket_count - 1) + 2 * (_LATENCY_HEAD.size + 4 * bucket_count)


def _parse_peripherals(
    block: bytes,
) -> tuple[tuple[PeripheralPort, ...] | None, bytes]:
    """Read the two peripheral ports, and hand back whatever follows them.

    Returns ``(None, b"")`` when the firmware sent no peripheral block at all.
    A block that is present but shorter than two whole records is refused
    rather than half-read: a port assembled from the bytes that happened to
    arrive would still answer questions, and every answer would be about a
    device the firmware never described.

    Anything beyond the two records is the appended backend block and is
    returned untouched, so this stopped being the last block on the wire the
    moment that one was added - which is exactly how it was designed to grow.
    """
    if not block:
        return (None, b"")
    if len(block) < 2 * _PERIPHERAL.size:
        raise PayloadError("GET_DIAGNOSTICS peripheral block has the wrong size")

    ports: list[PeripheralPort] = []
    for index in range(2):
        (
            attached,
            ready,
            kind,
            vendor_id,
            product_id,
            buttons,
            descriptor_bytes,
            digest,
        ) = _PERIPHERAL.unpack_from(block, index * _PERIPHERAL.size)
        ports.append(
            PeripheralPort(
                attached=bool(attached),
                ready=bool(ready),
                kind=_PERIPHERAL_KINDS.get(kind, "unknown") if attached else "none",
                vendor_id=vendor_id,
                product_id=product_id,
                buttons=buttons or None,
                report_descriptor_bytes=descriptor_bytes,
                descriptor_hash=digest.hex() if descriptor_bytes else None,
            )
        )
    return (tuple(ports), bytes(block[2 * _PERIPHERAL.size :]))


def _parse_backend(block: bytes) -> tuple[InputBackendReport | None, bytes]:
    """Read the appended backend block, in both compatibility directions.

    ``None`` means the firmware sent no block. That is a valid older payload -
    the block was appended after the two peripheral records precisely so that
    firmware predating it stays readable - and refusing it would make this
    configurator the thing that broke, over a field the device never claimed to
    have.

    A block that IS there but cannot be read is reported as ``"unreadable"``
    rather than raised, because everything in front of it - the counters, the
    link state, the latency, both ports - is complete and correct however
    garbled the suffix is, and throwing all of that away would lose good
    readings over a trailing block nobody needs. It is still not reported as
    absent: a firmware that sent a broken block is not one that sent none, and
    conflating them would hide the defect.

    The block is: one backend identifier, one count of the u32 counters that
    follow, then that many counters. The count is what lets a backend publish
    none of them - CH375 keeps none of these figures, and twelve zeros would
    read as twelve measurements - and what lets a reader find the end of a
    block whose counter list is longer than the one it knows.

    That count is now also what finds the START of the block behind it. This
    stopped being the last block on the wire when the host block was appended,
    which is exactly how it was designed to grow, so whatever follows the
    counters is handed back untouched rather than read as a defect - the way
    ``_parse_peripherals`` above already hands this block back.
    """
    if not block:
        return (None, b"")
    if len(block) < 2:
        return (
            InputBackendReport(
                "unreadable",
                unreadable_reason=(
                    "the backend block is one byte long: an identifier with no "
                    "count of the counters behind it"
                ),
            ),
            b"",
        )
    identifier, count = block[0], block[1]
    expected = 2 + 4 * count
    if len(block) < expected:
        return (
            InputBackendReport(
                "unreadable",
                unreadable_reason=(
                    f"the backend block claims {count} counters, which needs "
                    f"{expected} bytes, and {len(block)} arrived"
                ),
            ),
            b"",
        )
    values = [_U32.unpack_from(block, 2 + 4 * index)[0] for index in range(count)]
    # Counters past the ones this configurator knows about were appended by a
    # later firmware; reading the ones in common and ignoring the rest is the
    # same append-only rule that lets older firmware be read here at all.
    named = dict(zip(_BACKEND_COUNTER_FIELDS, values))
    report = InputBackendReport(_INPUT_BACKENDS.get(identifier, "unknown"), **named)
    return (report, bytes(block[expected:]))


#: The reference-counters block's own fields: two u32 counters and two
#: single-byte flags, little-endian, in the wire's own order - behind their
#: own one-byte length, the same shape the host block uses.
_REFERENCE_COUNTERS_FIELDS = struct.Struct("<IIBB")


@dataclass(frozen=True)
class ReferenceCounters:
    """The reference target's own diagnostics.

    Task 3's bounded callback queue overflow count, and how many of U1's own
    USB interfaces earned no logical role, were both readable in the firmware
    from the day each was added but never read on real hardware - there was
    no CDC path to ask a board for them. Whether each of the two roles
    currently has an owner is what makes a route selected by a freshly loaded
    profile (PC1-only, PC2-only, both) something an operator can tell apart
    from one that never took effect, because a route with nothing ready
    behind it produces the same silence as a route that is misconfigured.
    """

    #: ``"reported"`` - the fields below are real readings.
    #: ``"none"`` - the firmware links ConfigService and has not published
    #: these counters. True of every backend except the reference target:
    #: CH375 and PIO_USB never call set_reference_counters, and reporting
    #: zeros for them would read as real measurements on a board that never
    #: took them - the same failure mode the host block's own "none" state
    #: exists to prevent.
    #: ``"unreadable"`` - a block was there and did not parse.
    state: str = "none"
    #: Set only when state is ``"unreadable"``. Everything in front of this
    #: block in the reply is still complete and correct, the same rule the
    #: backend and host blocks follow.
    unreadable_reason: str | None = None
    #: ``None`` in every state but ``"reported"``.
    callback_overflows: int | None = None
    ignored_interfaces: int | None = None
    keyboard_ready: bool | None = None
    mouse_ready: bool | None = None


def _parse_reference_counters(block: bytes) -> ReferenceCounters | None:
    """Read the appended reference-counters block, in both compatibility
    directions.

    ``None`` means the firmware sent no block at all - a valid older payload,
    since this was appended after everything above it for the same reason the
    backend and host blocks were: firmware built before Task 5 sends nothing
    here.

    A leading length of zero means the firmware HAS the block and published
    nothing into it - true of every backend except the reference target, and
    reported as ``state == "none"``, the same way CH375's empty host block
    reports ``HostObservation.state == "none"``.

    A block that IS there and cannot be read is reported as
    ``"unreadable"`` rather than raised, for the same reason the backend and
    host blocks are: everything in front of it is complete and correct
    however garbled this trailing block is.
    """
    if not block:
        return None
    declared = block[0]
    if declared == 0:
        return ReferenceCounters(state="none")
    body = block[1:]
    if len(body) < declared:
        return ReferenceCounters(
            state="unreadable",
            unreadable_reason=(
                f"the reference-counters block declares {declared} bytes of "
                f"fields and {len(body)} arrived"
            ),
        )
    if declared < _REFERENCE_COUNTERS_FIELDS.size:
        return ReferenceCounters(
            state="unreadable",
            unreadable_reason=(
                f"the reference-counters block declares {declared} bytes of "
                f"fields, fewer than the {_REFERENCE_COUNTERS_FIELDS.size} "
                "this configurator reads"
            ),
        )
    callback_overflows, ignored_interfaces, keyboard_ready, mouse_ready = (
        _REFERENCE_COUNTERS_FIELDS.unpack_from(body, 0)
    )
    return ReferenceCounters(
        state="reported",
        callback_overflows=callback_overflows,
        ignored_interfaces=ignored_interfaces,
        keyboard_ready=bool(keyboard_ready),
        mouse_ready=bool(mouse_ready),
    )


def _completions_since_attach(
    host_event_counts: int | None, at_attach: int | None
) -> int | None:
    """Transfer completions queued after the latest accepted attach.

    ``None`` when either input is absent, which is what an older firmware
    sends. Clamped at zero rather than allowed to go negative: the snapshot is
    taken from the same monotonic total, so a negative difference is not a
    reading, it is a firmware defect, and a negative number in a report reads
    as neither.
    """
    if host_event_counts is None or at_attach is None:
        return None
    total = (host_event_counts >> HOST_EVENT_XFER_SHIFT) & 0xFFFF
    return max(0, total - at_attach)


def _parse_host_observation(block: bytes) -> tuple[HostObservation | None, bytes]:
    """Read the appended host block, in both compatibility directions.

    ``None`` means the firmware sent no block. That is a valid older payload -
    everything here was appended behind the backend block precisely so that
    firmware predating it stays readable - and refusing it would make this
    configurator the thing that broke.

    A leading length of zero means the firmware HAS the block and has nothing
    to put in it: the CH375 image has no host stack, no root port and no input
    core backend loop, so every field would be a reading of hardware that is
    not there. That is reported as ``state == "none"`` with every field
    ``None``, which is deliberately different from a firmware that sent no
    block at all, and from a host that genuinely measured zero.

    A block that IS there and cannot be read is reported as ``"unreadable"``
    rather than raised, for the same reason the backend block is: everything in
    front of it is complete and correct however garbled the suffix is.

    The length byte is also what lets a later firmware append more fields here
    without this reader changing: it reads the fields it knows and ignores the
    rest. Whatever follows the declared body is handed back untouched, so this
    stopped being the last block on the wire the moment the reference-counters
    block was appended behind it - the same way ``_parse_backend`` already
    hands its own trailing bytes back.
    """
    if not block:
        return (None, b"")
    declared = block[0]
    if declared == 0:
        return (HostObservation("none"), bytes(block[1:]))
    body = block[1:]
    if len(body) < declared:
        return (
            HostObservation(
                "unreadable",
                unreadable_reason=(
                    f"the host block declares {declared} bytes of fields and "
                    f"{len(body)} arrived"
                ),
            ),
            b"",
        )
    if declared < _HOST_OBSERVATION_BASE.size:
        return (
            HostObservation(
                "unreadable",
                unreadable_reason=(
                    f"the host block declares {declared} bytes of fields, fewer "
                    f"than the {_HOST_OBSERVATION_BASE.size} base this configurator reads"
                ),
            ),
            b"",
        )
    # Whatever follows the declared body, handed back untouched. Computed
    # here rather than at the top: only once ``declared`` has passed the
    # length check above is ``1 + declared`` known to be within ``block``.
    rest = bytes(block[1 + declared :])
    (
        init_flags,
        clock_at_begin,
        clock_now,
        sof_frames,
        root_state,
        root_connects,
        core1_passes,
    ) = _HOST_OBSERVATION_BASE.unpack_from(body, 0)
    extension_values: list[int | None] = []
    at = _HOST_OBSERVATION_BASE.size
    for field in (
        "<H",
        "<H",
        "<H",
        "<B",
        "<B",
        "<I",
        "<H",
        "<H",
        "<H",
        "<I",
        "<I",
        "<I",
        "<I",
        "<I",
        "<I",
        "<I",
        "<I",
        "<I",
        "<I",
        "<I",
    ):
        width = struct.calcsize(field)
        if declared == at:
            extension_values.append(None)
            continue
        if declared < at + width:
            return (
                HostObservation(
                    "unreadable",
                    unreadable_reason=(
                        f"the host block declares {declared} bytes of fields, which "
                        f"cuts through a {width}-byte field beginning at byte {at}"
                    ),
                ),
                b"",
            )
        extension_values.append(struct.unpack_from(field, body, at)[0])
        at += width
    extension = tuple(extension_values)
    return (
        HostObservation(
            "reported",
            host_already_active=bool(init_flags & _HOST_ALREADY_ACTIVE),
            host_configured=bool(init_flags & _HOST_CONFIGURED),
            host_initialized=bool(init_flags & _HOST_INITIALIZED),
            host_inited=bool(init_flags & _HOST_INITED),
            clock_hz_before_core1_change=clock_at_begin,
            clock_hz_now=clock_now,
            sof_frame_count=sof_frames,
            root_port_initialized=bool(root_state & _ROOT_INITIALIZED),
            root_port_connected=bool(root_state & _ROOT_CONNECTED),
            root_port_suspended=bool(root_state & _ROOT_SUSPENDED),
            root_port_fullspeed=bool(root_state & _ROOT_FULLSPEED),
            root_port_connects=root_connects,
            core1_passes=core1_passes,
            mount_events=extension[0],
            umount_events=extension[1],
            hid_mount_events=extension[2],
            ep_slots_opened=extension[3],
            ep_max_failed_count=extension[4],
            max_pass_gap_us=extension[5],
            max_sof_gap=extension[6],
            root_port_resets=extension[7],
            hub_mount_events=extension[8],
            ep_slot_map=extension[9],
            host_event_counts=extension[10],
            enum_progress_mask=extension[11],
            long_pass_count=extension[12],
            long_pass_total_ms=extension[13],
            core1_min_sp=extension[14],
            ep_transfer_flags=extension[15],
            xfer_completions_at_attach=extension[16],
            enum_stall_recoveries=extension[17],
            sof_interval_min_us=extension[18],
            sof_interval_max_us=extension[19],
            xfer_completions_since_attach=_completions_since_attach(
                extension[10], extension[16]
            ),
        ),
        rest,
    )


def parse_capture_event(payload: bytes) -> Trigger:
    """Turn one CAPTURE_EVENT payload into the trigger the operator pressed.

    Three bytes is the legacy shape: kind, code and the HID modifier byte,
    with no host-side interpretation in between. The emulator and older
    firmware still send exactly that, so it is read as "source unknown" -
    refusing it would break the compatibility matrix this repository ships.
    Eight bytes is the current shape: the same three bytes plus the VID, PID
    and interface number of the source that produced the press. Any other
    length is refused.

    All zero in the eight-byte form means the same "unknown" the three-byte
    form means - the firmware sends exactly that whenever its source table
    could not resolve the press - so it decodes to ``None`` rather than to
    ``TriggerSource(0, 0, 0)``. This mirrors the identical wire triple's other
    decoder, the stored-binding source at ``config_binary.py`` around line
    501, so the wire has exactly one representation of "unknown" reaching the
    domain layer. A triple that is zero in only one of VID/PID is neither
    shape and is refused, the same way that decoder refuses it.
    """
    if len(payload) == _CAPTURE_EVENT_FULL.size:
        kind, code, modifiers, vendor_id, product_id, interface_number = (
            _CAPTURE_EVENT_FULL.unpack(payload)
        )
        if vendor_id == 0 and product_id == 0 and interface_number == 0:
            source: TriggerSource | None = None
        elif vendor_id == 0 or product_id == 0:
            raise PayloadError("CAPTURE_EVENT source is partially zero")
        else:
            source = TriggerSource(vendor_id, product_id, interface_number)
    elif len(payload) == _CAPTURE_EVENT_LEGACY.size:
        kind, code, modifiers = _CAPTURE_EVENT_LEGACY.unpack(payload)
        source = None
    else:
        raise PayloadError("CAPTURE_EVENT payload has the wrong size")
    try:
        trigger_kind = TriggerKind(kind)
    except ValueError as error:
        raise PayloadError("CAPTURE_EVENT carries an unknown trigger kind") from error
    if trigger_kind is TriggerKind.MOUSE_BUTTON and (not 1 <= code <= 5 or modifiers):
        raise PayloadError("CAPTURE_EVENT mouse button is out of range")
    if trigger_kind is TriggerKind.CONSUMER_USAGE:
        code |= modifiers << 8
        modifiers = 0
    if not code:
        raise PayloadError("CAPTURE_EVENT carries no trigger code")
    return Trigger(trigger_kind, code, modifiers, source)


def parse_chunk_ack(payload: bytes) -> int:
    if len(payload) != _CHUNK_ACK.size:
        raise PayloadError("WRITE_CHUNK acknowledgement has the wrong size")
    return _CHUNK_ACK.unpack(payload)[1]


def parse_read_chunk(payload: bytes) -> tuple[int, bytes]:
    if len(payload) < _CHUNK_ACK.size:
        raise PayloadError("READ_CONFIG_CHUNK payload has the wrong size")
    offset = _CHUNK_ACK.unpack_from(payload)[1]
    return offset, bytes(payload[_CHUNK_ACK.size :])


def write_begin_payload(size: int, digest: bytes) -> bytes:
    if len(digest) != 32:
        raise PayloadError("WRITE_BEGIN requires a 32-byte digest")
    return struct.pack("<I", size) + digest


def write_chunk_payload(offset: int, chunk: bytes) -> bytes:
    return struct.pack("<I", offset) + chunk


def read_chunk_payload(offset: int, length: int) -> bytes:
    return struct.pack("<IH", offset, length)


def percentage(done: int, total: int) -> int:
    """Progress in 0..100 with both endpoints reachable."""
    if total <= 0:
        return 100
    return min(100, done * 100 // total)


__all__ = [
    "MAX_PENDING_FRAME_BYTES",
    "ActiveConfigInfo",
    "DeviceDiagnostics",
    "DeviceInfo",
    "DeviceStatus",
    "ErrorCode",
    "FailureReason",
    "FrameAssembler",
    "FrameOverflowError",
    "FrameScan",
    "InputBackendReport",
    "LatencyHistogram",
    "HostObservation",
    "HidDescriptorCapture",
    "HidReportEntry",
    "HidReportRejectionReason",
    "HidReportRole",
    "HidReportSets",
    "HidReportSource",
    "PeripheralPort",
    "ReferenceCounters",
    "OperationFailure",
    "OperationResult",
    "PayloadError",
    "SequenceGenerator",
    "Transaction",
    "parse_capture_event",
    "parse_chunk_ack",
    "parse_config_info",
    "parse_device_info",
    "parse_diagnostics",
    "parse_hid_descriptor_capture",
    "parse_hid_report_sets",
    "parse_read_chunk",
    "parse_status",
    "percentage",
    "read_chunk_payload",
    "reply_error",
    "write_begin_payload",
    "write_chunk_payload",
]
