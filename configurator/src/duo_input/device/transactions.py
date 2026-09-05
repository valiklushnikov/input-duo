"""Framing, sequencing and payload parsing for the host side of the CDC link.

This module is deliberately free of Qt and of any I/O: it is the pure part of
the device layer, which keeps :mod:`duo_input.device.service` small enough to
reason about.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from duo_input.domain.models import Trigger

# ErrorCode is defined exactly once, in protocol/schema.json, and reaches every
# side of the link through generated code. Importing it here from the emulator -
# which is test-support, not production - is how the host and the firmware would
# come to disagree about an error code without either of them changing.
from duo_input.generated.protocol import (
    CdcMessageType,
    ErrorCode,
    InputBackend,
    TriggerKind,
)

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
_CAPTURE_EVENT = struct.Struct("<BBB")


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


class FrameAssembler:
    """Splits a byte stream into COBS frames, retaining partial bytes.

    ``readyRead`` hands over whatever the driver happened to buffer, so a frame
    may be split across any number of reads and several frames may arrive in
    one. Everything after the last delimiter is kept for the next read.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    @property
    def pending(self) -> int:
        """Number of retained bytes that do not yet form a complete frame."""
        return len(self._buffer)

    def clear(self) -> None:
        self._buffer.clear()

    def push(self, data: bytes) -> list[bytes]:
        """Append ``data`` and return every complete delimited frame.

        Raises :class:`FrameOverflowError`, having discarded the buffer, when
        the retained bytes can no longer become a legal frame.
        """
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("data must be bytes-like")
        self._buffer.extend(data)
        frames: list[bytes] = []
        while True:
            try:
                end = self._buffer.index(_FRAME_DELIMITER)
            except ValueError:
                break
            frames.append(bytes(self._buffer[: end + 1]))
            del self._buffer[: end + 1]
        if len(self._buffer) > MAX_PENDING_FRAME_BYTES:
            retained = len(self._buffer)
            self._buffer.clear()
            raise FrameOverflowError(
                f"discarded {retained} undelimited bytes, "
                f"cap is {MAX_PENDING_FRAME_BYTES}"
            )
        return frames


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
    backend = _parse_backend(appended)

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


def _parse_backend(block: bytes) -> InputBackendReport | None:
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
    block whose counter list is longer than the one it knows. Being the last
    block, its length is checked exactly; whoever appends the next block
    changes that the same way the peripheral block above was changed.
    """
    if not block:
        return None
    if len(block) < 2:
        return InputBackendReport(
            "unreadable",
            unreadable_reason=(
                "the backend block is one byte long: an identifier with no "
                "count of the counters behind it"
            ),
        )
    identifier, count = block[0], block[1]
    expected = 2 + 4 * count
    if len(block) != expected:
        return InputBackendReport(
            "unreadable",
            unreadable_reason=(
                f"the backend block claims {count} counters, which needs "
                f"{expected} bytes, and {len(block)} arrived"
            ),
        )
    values = [_U32.unpack_from(block, 2 + 4 * index)[0] for index in range(count)]
    # Counters past the ones this configurator knows about were appended by a
    # later firmware; reading the ones in common and ignoring the rest is the
    # same append-only rule that lets older firmware be read here at all.
    named = dict(zip(_BACKEND_COUNTER_FIELDS, values))
    return InputBackendReport(_INPUT_BACKENDS.get(identifier, "unknown"), **named)


def parse_capture_event(payload: bytes) -> Trigger:
    """Turn one CAPTURE_EVENT payload into the trigger the operator pressed.

    The payload is the trigger itself - kind, code and HID modifier byte - so
    the value the device reports and the value the project stores are the same
    three numbers, with no host-side interpretation in between.
    """
    if len(payload) != _CAPTURE_EVENT.size:
        raise PayloadError("CAPTURE_EVENT payload has the wrong size")
    kind, code, modifiers = _CAPTURE_EVENT.unpack(payload)
    try:
        trigger_kind = TriggerKind(kind)
    except ValueError as error:
        raise PayloadError("CAPTURE_EVENT carries an unknown trigger kind") from error
    if trigger_kind is TriggerKind.MOUSE_BUTTON and (not 1 <= code <= 5 or modifiers):
        raise PayloadError("CAPTURE_EVENT mouse button is out of range")
    if not code:
        raise PayloadError("CAPTURE_EVENT carries no trigger code")
    return Trigger(trigger_kind, code, modifiers)


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
    "InputBackendReport",
    "LatencyHistogram",
    "PeripheralPort",
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
    "parse_read_chunk",
    "parse_status",
    "percentage",
    "read_chunk_payload",
    "reply_error",
    "write_begin_payload",
    "write_chunk_payload",
]
