"""The wire documentation is a deliverable, and deliverables need guards.

``docs/protocol/compatibility.md`` is the only description of the
``GET_DIAGNOSTICS`` reply anybody outside this repository reads, and the host
block behind the backend block has now been appended to five times. Every one
of those rounds updated the prose by hand, and nothing checked it: a field
added to the firmware and forgotten in the document, or a document listing the
fields in an order the firmware does not serialise, would both have passed
every test in this tree.

The two assertions below close that. Both expectations are DERIVED from
``ConfigService::write_host_observation`` rather than written down here, so the
guard cannot drift with the thing it guards, and it fails on a reordering as
well as on an omission - which matters, because the block is append-only and a
reordering is the one mistake that silently breaks every already-shipped
configurator.

The comparison is over ``(name, width)`` PAIRS, not over a sum of widths. The
earlier version of this guard compared the total of the documented widths
against the declared block size, which two compensating width edits - one
field widened, another narrowed - passed while leaving every offset behind
them wrong in the only text a third-party reader parses the reply from.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPATIBILITY = ROOT / "docs" / "protocol" / "compatibility.md"
CONFIG_SERVICE = ROOT / "firmware" / "u1_main" / "config_service.cpp"
CONFIG_SERVICE_HEADER = ROOT / "firmware" / "u1_main" / "config_service.hpp"


def _serialised_host_fields() -> list[tuple[str, str]]:
    """Ordered ``(name, width)`` pairs emitted by the firmware serializer."""
    body = CONFIG_SERVICE.read_text(encoding="utf-8")
    start = body.index("std::size_t ConfigService::write_host_observation(")
    end = body.index("\n}", start)
    listing = body[start:end]
    fields: list[tuple[str, str]] = []
    pattern = re.compile(
        r"out\[at\+\+\]\s*=\s*host_observation_\."
        r"(?P<byte>[A-Za-z_][A-Za-z0-9_]*)"
        r"|put_u(?P<bits>16|32)\(out \+ at,\s*host_observation_\."
        r"(?P<word>[A-Za-z_][A-Za-z0-9_]*)\)"
    )
    for match in pattern.finditer(listing):
        if match.group("byte") is not None:
            fields.append((match.group("byte"), "u8"))
        else:
            fields.append((match.group("word"), f"u{match.group('bits')}"))
    return fields


def _documented_host_fields() -> list[tuple[str, str]]:
    """Ordered ``(name, width)`` pairs in the compatibility document."""
    text = COMPATIBILITY.read_text(encoding="utf-8")
    start = text.index("- the **host block**")
    end = text.index("\n\n", start)
    listing = text[start:end]
    fields = re.findall(
        r"([A-Za-z_][A-Za-z0-9_]*):(u(?:8|16|32))\b", listing
    )
    # The leading length byte is the block's own framing, not one of the
    # observation's fields.
    assert fields and fields[0] == ("field_bytes", "u8"), fields[:1]
    return fields[1:]


class HostBlockDocumentationTest(unittest.TestCase):
    def test_the_document_lists_every_serialised_host_field_in_wire_order(self):
        self.assertEqual(_documented_host_fields(), _serialised_host_fields())

    def test_the_declared_block_size_matches_the_widths_the_firmware_writes(self):
        """``kHostObservationBytes`` has to equal what the serializer emits.

        Derived from the serializer rather than from the document on purpose.
        The assertion above already ties the document to the serializer field
        for field, INCLUDING each field's width, so comparing the document to
        the constant here as well would only re-check what it checked - and the
        version of this guard that summed the documented widths could be passed
        by two compensating width edits that cancelled out. This one instead
        catches the other mistake: a field appended to the serializer and to
        the document while the length constant in front of them is left behind,
        which would make every reply declare fewer bytes than it carries.
        """
        widths = {"u8": 1, "u16": 2, "u32": 4}
        serialised = sum(widths[width] for _, width in _serialised_host_fields())

        header = CONFIG_SERVICE_HEADER.read_text(encoding="utf-8")
        declared = header[
            header.index("inline constexpr std::size_t kHostObservationBytes") :
        ]
        declared = declared[declared.index("=") + 1 : declared.index(";")]
        expected = sum(int(part) for part in re.findall(r"\d+", declared))

        self.assertEqual(serialised, expected)


if __name__ == "__main__":
    unittest.main()
