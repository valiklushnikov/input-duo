import json
from pathlib import Path

from duo_input.generated import protocol
from duo_input.domain import models


def test_generated_values_match_schema():
    schema = json.loads(Path("protocol/schema.json").read_text("utf-8"))

    assert protocol.CDC_MAX_PAYLOAD == schema["limits"]["cdc_max_payload"] == 1024
    assert protocol.SPI_FRAME_SIZE == 64
    assert protocol.CdcMessageType.HELLO.value == schema["cdc_messages"]["HELLO"]


def test_every_binary_config_enum_matches_schema_and_is_reexported_by_domain_models():
    schema = json.loads(Path("protocol/schema.json").read_text("utf-8"))
    enum_sections = {
        "KeyboardRoute": "keyboard_routes",
        "MouseRoute": "mouse_routes",
        "TargetMode": "target_modes",
        "MouseRouteCommand": "mouse_route_commands",
        "TextLayout": "text_layouts",
        "TriggerKind": "trigger_kinds",
        "BindingMode": "binding_modes",
        "ActionKind": "action_kinds",
    }

    for enum_name, schema_section in enum_sections.items():
        generated_enum = getattr(protocol, enum_name)
        assert {name: member.value for name, member in generated_enum.__members__.items()} == schema[
            schema_section
        ]
        assert getattr(models, enum_name) is generated_enum


def _cpp_member(name: str) -> str:
    return "".join(word.capitalize() for word in name.split("_"))


def test_cdc_error_codes_are_generated_from_the_schema():
    schema = json.loads(Path("protocol/schema.json").read_text("utf-8"))

    assert {
        name: member.value for name, member in protocol.ErrorCode.__members__.items()
    } == schema["cdc_errors"]


def test_the_firmware_error_enum_comes_from_the_same_schema():
    schema = json.loads(Path("protocol/schema.json").read_text("utf-8"))
    header = Path("firmware/common/protocol/generated.hpp").read_text("utf-8")

    assert "enum class CdcError : std::uint8_t {" in header
    for name, value in schema["cdc_errors"].items():
        assert f"    {_cpp_member(name)} = 0x{value:02X}," in header


def test_no_hand_written_copy_of_the_error_codes_survives():
    """The identifiers exist in the schema and its generated outputs, nowhere else."""
    hand_written = (
        Path("configurator/src/duo_input/device/emulator.py"),
        Path("configurator/src/duo_input/device/transactions.py"),
        Path("firmware/u1_main/config_service.hpp"),
    )

    for path in hand_written:
        source = path.read_text("utf-8")
        assert "PHYSICAL_CONFIRMATION_REQUIRED = 11" not in source
        assert "PhysicalConfirmationRequired = 11" not in source


def test_the_device_layer_takes_error_codes_from_the_generated_protocol():
    from duo_input import device
    from duo_input.device import emulator, service, transactions

    assert transactions.ErrorCode is protocol.ErrorCode
    assert emulator.ErrorCode is protocol.ErrorCode
    assert service.ErrorCode is protocol.ErrorCode
    assert device.ErrorCode is protocol.ErrorCode

    # Production framing must not reach into the test-support emulator for a
    # protocol identifier: that is how host and firmware drift apart silently.
    source = Path("configurator/src/duo_input/device/transactions.py").read_text("utf-8")
    assert "from .emulator import" not in source


def test_config_flags_are_generated_from_the_schema():
    schema = json.loads(Path("protocol/schema.json").read_text("utf-8"))

    assert protocol.ConfigFlag.SYNCHRONISED_CONTROL.value == schema["config_flags"]["SYNCHRONISED_CONTROL"]
    # A set of bits, not a list of values: two flags must be combinable, and
    # the reader below tests membership rather than equality.
    assert isinstance(protocol.ConfigFlag.SYNCHRONISED_CONTROL, protocol.ConfigFlag)
    assert protocol.SCHEMA_VERSION_MINOR == schema["schema_version"]["minor"]
