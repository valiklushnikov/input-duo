"""Generate shared Duo Input protocol identifiers from protocol/schema.json."""

from __future__ import annotations

import argparse
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
    lines.extend(_cpp_enum("Capability", "std::uint16_t", _items(schema, "capabilities")))
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
    lines.extend(_python_enum("Capability", "IntFlag", _items(schema, "capabilities")))
    lines.append("")
    return "\n".join(lines)


def _write_if_changed(path: Path, content: str, check: bool) -> bool:
    encoded = content.encode("utf-8")
    if path.is_file() and path.read_bytes() == encoded:
        return False
    if check:
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if generated files are stale")
    args = parser.parse_args(argv)

    schema: dict[str, object] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    stale = _write_if_changed(CPP_PATH, render_cpp(schema), args.check)
    stale |= _write_if_changed(PYTHON_PATH, render_python(schema), args.check)
    stale |= _write_if_changed(PYTHON_INIT_PATH, "# generated; do not edit\n", args.check)
    return 1 if args.check and stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
