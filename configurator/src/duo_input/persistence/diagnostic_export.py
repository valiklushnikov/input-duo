"""The diagnostic report, and the rule that it carries no macro text.

A diagnostic ZIP is something an operator emails to a stranger. What they type
with their macros - passwords, commands, personal messages - is exactly the
kind of thing that must not travel with it. So the report is built from device
counters and versions only, the project file is included solely when the
operator asks for it by name, and the application log is redacted before any
text step can reach it.

Protocol v1 reports fewer facts than a full report wants. Where it reports
nothing, the field says ``unknown`` rather than a plausible-looking value.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from duo_input import __version__
# For the host block's own bit layout. Imported from the module that reads the
# wire rather than restated here: two copies of a bit position are two chances
# for the report to decode a reading into a different device than the one the
# firmware named.
from duo_input.device import transactions
from duo_input.generated.protocol import Capability

#: Names inside the archive. They are identifiers, not localised labels.
DIAGNOSTICS_MEMBER = "diagnostics.json"
LOG_MEMBER = "application.log"
PROJECT_MEMBER = "project.duoinput.json"

#: Shown wherever protocol v1 genuinely carries no value.
UNKNOWN = "unknown"

#: What a redacted phrase is replaced with in the log.
REDACTION = "***"


#: What the two logical role slots are called in a report.
#:
#: Backend-neutral on purpose. "Keyboard channel" and "mouse channel" named a
#: pair of CH375 pins; the PIO USB host has one bus and no channels at all, so
#: a label built on them would be wrong on half the builds this configurator
#: talks to. What survives both backends is the logical role V1 accepts exactly
#: one of - a keyboard and a mouse - which is also what the wire has always
#: ordered these two records by.
ROLE_SLOTS = ("Keyboard", "Mouse")


@dataclass(frozen=True)
class PeripheralIdentity:
    """One USB device the U1 has enumerated, as it identifies itself."""

    role: str
    vendor_id: str = UNKNOWN
    product_id: str = UNKNOWN
    descriptor_hash: str = UNKNOWN

    def __str__(self) -> str:
        """One line, for the page that shows these side by side."""
        if self.vendor_id == UNKNOWN:
            return f"{self.role}: none"
        return f"{self.role}: {self.vendor_id}:{self.product_id}"


@dataclass(frozen=True)
class DiagnosticSnapshot:
    """Everything the report knows at one moment. No project data at all."""

    application_version: str = __version__
    protocol_version: str = UNKNOWN
    u1_firmware_version: str = UNKNOWN
    u2_firmware_version: str = UNKNOWN
    chip_id: str = UNKNOWN
    reset_reason: str = UNKNOWN
    watchdog_count: str = UNKNOWN
    #: Which host stack read U1's own USB ports: "CH375", "PIO_USB", or
    #: ``unknown`` for firmware that names none. Never guessed - a report that
    #: names the wrong backend is worse than one that names none, because the
    #: two fail in entirely different ways and half the counters below only
    #: exist on one of them.
    input_backend: str = UNKNOWN
    #: That backend's own counters, by name, and only the ones it actually
    #: sent. Empty for a backend that publishes none and for firmware that
    #: names no backend at all.
    input_backend_counters: dict[str, int] = field(default_factory=dict)
    #: What that backend's host stack and its raw root port are doing.
    #:
    #: Every counter above is a reason a peripheral that enumerated was not
    #: read; none of them says anything when nothing enumerates. These are the
    #: readings from below all of that - whether the host stack started, on
    #: whose clock, whether the port is being driven and whether U1's input
    #: core is still turning. ``unknown`` wherever the device reported nothing:
    #: a firmware that predates the block, and an image with no host stack to
    #: observe, both genuinely measured none of this.
    host_stack: dict[str, str] = field(default_factory=dict)
    peripherals: tuple[PeripheralIdentity, ...] = ()
    advertised_capabilities: tuple[str, ...] = ()
    device_generation: str = UNKNOWN
    device_hash: str = UNKNOWN
    spi_crc_errors: int | str = UNKNOWN
    spi_timeouts: str = UNKNOWN
    spi_frames_sent: int | str = UNKNOWN
    spi_echoed_frames: int | str = UNKNOWN
    endpoint_answering: str = UNKNOWN
    endpoint_usb: str = UNKNOWN
    endpoint_drops: int | str = UNKNOWN
    endpoint_release_ms: int | str = UNKNOWN
    dropped_commands: int | str = UNKNOWN
    cdc_bad_crc: int | str = UNKNOWN
    cdc_bad_sequence: int | str = UNKNOWN
    cdc_timeout: int | str = UNKNOWN
    cdc_disconnect: int | str = UNKNOWN
    cdc_aborted_staging: int | str = UNKNOWN

    @classmethod
    def unknown(cls) -> DiagnosticSnapshot:
        """A report from a machine with no device attached."""
        return cls()

    @classmethod
    def from_service(cls, service: object) -> DiagnosticSnapshot:
        """Read whatever the connected device has already reported.

        Nothing is requested here: the caller decides when to spend a round
        trip on ``get_diagnostics``. What has not been asked for stays unknown.
        """
        if not getattr(service, "is_connected", False):
            return cls.unknown()
        info = getattr(service, "device_info", None)
        counters = getattr(service, "diagnostics", None)
        if info is None:
            return cls.unknown()

        capabilities = tuple(
            capability.name
            for capability in Capability
            if info.capabilities & int(capability)
        )
        digest = getattr(service, "device_hash", b"") or b""
        return cls(
            protocol_version=f"{info.protocol_major}.{info.protocol_minor}",
            advertised_capabilities=capabilities,
            device_generation=str(info.active_generation),
            device_hash=bytes(digest).hex() or UNKNOWN,
            cdc_bad_crc=_counter(counters, "bad_crc"),
            cdc_bad_sequence=_counter(counters, "bad_sequence"),
            cdc_timeout=_counter(counters, "timeout"),
            cdc_disconnect=_counter(counters, "disconnect"),
            cdc_aborted_staging=_counter(counters, "aborted_staging"),
            spi_crc_errors=_counter(counters, "link_crc_errors"),
            spi_frames_sent=_counter(counters, "link_frames_sent"),
            spi_echoed_frames=_counter(counters, "link_echoed_frames"),
            endpoint_answering=_yes_no(counters, "endpoint_answering"),
            endpoint_usb=_yes_no(counters, "endpoint_mounted"),
            endpoint_drops=_counter(counters, "endpoint_drops"),
            endpoint_release_ms=_counter(counters, "endpoint_release_ms"),
            dropped_commands=_counter(counters, "dropped_commands"),
            input_backend=_backend_name(counters),
            input_backend_counters=_backend_counters(counters),
            host_stack=_host_stack(counters),
            peripherals=_peripherals(counters),
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2, sort_keys=True)


def _yes_no(counters: object, field: str) -> str:
    """A plain yes or no, because this is the line someone reads first.

    Firmware predating the field reports nothing rather than guessing, and the
    report then says unknown - which is not the same as "no" and must stay
    different from it.
    """
    value = getattr(counters, field, None)
    if value is None:
        return UNKNOWN
    return "yes" if value else "no"


def _counter(counters: object, name: str) -> int | str:
    value = getattr(counters, name, None)
    return UNKNOWN if value is None else int(value)


def _backend_name(counters: object) -> str:
    """Which backend the firmware named, or ``unknown`` if it named none.

    Firmware predating the field sends no backend block at all, and this must
    not fill that silence in with a guess: an operator reading "CH375" in a
    report from a PIO USB board would chase the wrong half of the firmware.
    """
    backend = getattr(counters, "backend", None)
    if backend is None:
        return UNKNOWN
    name = getattr(backend, "name", UNKNOWN)
    return UNKNOWN if name == "unknown" else str(name)


def _backend_counters(counters: object) -> dict[str, int]:
    backend = getattr(counters, "backend", None)
    if backend is None or not hasattr(backend, "counters"):
        return {}
    return dict(backend.counters())


#: The host block's fields in the order a person reads them: did the stack
#: start, on whose clock, is the port being driven, is the core alive.
_HOST_STACK_ROWS = (
    ("host_already_active", "Host stack was already up before Core 1"),
    ("host_configured", "Host configure succeeded"),
    ("host_initialized", "Host init succeeded"),
    ("host_inited", "Host stack reports itself initialised"),
    ("clock_hz_before_core1_change", "System clock when the input core began (Hz)"),
    # The divider clock, whenever the row above it reads no. Labelled for what
    # it is rather than for what it proves, because what it proves depends on
    # that row: with the host stack already up, the dividers were computed
    # somewhere this reading never saw.
    ("clock_hz_now", "System clock now, and the PIO divider clock (Hz)"),
    ("sof_frame_count", "Root-port frames sent"),
    ("root_port_initialized", "Root port initialised"),
    ("root_port_connected", "Root port connected"),
    ("root_port_suspended", "Root port suspended"),
    ("root_port_fullspeed", "Root port at full speed"),
    ("root_port_connects", "Root-port attaches seen (lower bound)"),
    ("core1_passes", "Input core passes"),
    ("mount_events", "Device mount callbacks"),
    ("umount_events", "Device unmount callbacks"),
    ("hid_mount_events", "HID mount callbacks"),
    ("ep_slots_opened", "Endpoint slots opened (high-water)"),
    ("ep_max_failed_count", "Endpoint transaction failures (high-water)"),
    ("max_pass_gap_us", "Longest input-core pass gap (us)"),
    ("max_sof_gap", "Largest SOF-frame advance between passes"),
    ("root_port_resets", "Root-port resets seen (lower bound)"),
    ("hub_mount_events", "Hub mounts seen (lower bound)"),
)


#: The readings from inside the window where enumeration stops, in the order a
#: person works through them: whose endpoints are open, what the host stack
#: saw, how far each address got, what it spent the time on, and how close the
#: input core's stack came to the bottom while all of it ran.
#:
#: Separate from the table above because every one of these is a packed word.
#: A row that printed ``ep_slot_map: 2964492288`` would be a reading nobody
#: takes correctly at a bench, and a bench trip is what this costs.
#:
#: Two of the labels carry a caveat, and they carry it because the label is
#: what reaches the exported report and the page while a docstring reaches
#: nobody. Blocked passes are what a HEALTHY enumeration produces, and a zero
#: stack pointer is a sample that was never taken rather than a stack that
#: reached address zero.
_HOST_STACK_PACKED_ROWS = (
    ("ep_slot_map", "Endpoint slot map, live (pool slots 0-3)"),
    ("host_event_counts", "Host events queued since boot"),
    ("enum_progress_mask", "Enumeration reached, by address (1-4 devices, 5 hub)"),
    ("long_pass_count", "Input-core passes blocked over 20 ms (some are normal)"),
    (
        "long_pass_total_ms",
        "Time in passes blocked over 20 ms (ms; ~500 per enumeration is normal)",
    ),
    (
        "core1_min_sp",
        "Deepest input-core stack pointer (0 = no host event was ever queued)",
    ),
    ("ep_transfer_flags", "Endpoint transfer state, live (pool slots 0-3)"),
    (
        "xfer_completions_at_attach",
        "Transfer completions when latest attach was queued (subtract from current total)",
    ),
)


def host_stack_field_names() -> set[str]:
    """Every host-block reading this module turns into a row.

    Exists so a test can compare it against the parser's own field list. A
    reading that reaches the wire and the parser but no row is a reading nobody
    ever sees, and that failure mode costs a bench trip rather than a test run.
    """
    return {name for name, _ in _HOST_STACK_ROWS} | {
        name for name, _ in _HOST_STACK_PACKED_ROWS
    }


def _endpoint_slot_map(value: int) -> str:
    """The four pool slots, named rather than packed.

    The whole enumeration analysis turns on which device owns the third open
    slot, so this says ``dev0 ep0 out`` where the wire says ``0x10``.
    """
    parts: list[str] = []
    for slot in range(transactions.EP_SLOT_COUNT):
        byte = (value >> (8 * slot)) & 0xFF
        if not byte & transactions.EP_SLOT_OPEN:
            parts.append(f"slot{slot} closed")
            continue
        address = (
            byte >> transactions.EP_SLOT_ADDRESS_SHIFT
        ) & transactions.EP_SLOT_ADDRESS_MASK
        number = byte & transactions.EP_SLOT_ENDPOINT_MASK
        direction = "in" if byte & transactions.EP_SLOT_DIRECTION_IN else "out"
        parts.append(f"slot{slot} dev{address} ep{number} {direction}")
    return " | ".join(parts)


def _host_event_counts(value: int) -> str:
    attach = value & 0xFF
    remove = (value >> transactions.HOST_EVENT_REMOVE_SHIFT) & 0xFF
    transfers = (value >> transactions.HOST_EVENT_XFER_SHIFT) & 0xFFFF
    return f"attach {attach} | remove {remove} | transfer completions {transfers}"


def _endpoint_transfer_flags(value: int) -> str:
    """Decode whether each pool slot is idle or carrying a control stage."""
    parts: list[str] = []
    for slot in range(transactions.EP_SLOT_COUNT):
        byte = (value >> (8 * slot)) & 0xFF
        if not byte & transactions.EP_XFER_OPEN:
            parts.append(f"slot{slot} closed")
            continue

        state = [f"slot{slot} open"]
        active = bool(byte & transactions.EP_XFER_HAS_TRANSFER)
        state.append("active" if active else "idle")
        if active:
            if byte & transactions.EP_XFER_SETUP_STAGED:
                state.append("SETUP")
            elif byte & transactions.EP_XFER_DATA1:
                state.append("DATA1")
            else:
                state.append("DATA0")
            state.append(
                "host-out" if byte & transactions.EP_XFER_HOST_OUT else "host-in"
            )
        if byte & transactions.EP_XFER_NEED_PRE:
            state.append("PRE")
        if byte & transactions.EP_XFER_STALLED:
            state.append("stalled")
        if byte & transactions.EP_XFER_ABORTED:
            state.append("aborted")
        parts.append(" ".join(state))
    return " | ".join(parts)


def _enum_progress(value: int) -> str:
    """What each device address reached, spelled out for every address.

    Every address is listed, including the ones that reached nothing: on a
    board where enumeration stopped, "dev1 nothing" through "dev4 nothing" is
    the answer, and a row that showed only the addresses that got somewhere
    would have hidden it behind an absence.
    """
    parts: list[str] = []
    for address in range(1, transactions.ENUM_HIGHEST_ADDRESS + 1):
        bit = address - 1
        configured = bool(value & (1 << bit))
        descriptor = bool(value & (1 << (bit + transactions.ENUM_DESCRIPTOR_SHIFT)))
        if configured and descriptor:
            reached = "configured+descriptor"
        elif configured:
            reached = "configured"
        elif descriptor:
            reached = "descriptor"
        else:
            reached = "nothing"
        parts.append(f"dev{address} {reached}")
    return " | ".join(parts)


def _stack_pointer(value: int) -> str:
    """Hex, because a stack pointer is an address and gets compared to one.

    Zero stays plain zero: it is this field's "never sampled", and dressing it
    up as ``0x00000000`` would make it look like an address that was reached.
    """
    return "0" if value == 0 else f"0x{value:08X}"


#: How each packed reading is rendered. Anything without an entry here is
#: rendered as its plain decimal value.
_HOST_STACK_FORMATTERS = {
    "ep_slot_map": _endpoint_slot_map,
    "host_event_counts": _host_event_counts,
    "enum_progress_mask": _enum_progress,
    "core1_min_sp": _stack_pointer,
    "ep_transfer_flags": _endpoint_transfer_flags,
}


#: The row a broken host block gets instead of readings.
HOST_STACK_UNREADABLE = "Host stack readings"


def _host_stack(counters: object) -> dict[str, str]:
    """What the host stack reported, by label, or nothing at all.

    An empty mapping covers both firmware that predates the block and an image
    with no host stack to observe. Neither measured any of this, and a report
    printing "Root-port frames sent: 0" for a board with no root port would
    have invented a measurement - the same rule the backend counters above
    already follow.

    A block that arrived and could not be read is neither of those, and it says
    so in its own row rather than reading as an absence. A firmware that sent a
    broken block is not a firmware that sent none, and a report that conflated
    them would hide the defect behind the same blank the CH375 image leaves.
    """
    observation = getattr(counters, "host_observation", None)
    if observation is None:
        return {}
    state = getattr(observation, "state", None)
    if state == "unreadable":
        reason = getattr(observation, "unreadable_reason", None) or "no reason given"
        return {HOST_STACK_UNREADABLE: f"unreadable: {reason}"}
    if state != "reported":
        return {}
    rows: dict[str, str] = {}
    for name, label in _HOST_STACK_ROWS + _HOST_STACK_PACKED_ROWS:
        value = getattr(observation, name, None)
        if value is None:
            continue
        if isinstance(value, bool):
            rows[label] = "yes" if value else "no"
        else:
            formatter = _HOST_STACK_FORMATTERS.get(name)
            rows[label] = formatter(value) if formatter else str(value)
    # No derived clock row. Current firmware selects 120 MHz before either
    # peripheral or Core 1 starts, so both readings should be 120 MHz; older
    # valid firmware reports 125/120. The plain readings preserve both facts
    # without turning a version-dependent pair into a fault verdict.
    return rows


def _peripherals(counters: object) -> tuple[PeripheralIdentity, ...]:
    """The two role slots, named by role rather than by any backend's wiring.

    An empty slot is still a row: nothing on the keyboard slot is a fact about
    the run, and a reader inferring it from a missing row would be inferring it
    from the same absence that firmware without the block produces.
    """
    ports = getattr(counters, "peripherals", None)
    if not ports:
        return ()
    identities: list[PeripheralIdentity] = []
    for index, port in enumerate(ports):
        role = ROLE_SLOTS[index] if index < len(ROLE_SLOTS) else f"Slot {index}"
        if not getattr(port, "attached", False):
            identities.append(PeripheralIdentity(role))
            continue
        identities.append(
            PeripheralIdentity(
                role,
                vendor_id=f"0x{port.vendor_id:04X}",
                product_id=f"0x{port.product_id:04X}",
                descriptor_hash=port.descriptor_hash or UNKNOWN,
            )
        )
    return tuple(identities)


def redact(message: str, secrets: tuple[str, ...]) -> str:
    """Replace every phrase in ``secrets`` before ``message`` is written down."""
    for secret in secrets:
        if secret:
            message = message.replace(secret, REDACTION)
    return message


def build_diagnostic_zip(
    destination: str | Path,
    snapshot: DiagnosticSnapshot,
    *,
    project: object | None = None,
    include_config: bool = False,
    log_path: str | Path | None = None,
) -> Path:
    """Write the report to ``destination`` and return the path it was written to.

    ``include_config`` is the only way the operator's project - and therefore
    their macro text - enters the archive.
    """
    target = Path(destination)
    project_path: Path | None = None
    if include_config:
        project_path = _project_file(project)

    if log_path is None:
        from duo_input.persistence.locations import log_path as default_log_path

        log_path = default_log_path()
    log = Path(log_path)

    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(DIAGNOSTICS_MEMBER, snapshot.to_json())
        if log.is_file():
            archive.write(log, LOG_MEMBER)
        if project_path is not None:
            archive.write(project_path, PROJECT_MEMBER)
    return target


def _project_file(project: object | None) -> Path:
    path = getattr(project, "path", None) if project is not None else None
    if path is None:
        raise ValueError(
            "the project has never been saved, so there is no file to include"
        )
    location = Path(path)
    if not location.is_file():
        raise ValueError(f"{location} is not a file")
    return location


__all__ = [
    "DIAGNOSTICS_MEMBER",
    "ROLE_SLOTS",
    "LOG_MEMBER",
    "PROJECT_MEMBER",
    "REDACTION",
    "UNKNOWN",
    "DiagnosticSnapshot",
    "PeripheralIdentity",
    "build_diagnostic_zip",
    "redact",
]
