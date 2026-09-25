"""What each board tells a computer it is, read from the linked firmware.

Descriptor bytes are assembled by macros, sized by `sizeof` and ordered by an
initialiser list. Every one of those is a place a mistake hides in a way that
reading the source does not reveal, so these assertions run against the bytes
in the built ELF rather than against the file that produced them.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "tools"))
sys.path.insert(0, str(REPOSITORY_ROOT / "configurator" / "src"))

from dump_usb_descriptors import dump  # noqa: E402

# USB class codes, so the assertions read as what they mean.
CLASS_HID = 0x03
CLASS_CDC_CONTROL = 0x02
CLASS_CDC_DATA = 0x0A

HID_SUBCLASS_BOOT = 0x01
HID_PROTOCOL_KEYBOARD = 0x01
HID_PROTOCOL_NONE = 0x00

ENDPOINT_IN = 0x80
TRANSFER_INTERRUPT = 0x03


def _build_dir() -> Path:
    override = os.environ.get("DUO_INPUT_PICO_BUILD")
    return Path(override) if override else REPOSITORY_ROOT / "build" / "pico-release"


def _elf(target: str, name: str) -> Path:
    return _build_dir() / "firmware" / target / f"{name}.elf"


U1_ELF = _elf("u1_main", "duo_u1_main")
U2_ELF = _elf("u2_endpoint", "duo_u2_endpoint")

pytestmark = pytest.mark.skipif(
    not (U1_ELF.is_file() and U2_ELF.is_file()),
    reason="no Pico build; run cmake --build --preset pico-release first",
)


@pytest.fixture(scope="module")
def u1() -> dict:
    return dump(U1_ELF)


@pytest.fixture(scope="module")
def u2() -> dict:
    return dump(U2_ELF)


def _hid(document: dict) -> list[dict]:
    return [
        interface
        for interface in document["configuration"]["interfaces"]
        if interface["bInterfaceClass"] == CLASS_HID
    ]


# ------------------------------------------------------------------ identity


def test_the_firmware_and_the_configurator_agree_on_who_u1_is(u1):
    from duo_input.device.discovery import U1_IDENTITY

    # These numbers are a prototype placeholder, but the firmware and the
    # program that looks for it must at least agree on which placeholder.
    assert u1["device"]["idVendor"] == U1_IDENTITY.vendor_id
    assert u1["device"]["idProduct"] == U1_IDENTITY.product_id


def test_the_firmware_and_the_configurator_agree_on_who_u2_is(u2):
    from duo_input.device.discovery import U2_IDENTITY

    assert u2["device"]["idVendor"] == U2_IDENTITY.vendor_id
    assert u2["device"]["idProduct"] == U2_IDENTITY.product_id


def test_the_two_boards_are_told_apart_by_their_product_id(u1, u2):
    assert u1["device"]["idVendor"] == u2["device"]["idVendor"]
    assert u1["device"]["idProduct"] != u2["device"]["idProduct"]


@pytest.mark.parametrize("board", ("u1", "u2"))
def test_each_board_names_a_manufacturer_product_and_serial(board, u1, u2):
    device = (u1 if board == "u1" else u2)["device"]

    # A device with no serial number cannot be recognised across a reconnect,
    # and the configurator's whitelist needs one.
    assert device["iManufacturer"] != 0
    assert device["iProduct"] != 0
    assert device["iSerialNumber"] != 0


# ---------------------------------------------------------------- interfaces


def test_u1_offers_three_hid_interfaces_and_one_cdc(u1):
    interfaces = u1["configuration"]["interfaces"]
    classes = [interface["bInterfaceClass"] for interface in interfaces]

    assert classes == [CLASS_HID, CLASS_HID, CLASS_HID, CLASS_CDC_CONTROL, CLASS_CDC_DATA]
    assert u1["configuration"]["bNumInterfaces"] == 5


def test_u2_offers_the_same_hid_and_one_cdc_for_address_exchange(u2):
    classes = [interface["bInterfaceClass"] for interface in u2["configuration"]["interfaces"]]
    # U2 has no configuration; its serial port exists only so PC2's program can
    # swap addresses with PC1's. The HID part must stay exactly U1's - see
    # test_both_boards_describe_identical_input_devices.
    assert classes == [CLASS_HID, CLASS_HID, CLASS_HID, CLASS_CDC_CONTROL, CLASS_CDC_DATA]
    assert u2["configuration"]["bNumInterfaces"] == 5


@pytest.mark.parametrize("board", ("u1", "u2"))
def test_the_keyboard_is_a_boot_keyboard(board, u1, u2):
    keyboard = _hid(u1 if board == "u1" else u2)[0]

    # Boot protocol is what makes the keyboard work in a BIOS and a UEFI setup
    # screen, where there is no operating system to load a driver.
    assert keyboard["bInterfaceSubClass"] == HID_SUBCLASS_BOOT
    assert keyboard["bInterfaceProtocol"] == HID_PROTOCOL_KEYBOARD


@pytest.mark.parametrize("board", ("u1", "u2"))
def test_the_mouse_and_consumer_are_report_protocol(board, u1, u2):
    mouse, consumer = _hid(u1 if board == "u1" else u2)[1:3]

    # A boot mouse cannot describe five buttons or a pan wheel, so claiming
    # boot protocol here would be a lie the host could act on.
    for interface in (mouse, consumer):
        assert interface["bInterfaceProtocol"] == HID_PROTOCOL_NONE


# ------------------------------------------------------------ report content


@pytest.mark.parametrize("report", ("keyboard", "mouse", "consumer"))
def test_both_boards_describe_identical_input_devices(report, u1, u2):
    # The two computers must see the same keyboard and the same mouse. If they
    # did not, a macro recorded against one would type something else on the
    # other.
    assert u1["report_descriptors"][report] == u2["report_descriptors"][report]


@pytest.mark.parametrize("board", ("u1", "u2"))
def test_the_mouse_reports_five_buttons(board, u1, u2):
    descriptor = bytes.fromhex((u1 if board == "u1" else u2)["report_descriptors"]["mouse"])

    # Usage Page (Button), Usage Minimum 1, Usage Maximum 5.
    assert bytes((0x05, 0x09)) in descriptor
    assert bytes((0x19, 0x01)) in descriptor
    assert bytes((0x29, 0x05)) in descriptor


@pytest.mark.parametrize("board", ("u1", "u2"))
def test_the_mouse_reports_a_pan_wheel(board, u1, u2):
    descriptor = bytes.fromhex((u1 if board == "u1" else u2)["report_descriptors"]["mouse"])

    # Usage Page (Consumer) followed by AC Pan, which is how horizontal scroll
    # is described.
    assert bytes((0x05, 0x0C)) in descriptor
    assert bytes((0x0A, 0x38, 0x02)) in descriptor


@pytest.mark.parametrize("board", ("u1", "u2"))
def test_no_report_carries_a_report_id(board, u1, u2):
    document = u1 if board == "u1" else u2

    for report in ("keyboard", "mouse", "consumer"):
        descriptor = bytes.fromhex(document["report_descriptors"][report])
        # 0x85 is Report ID. Each interface carries exactly one report, so
        # there is nothing to disambiguate - and a boot keyboard must not have
        # one at all.
        assert 0x85 not in descriptor, report


# ------------------------------------------------------------------ endpoints


@pytest.mark.parametrize("board", ("u1", "u2"))
def test_every_hid_endpoint_is_an_interrupt_in(board, u1, u2):
    document = u1 if board == "u1" else u2
    interrupt = [
        endpoint
        for endpoint in document["configuration"]["endpoints"]
        if endpoint["bmAttributes"] == TRANSFER_INTERRUPT
    ]

    assert len(interrupt) >= 3
    for endpoint in interrupt[:3]:
        assert endpoint["bEndpointAddress"] & ENDPOINT_IN


@pytest.mark.parametrize("board", ("u1", "u2"))
def test_keyboard_and_mouse_are_polled_every_frame(board, u1, u2):
    document = u1 if board == "u1" else u2
    endpoints = document["configuration"]["endpoints"]

    # 1 ms. Anything slower shows up directly in the latency budget.
    assert endpoints[0]["bInterval"] == 1
    assert endpoints[1]["bInterval"] == 1


@pytest.mark.parametrize("board", ("u1", "u2"))
def test_no_endpoint_address_is_used_twice(board, u1, u2):
    document = u1 if board == "u1" else u2
    addresses = [
        endpoint["bEndpointAddress"] for endpoint in document["configuration"]["endpoints"]
    ]

    assert len(addresses) == len(set(addresses))


# ------------------------------------------------------------------- power


@pytest.mark.parametrize("board", ("u1", "u2"))
def test_remote_wakeup_is_not_claimed(board, u1, u2):
    document = u1 if board == "u1" else u2

    # The MVP does not implement it, and claiming a capability the device does
    # not have is how a machine fails to wake.
    assert document["configuration"]["bmAttributes"] & 0x20 == 0


@pytest.mark.parametrize("board", ("u1", "u2"))
def test_the_declared_length_matches_the_bytes_that_are_there(board, u1, u2):
    configuration = (u1 if board == "u1" else u2)["configuration"]

    # A wTotalLength that disagrees with the actual array is the classic
    # descriptor bug: the host reads past the end or stops early.
    assert configuration["wTotalLength"] == configuration["declared_length"]
