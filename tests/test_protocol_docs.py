"""The wire documentation is a deliverable, and deliverables need guards.

``docs/protocol/compatibility.md`` is the only description of the
``GET_DIAGNOSTICS`` reply anybody outside this repository reads, and the host
block behind the backend block has now been appended to four times. Every one
of those rounds updated the prose by hand, and nothing checked it: a field
added to the firmware and forgotten in the document, or a document listing the
fields in an order the firmware does not serialise, would both have passed
every test in this tree.

The two assertions below close that. The expected list is DERIVED from
``ConfigService::write_host_observation`` rather than written down here, so the
guard cannot drift with the thing it guards, and it fails on a reordering as
well as on an omission - which matters, because the block is append-only and a
reordering is the one mistake that silently breaks every already-shipped
configurator.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPATIBILITY = ROOT / "docs" / "protocol" / "compatibility.md"
CONFIG_SERVICE = ROOT / "firmware" / "u1_main" / "config_service.cpp"
CONFIG_SERVICE_HEADER = ROOT / "firmware" / "u1_main" / "config_service.hpp"


def _serialised_host_fields() -> list[str]:
    """The host block's fields in the order the firmware puts them on the wire."""
    body = CONFIG_SERVICE.read_text(encoding="utf-8")
    start = body.index("std::size_t ConfigService::write_host_observation(")
    end = body.index("\n}", start)
    return re.findall(r"host_observation_\.([A-Za-z_][A-Za-z0-9_]*)", body[start:end])


def _documented_host_fields() -> list[str]:
    """The host block's fields in the order the compatibility document lists them."""
    text = COMPATIBILITY.read_text(encoding="utf-8")
    start = text.index("- the **host block**")
    end = text.index("\n\n", start)
    listing = text[start:end]
    names = re.findall(r"([A-Za-z_][A-Za-z0-9_]*):u(?:8|16|32)\b", listing)
    # The leading length byte is the block's own framing, not one of the
    # observation's fields.
    assert names and names[0] == "field_bytes", names[:1]
    return names[1:]


class HostBlockDocumentationTest(unittest.TestCase):
    def test_the_document_lists_every_serialised_host_field_in_wire_order(self):
        self.assertEqual(_documented_host_fields(), _serialised_host_fields())

    def test_the_documented_field_widths_add_up_to_the_declared_block_size(self):
        """The document's own byte widths have to equal kHostObservationBytes.

        A field documented as a ``u16`` that the firmware writes as a ``u32``
        would leave every offset behind it wrong in the one text a third-party
        reader parses the reply from.
        """
        text = COMPATIBILITY.read_text(encoding="utf-8")
        start = text.index("- the **host block**")
        listing = text[start : text.index("\n\n", start)]
        widths = {"u8": 1, "u16": 2, "u32": 4}
        documented = [
            widths[width]
            for name, width in re.findall(
                r"([A-Za-z_][A-Za-z0-9_]*):(u8|u16|u32)\b", listing
            )
            if name != "field_bytes"
        ]

        header = CONFIG_SERVICE_HEADER.read_text(encoding="utf-8")
        declared = header[
            header.index("inline constexpr std::size_t kHostObservationBytes") :
        ]
        declared = declared[declared.index("=") + 1 : declared.index(";")]
        expected = sum(int(part) for part in re.findall(r"\d+", declared))

        self.assertEqual(sum(documented), expected)


if __name__ == "__main__":
    unittest.main()
