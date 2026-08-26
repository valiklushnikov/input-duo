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
from duo_input.generated.protocol import Capability

#: Names inside the archive. They are identifiers, not localised labels.
DIAGNOSTICS_MEMBER = "diagnostics.json"
LOG_MEMBER = "application.log"
PROJECT_MEMBER = "project.duoinput.json"

#: Shown wherever protocol v1 genuinely carries no value.
UNKNOWN = "unknown"

#: What a redacted phrase is replaced with in the log.
REDACTION = "***"


@dataclass(frozen=True)
class PeripheralIdentity:
    """One USB device the U1 has enumerated, as it identifies itself."""

    role: str
    vendor_id: str = UNKNOWN
    product_id: str = UNKNOWN
    descriptor_hash: str = UNKNOWN


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
    ch375_state: str = UNKNOWN
    peripherals: tuple[PeripheralIdentity, ...] = ()
    advertised_capabilities: tuple[str, ...] = ()
    device_generation: str = UNKNOWN
    device_hash: str = UNKNOWN
    spi_crc_errors: str = UNKNOWN
    spi_timeouts: str = UNKNOWN
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
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2, sort_keys=True)


def _counter(counters: object, name: str) -> int | str:
    value = getattr(counters, name, None)
    return UNKNOWN if value is None else int(value)


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
    "LOG_MEMBER",
    "PROJECT_MEMBER",
    "REDACTION",
    "UNKNOWN",
    "DiagnosticSnapshot",
    "PeripheralIdentity",
    "build_diagnostic_zip",
    "redact",
]
