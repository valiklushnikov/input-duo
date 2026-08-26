"""Port whitelisting: only the U1 may ever be offered as connectable."""

from __future__ import annotations

from dataclasses import dataclass, replace

from duo_input.device.discovery import (
    U1_IDENTITY,
    U2_IDENTITY,
    PortCandidate,
    find_u1_ports,
    matches_u1,
)


@dataclass
class _FakePortInfo:
    """Duck-typed stand-in for QSerialPortInfo."""

    port: str
    vendor: int | None
    product: int | None
    product_string: str
    serial: str

    def portName(self) -> str:  # noqa: N802 - mirrors the Qt API
        return self.port

    def hasVendorIdentifier(self) -> bool:  # noqa: N802
        return self.vendor is not None

    def hasProductIdentifier(self) -> bool:  # noqa: N802
        return self.product is not None

    def vendorIdentifier(self) -> int:  # noqa: N802
        return self.vendor or 0

    def productIdentifier(self) -> int:  # noqa: N802
        return self.product or 0

    def description(self) -> str:
        return self.product_string

    def serialNumber(self) -> str:  # noqa: N802
        return self.serial


def _u1(port: str = "COM7", serial: str | None = None) -> _FakePortInfo:
    return _FakePortInfo(
        port,
        U1_IDENTITY.vendor_id,
        U1_IDENTITY.product_id,
        U1_IDENTITY.product_string,
        serial if serial is not None else U1_IDENTITY.serial_number_prefix + "0001",
    )


def _u2(port: str = "COM8") -> _FakePortInfo:
    return _FakePortInfo(
        port,
        U2_IDENTITY.vendor_id,
        U2_IDENTITY.product_id,
        U2_IDENTITY.product_string,
        U2_IDENTITY.serial_number_prefix + "0001",
    )


def _unrelated(port: str = "COM3") -> _FakePortInfo:
    return _FakePortInfo(port, 0x0403, 0x6001, "USB Serial Port", "A50285BI")


def test_only_the_u1_identity_is_offered_as_connectable():
    ports = find_u1_ports([_unrelated(), _u2(), _u1()])

    assert ports == (PortCandidate("COM7", U1_IDENTITY.serial_number_prefix + "0001"),)


def test_u2_is_never_surfaced_even_when_otherwise_well_formed():
    assert not matches_u1(_u2())
    assert find_u1_ports([_u2("COM8"), _u2("COM9")]) == ()


def test_unrelated_ports_are_rejected():
    assert not matches_u1(_unrelated())


def test_missing_or_blank_identity_fields_are_rejected():
    assert not matches_u1(replace(_u1(), vendor=None))
    assert not matches_u1(replace(_u1(), product=None))
    assert not matches_u1(replace(_u1(), serial=""))
    assert not matches_u1(replace(_u1(), serial="   "))
    assert not matches_u1(replace(_u1(), serial="XX-0001"))


def test_the_name_windows_gives_the_port_is_not_part_of_the_identity():
    # This used to be a rejection, and hardware showed it was the wrong rule:
    # Windows names a composite CDC function from the driver, so a genuine U1
    # never presents its own product string here. See
    # test_a_real_u1_is_found_although_windows_names_the_port_itself.
    assert matches_u1(replace(_u1(), product_string=""))
    assert matches_u1(replace(_u1(), product_string="Some Other Device"))


def test_matching_u1_is_accepted_for_any_serial_with_the_expected_prefix():
    assert matches_u1(_u1(serial=U1_IDENTITY.serial_number_prefix + "ZZZZ"))


def test_u1_and_u2_identities_are_distinct_products():
    assert U1_IDENTITY.product_id != U2_IDENTITY.product_id
    assert U1_IDENTITY.product_string != U2_IDENTITY.product_string


# ------------------------------------------------------- what Windows reports


def test_a_real_u1_is_found_although_windows_names_the_port_itself():
    """The identity a composite CDC function actually presents on Windows.

    Windows names a composite device's serial function from the driver, not
    from the strings the device supplies: a real U1 shows up as "Устройство с
    последовательным интерфейсом USB". Requiring our product string in
    description() meant the configurator never found a device that was sitting
    right there - confirmed against hardware, VID/PID 1209/D101, serial
    DIU1-E663B03597570C2C.
    """
    windows_port = _FakePortInfo(
        "COM18",
        U1_IDENTITY.vendor_id,
        U1_IDENTITY.product_id,
        "Устройство с последовательным интерфейсом USB",
        "DIU1-E663B03597570C2C",
    )

    assert matches_u1(windows_port) is True
    assert find_u1_ports([windows_port]) == (PortCandidate("COM18", "DIU1-E663B03597570C2C"),)


def test_a_u2_is_still_refused_whatever_the_port_is_called():
    windows_port = _FakePortInfo(
        "COM19",
        U2_IDENTITY.vendor_id,
        U2_IDENTITY.product_id,
        "Устройство с последовательным интерфейсом USB",
        "DIU2-E663B03597570C2C",
    )

    assert matches_u1(windows_port) is False


def test_a_serial_from_the_wrong_model_is_refused_even_on_the_right_ids():
    # The serial prefix is the model identity that survives Windows renaming
    # the port, so it has to carry the check that description() no longer can.
    impostor = replace(_u1(), serial="DIU2-0001")

    assert matches_u1(impostor) is False


def test_a_device_with_no_serial_number_is_refused():
    assert matches_u1(replace(_u1(), serial="")) is False
