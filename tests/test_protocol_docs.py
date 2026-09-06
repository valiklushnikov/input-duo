"""The wire documentation is a deliverable, and deliverables need guards.

``docs/protocol/compatibility.md`` is the only description of the
``GET_DIAGNOSTICS`` reply anybody outside this repository reads, and the host
block behind the backend block has now been appended to five times. Every one
of those rounds updated the prose by hand, and nothing checked it: a field
added to the firmware and forgotten in the document, or a document listing the
fields in an order the firmware does not serialise, would both have passed
every test in this tree.

``HostBlockDocumentationTest`` closes that. Both of its expectations are
DERIVED from ``ConfigService::write_host_observation`` rather than written down
here, so the guard cannot drift with the thing it guards, and it fails on a
reordering as well as on an omission - which matters, because the block is
append-only and a reordering is the one mistake that silently breaks every
already-shipped configurator.

``EndpointTransferDocumentationTest`` does the same for the part of the
document a person reads at a bench: the bit list comes from the firmware
header, every rendered spelling from the configurator's own decoder, and the
delta arithmetic from the constant the exported report is built on.

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


BACKEND_HEADER = (
    ROOT / "firmware" / "u1_main" / "pio_usb" / "backend.hpp"
)


def _markdown_table(header: str) -> list[list[str]]:
    """The body rows of the one table whose header line starts with ``header``.

    Cells are stripped of surrounding whitespace and of the backticks the
    document sets identifiers in, so a guard compares the identifier and not
    the markup around it.
    """
    text = COMPATIBILITY.read_text(encoding="utf-8")
    start = text.index(header)
    block = text[start : text.index("\n\n", start)]
    rows = []
    for line in block.splitlines()[2:]:  # skip the header and its separator
        line = line.strip()
        if not line.startswith("|"):
            continue
        rows.append([cell.strip().strip("`") for cell in line.strip("|").split("|")])
    return rows


def _firmware_endpoint_transfer_bits() -> list[tuple[int, str]]:
    """``(bit, constant)`` for every ``kEpXfer*`` the firmware defines."""
    body = BACKEND_HEADER.read_text(encoding="utf-8")
    found = re.findall(
        r"inline constexpr std::uint8_t (kEpXfer[A-Za-z0-9_]*) = 1u << (\d+);", body
    )
    return sorted(((int(bit), name) for name, bit in found), key=lambda pair: pair[0])


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


class EndpointTransferDocumentationTest(unittest.TestCase):
    """The prose an operator reads at a bench is a deliverable, so it is derived.

    Round 5's review made this concrete rather than theoretical: it inverted
    "bit 3 means DATA1 (clear means DATA0)" and changed the hub's fixed share of
    the completion delta from five to nine, in the one document a person holds
    a board next to, and every test in this repository passed. The three
    assertions below take the bit list from the firmware header, the rendered
    spelling of every bit from the configurator's own decoder, and the delta
    arithmetic from the constant the exported report is built on, so none of
    those three edits can pass again.
    """

    def test_the_document_lists_the_endpoint_transfer_bits_the_firmware_defines(self):
        documented = [(int(bit), name) for bit, name in _markdown_table(
            "| bit | firmware constant |"
        )]
        self.assertEqual(documented, _firmware_endpoint_transfer_bits())

    def test_the_configurator_bit_constants_are_the_firmwares(self):
        """The parser keeps its own copy of these bits; it must be the same copy."""
        from duo_input.device import transactions

        for bit, name in _firmware_endpoint_transfer_bits():
            # kEpXferHasTransfer -> EP_XFER_HAS_TRANSFER
            tail = re.sub(r"(?<!^)(?=[A-Z])", "_", name[len("kEpXfer"):]).upper()
            self.assertEqual(
                getattr(transactions, f"EP_XFER_{tail}"), 1 << bit, name
            )

    def test_every_documented_byte_reads_as_what_the_report_prints(self):
        from duo_input.persistence.diagnostic_export import endpoint_transfer_slot

        rows = _markdown_table("| byte | reads as |")
        self.assertTrue(rows)
        for byte, reads_as in rows:
            self.assertEqual(endpoint_transfer_slot(int(byte, 16)), reads_as, byte)

    def test_every_endpoint_transfer_bit_is_exercised_by_a_documented_byte(self):
        """A bit no example sets is a bit whose spelling nothing checks."""
        covered = 0
        for byte, _ in _markdown_table("| byte | reads as |"):
            covered |= int(byte, 16)
        for bit, name in _firmware_endpoint_transfer_bits():
            self.assertTrue(covered & (1 << bit), name)

    def test_the_documented_hub_cost_adds_up_to_the_constant_the_report_uses(self):
        """The fixed part of the delta, itemised, summed and tied to one constant.

        The review's second mutation changed this number in the prose and
        nothing failed. It is now three numbers in a table: the two stages that
        make it up and their total, checked against each other and against the
        constant the exported row's label is built from.
        """
        from duo_input.persistence.diagnostic_export import (
            HUB_COMPLETIONS_AFTER_ATTACH as HUB,
            completions_since_attach_label,
        )

        rows = _markdown_table("| hub work after an accepted port attach |")
        stages = [int(row[1]) for row in rows]
        self.assertEqual(stages[-1], HUB)
        self.assertEqual(sum(stages[:-1]), stages[-1])
        self.assertIn(str(HUB), completions_since_attach_label())

    def test_the_recovery_counter_names_submitted_events_not_health_or_cause(self):
        """The operator text must not overclaim what this counter knows.

        Removing ``submitted``, calling zero healthy, or treating a rise as a
        cause would make the same number mean materially different things at a
        bench. The label and durable protocol text therefore have to state the
        same bounded observation, not merely share the field name.
        """
        from duo_input.persistence.diagnostic_export import host_stack_labels

        label = host_stack_labels()["enum_stall_recoveries"]
        self.assertEqual(
            label,
            "Address-0 recovery attach events submitted (0 is not a health verdict)",
        )

        text = COMPATIBILITY.read_text(encoding="utf-8")
        start = text.index("`enum_stall_recoveries` counts")
        section = " ".join(text[start : text.index("`clk_hz_now`", start)].split())
        self.assertIn("synthetic duplicate-attach recovery requests submitted", section)
        self.assertIn("zero is not a health verdict", section)
        self.assertIn("at most two", section)
        self.assertIn("not its cause", section)
        self.assertIn("tuh_deinit", section)
        self.assertIn("process_removing_device", section)
        self.assertIn("ep_max_failed_count == 0", section)
        self.assertIn("real NAK", section)
        self.assertIn("probably not the root cause", section)
        self.assertIn("logic analyser", section)
        self.assertIn("upstream example", section)

    def test_the_bench_decision_table_agrees_with_the_decoder_and_the_hub_cost(self):
        from duo_input.persistence.diagnostic_export import (
            HUB_COMPLETIONS_AFTER_ATTACH as HUB,
            endpoint_transfer_slot,
        )

        rows = _markdown_table("| Slot-2 byte |")
        listed = [row for row in rows if row[0].startswith("0x")]

        # The last row is the catch-all for every state/delta pair the rows
        # above do not list, and its instruction is the deliverable: a person
        # holding a board must be told to stop rather than left to decide which
        # half of an inconsistent pair to believe. Asserted on the wording,
        # because a catch-all row that merely EXISTS is one a rewrite can empty
        # out without failing anything - which is exactly what happened to the
        # first version of this assertion.
        self.assertEqual(len(rows) - len(listed), 1, "the catch-all row is missing")
        catch_all = rows[-1]
        self.assertEqual(catch_all[0], "anything else")
        self.assertIn("Record both rows verbatim and stop", catch_all[3])

        self.assertEqual(
            [endpoint_transfer_slot(int(row[0], 16)) for row in listed],
            [row[1] for row in listed],
        )
        self.assertEqual(
            [row[2] for row in listed],
            [
                str(HUB),
                str(HUB),
                str(HUB + 1),
                str(HUB + 2),
                f"{HUB + 3} or more",
            ],
        )


if __name__ == "__main__":
    unittest.main()
