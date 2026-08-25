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
