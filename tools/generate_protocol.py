"""Generate shared Duo Input protocol identifiers from protocol/schema.json."""

from __future__ import annotations

import argparse
import difflib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "protocol" / "schema.json"
CPP_PATH = ROOT / "firmware" / "common" / "protocol" / "generated.hpp"
PYTHON_INIT_PATH = ROOT / "configurator" / "src" / "duo_input" / "generated" / "__init__.py"
PYTHON_PATH = ROOT / "configurator" / "src" / "duo_input" / "generated" / "protocol.py"


def _items(schema: dict[str, object], key: str) -> list[tuple[str, int]]:
    values = schema[key]
    assert isinstance(values, dict)
    return [(name, int(value)) for name, value in sorted(values.items())]


def _limit_name(name: str) -> str:
    return name.upper()


def _cpp_member(name: str) -> str:
    """``BAD_SEQUENCE`` -> ``BadSequence``.

    The schema names the error codes the way the wire and the Python enum do;
    the firmware has always spelled them in upper camel case. The spelling is
    a naming convention on each side, not a second definition, so it is applied
    here rather than by hand in a header nobody regenerates.
    """
    return "".join(word.capitalize() for word in name.split("_"))


def _cpp_enum(name: str, underlying_type: str, values: list[tuple[str, int]]) -> list[str]:
    lines = [f"enum class {name} : {underlying_type} {{"]
    lines.extend(f"    {key} = 0x{value:02X}," for key, value in values)
    lines.append("};")
    return lines


def render_cpp(schema: dict[str, object]) -> str:
    limits = _items(schema, "limits")
    protocol_version = schema["protocol_version"]
    schema_version = schema["schema_version"]
    assert isinstance(protocol_version, dict)
    assert isinstance(schema_version, dict)

    lines = [
        "// generated; do not edit",
        "#pragma once",
        "",
        "#include <cstddef>",
        "#include <cstdint>",
        "",
        "namespace duo_input::protocol {",
        "",
        f"inline constexpr std::uint8_t SCHEMA_VERSION_MAJOR = {int(schema_version['major'])};",
        f"inline constexpr std::uint8_t SCHEMA_VERSION_MINOR = {int(schema_version['minor'])};",
        f"inline constexpr std::uint8_t PROTOCOL_VERSION_MAJOR = {int(protocol_version['major'])};",
        f"inline constexpr std::uint8_t PROTOCOL_VERSION_MINOR = {int(protocol_version['minor'])};",
        "",
        "struct ProtocolLimits {",
    ]
    lines.extend(f"    static constexpr std::size_t {_limit_name(name)} = {value};" for name, value in limits)
    lines.extend(["};", ""])
    lines.extend(_cpp_enum("CdcMessageType", "std::uint8_t", _items(schema, "cdc_messages")))
    lines.append("")
    lines.extend(_cpp_enum("SpiMessageType", "std::uint8_t", _items(schema, "spi_messages")))
    lines.append("")
    lines.extend(_cpp_enum("MacroStepType", "std::uint8_t", _items(schema, "macro_steps")))
    lines.append("")
    lines.extend(
        _cpp_enum(
            "CdcError",
            "std::uint8_t",
            [(_cpp_member(name), value) for name, value in _items(schema, "cdc_errors")],
        )
    )
    lines.append("")
    for enum_name, schema_key in (
        ("KeyboardRoute", "keyboard_routes"),
        ("MouseRoute", "mouse_routes"),
        ("TargetMode", "target_modes"),
        ("MouseRouteCommand", "mouse_route_commands"),
        ("TextLayout", "text_layouts"),
        ("TriggerKind", "trigger_kinds"),
        ("BindingMode", "binding_modes"),
        ("ActionKind", "action_kinds"),
    ):
        lines.extend(_cpp_enum(enum_name, "std::uint8_t", _items(schema, schema_key)))
        lines.append("")
    lines.extend(_cpp_enum("Capability", "std::uint32_t", _items(schema, "capabilities")))
    lines.extend(["", "}  // namespace duo_input::protocol", ""])
    return "\n".join(lines)


def _python_enum(name: str, base: str, values: list[tuple[str, int]]) -> list[str]:
    lines = [f"class {name}({base}):"]
    lines.extend(f"    {key} = 0x{value:02X}" for key, value in values)
    return lines


