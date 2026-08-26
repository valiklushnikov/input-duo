"""Serial port discovery restricted to the U1 configuration endpoint.

Only the U1 exposes a configuration CDC interface. The U2 endpoint is listed
here solely so that it can be recognised and *excluded*: it must never be
offered as a connectable device.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceIdentity:
    """USB identity a port must present before it is considered a Duo Input unit.

    ``product_string`` is what the device *calls itself*. It is deliberately
    not part of the match: Windows names a composite device's serial function
    from the driver rather than from the strings the device supplies, so a real
    U1 appears as a generic "USB serial device" no matter what its descriptors
    say. Matching on it meant the configurator could not find a board that was
    sitting right there.

    What survives that is the vendor ID, the product ID, and the serial number
    prefix - and the prefix is model identity in its own right, because the
    firmware builds every serial as its own prefix followed by the chip ID.
    """

    vendor_id: int
    product_id: int
    product_string: str
    serial_number_prefix: str


# ---------------------------------------------------------------------------
# PROTOTYPE PLACEHOLDER - NOT A LAWFUL USB VENDOR ALLOCATION.
#
# VID/PID are build-time configuration and no legitimate vendor/product
# allocation exists for this project yet. The values below exist only so the
# whitelist has something concrete to match during development and testing.
# They MUST be replaced with the real allocated identifiers (and this comment
# removed) before any commercial release. Nothing outside this module may
# hard-code these numbers, and tests must assert filtering behaviour rather
# than these specific values.
# ---------------------------------------------------------------------------
U1_IDENTITY = DeviceIdentity(
    vendor_id=0x1209,
    product_id=0xD101,
    product_string="Duo Input U1",
    serial_number_prefix="DIU1-",
)

U2_IDENTITY = DeviceIdentity(
    vendor_id=0x1209,
    product_id=0xD102,
    product_string="Duo Input U2",
    serial_number_prefix="DIU2-",
)


@dataclass(frozen=True)
class PortCandidate:
    """A serial port that passed the U1 whitelist."""

    port_name: str
    serial_number: str


def _identity_matches(port_info, identity: DeviceIdentity) -> bool:
    if not port_info.hasVendorIdentifier() or not port_info.hasProductIdentifier():
        return False
    if port_info.vendorIdentifier() != identity.vendor_id:
        return False
    if port_info.productIdentifier() != identity.product_id:
        return False
    serial_number = (port_info.serialNumber() or "").strip()
    return bool(serial_number) and serial_number.startswith(identity.serial_number_prefix)


def matches_u1(port_info) -> bool:
    """Return whether a ``QSerialPortInfo``-shaped object is a configurable U1."""
    return _identity_matches(port_info, U1_IDENTITY)


def available_port_infos():
    """Return the live ``QSerialPortInfo`` list (imported lazily to stay Qt-free)."""
    from PySide6.QtSerialPort import QSerialPortInfo

    return QSerialPortInfo.availablePorts()


def find_u1_ports(port_infos=None) -> tuple[PortCandidate, ...]:
    """Return every port that presents the U1 identity, in enumeration order."""
    if port_infos is None:
        port_infos = available_port_infos()
    return tuple(
        PortCandidate(info.portName(), (info.serialNumber() or "").strip())
        for info in port_infos
        if matches_u1(info)
    )


__all__ = [
    "DeviceIdentity",
    "PortCandidate",
    "U1_IDENTITY",
    "U2_IDENTITY",
    "available_port_infos",
    "find_u1_ports",
    "matches_u1",
]