def render_python(schema: dict[str, object]) -> str:
    limits = _items(schema, "limits")
    protocol_version = schema["protocol_version"]
    schema_version = schema["schema_version"]
    assert isinstance(protocol_version, dict)
    assert isinstance(schema_version, dict)

    lines = [
        "# generated; do not edit",
        "from enum import IntEnum, IntFlag",
        "",
        f"SCHEMA_VERSION_MAJOR = {int(schema_version['major'])}",
        f"SCHEMA_VERSION_MINOR = {int(schema_version['minor'])}",
        f"PROTOCOL_VERSION_MAJOR = {int(protocol_version['major'])}",
        f"PROTOCOL_VERSION_MINOR = {int(protocol_version['minor'])}",
        "",
        "class ProtocolLimits:",
    ]
    lines.extend(f"    {_limit_name(name)} = {value}" for name, value in limits)
    lines.append("")
    lines.extend(f"{_limit_name(name)} = ProtocolLimits.{_limit_name(name)}" for name, _ in limits)
    lines.append("")
    lines.extend(_python_enum("CdcMessageType", "IntEnum", _items(schema, "cdc_messages")))
    lines.append("")
    lines.extend(_python_enum("SpiMessageType", "IntEnum", _items(schema, "spi_messages")))
    lines.append("")
    lines.extend(_python_enum("MacroStepType", "IntEnum", _items(schema, "macro_steps")))
    lines.append("")
    lines.extend(_python_enum("ErrorCode", "IntEnum", _items(schema, "cdc_errors")))
    lines.append("")
    for enum_name, schema_key in (
        ("KeyboardRoute", "keyboard_routes"),
        ("MouseRoute", "mouse_routes"),
        ("TargetMode", "target_modes"),
        ("MouseRouteCommand", "mouse_route_commands"),
        ("TextLayout", "text_layouts"),
        ("TriggerKind", "trigger_kinds"),
        ("BindingMode", "binding_modes"),
        ("ActionKind", "action_kinds"),
    ):
        lines.extend(_python_enum(enum_name, "IntEnum", _items(schema, schema_key)))
        lines.append("")
    lines.extend(_python_enum("Capability", "IntFlag", _items(schema, "capabilities")))
    lines.append("")
    return "\n".join(lines)


def _lines(text: str) -> list[str]:
    """Split into lines the way this comparison has to see them.

    ``str.splitlines`` drops the terminator, so CRLF and LF land on the same
    list. That is the point: a checkout's line endings belong to the filesystem
    git wrote it onto, not to the schema. ``--check`` asks whether the
    generated files still describe ``protocol/schema.json``, and on Windows
    every fresh clone holds CRLF while the generator writes LF - a byte
    comparison answers "stale" for a reason that has nothing to do with the
    protocol, and takes ``build_release.ps1`` down with it.
    """
    return text.splitlines()


def stale_report(path: Path, content: str) -> str | None:
    """``None`` when ``path`` already says ``content``, else why it does not.

    The report names the file and shows the differing lines. A release gate
    that exits 1 printing nothing tells its operator only that something,
    somewhere, is wrong.
    """
    if not path.is_file():
        return f"{path}: missing; run tools/generate_protocol.py"

    current = path.read_bytes().decode("utf-8")
    if _lines(current) == _lines(content):
        return None

    diff = difflib.unified_diff(
        _lines(current),
        _lines(content),
        fromfile=f"{path} (checked in)",
        tofile=f"{path} (from protocol/schema.json)",
        lineterm="",
    )
    return f"{path}: out of date\n" + "\n".join(diff)


def write_if_changed(path: Path, content: str) -> bool:
    """Write ``content`` unless ``path`` already says the same thing.

    "The same thing" ignores line endings, so running the generator inside a
    CRLF checkout no longer rewrites three files into LF for no reason.
    """
    if stale_report(path, content) is None:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if generated files are stale")
    args = parser.parse_args(argv)

    schema: dict[str, object] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    outputs = [
        (CPP_PATH, render_cpp(schema)),
        (PYTHON_PATH, render_python(schema)),
        (PYTHON_INIT_PATH, "# generated; do not edit\n"),
    ]

    if args.check:
        reports = [
            report for target, content in outputs if (report := stale_report(target, content)) is not None
        ]
        for report in reports:
            print(report)
        if reports:
            print(
                f"{len(reports)} generated file(s) no longer match protocol/schema.json; "
                "run tools/generate_protocol.py"
            )
            return 1
        return 0

    for target, content in outputs:
        write_if_changed(target, content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
