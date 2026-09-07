"""What the patched PIO USB toolchain must promise, verbatim.

Four defects in the pinned TinyUSB and Pico-PIO-USB revisions stop this
hardware from enumerating and reporting at all; they are recorded in
``docs/superpowers/records/2026-09-03-pio-usb-hub-v1-record.md``. Rather than
hand-editing the clones - which
``cmake/pio_usb_toolchain_lock.cmake`` rightly refuses to build against - the
fixes live here as patches under version control, and the bootstrap applies
them and commits them into the clone.

Pico-PIO-USB first carries the exact upstream ACK-turnaround backport, then the
host guards, and finally a numbered patch that is not a fix at all: the
control-transfer packet trace, a diagnostic instrument that is meant to be
deleted once the question it was built to answer has been answered. It is a
separate file so deleting it is deleting a file, and the last part of this
module is what keeps it honest - above all that it records *after* the
handshake for each packet has been sent, never before.

That commit's SHA is what the lock pins, so it has to be reproducible on any
machine. A git commit hashes its tree, its parent and its author and committer
identity, name, email and date alike - so every one of those is fixed, and the
clone is normalised to LF first, because a checkout under ``core.autocrlf=true``
would otherwise store different bytes and produce a different SHA.

Like its neighbour ``test_pio_usb_toolchain_lock.py`` this file runs no
configure and no git: it reads the files the way a human reviewing a diff
would.
"""

from __future__ import annotations

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

#: Upstream, before any patch of ours. These stay visible so the provenance of
#: what we build is never in doubt.
TINYUSB_BASE_REVISION = "86ad6e56c1700e85f1c5678607a762cfe3aa2f47"
PICO_PIO_USB_BASE_REVISION = "3c1eec341a5232640e4c00628b889b641af34b28"

#: The revisions the bootstrap produces by applying ``patches/`` on top of the
#: bases above, with the fixed identity and date below. These are what the
#: build is verified against.
TINYUSB_PATCHED_REVISION = "507766faf14f38a6752401fb4f324cc00cd145dd"
PICO_PIO_USB_PATCHED_REVISION = "ce67882de7c6e75734087e3181caeb2511f48c46"

#: Fixed so the commit SHA is reproducible.
PATCH_COMMIT_IDENTITY = "toolchain@duo-input.invalid"
PATCH_COMMIT_DATE = "1788691431 +0000"
PATCH_COMMIT_MESSAGE = "Duo Input host fixes"

PATCHED_DEPENDENCIES = ("tinyusb", "pico-pio-usb")


def _lock_text() -> str:
    return (REPOSITORY_ROOT / "cmake" / "pio_usb_toolchain_lock.cmake").read_text(
        encoding="utf-8"
    )


def _bootstrap_text() -> str:
    return (REPOSITORY_ROOT / "tools" / "bootstrap_pio_usb_toolchain.ps1").read_text(
        encoding="utf-8"
    )


def _patch_paths() -> list[Path]:
    return sorted((REPOSITORY_ROOT / "patches").rglob("*.patch"))


# ------------------------------------------------------------------ patches


def test_every_patched_dependency_has_at_least_one_patch_in_sorted_order():
    """One patch per dependency was the rule until the control trace.

    The trace instrumentation is diagnostic and temporary: it exists to tell
    "the device sent nothing" from "the host stack lost what the device sent",
    and it is meant to be deleted once that question is answered. Folding it
    into ``0001-duo-input-host-fixes.patch`` would bury four measured, keeping
    fixes and one throwaway instrument in the same file, and removing the
    instrument later would then mean editing the file that carries the fixes.
    A separate numbered patch is deleted by deleting a file.

    What replaces the old invariant is the property that actually mattered:
    the patches are numbered, applied in sorted order, and every one of them
    is accounted for.
    """
    for name in PATCHED_DEPENDENCIES:
        patches = sorted((REPOSITORY_ROOT / "patches" / name).glob("*.patch"))
        assert patches, f"{name}: no patches found"
        for index, patch in enumerate(patches, start=1):
            assert patch.name.startswith(f"{index:04d}-"), (
                f"{name}: patches must be numbered without gaps so their apply "
                f"order is unambiguous; found {patch.name} at position {index}"
            )


def test_the_bootstrap_applies_the_patches_in_sorted_order():
    """More than one patch means the order they apply in is load-bearing."""
    bootstrap = _bootstrap_text()
    assert "Sort-Object Name" in bootstrap, (
        "the bootstrap does not sort the patch files, so a second patch would "
        "apply in whatever order the filesystem returned"
    )


def test_patches_are_tracked_text_and_not_empty():
    for patch in _patch_paths():
        text = patch.read_text(encoding="utf-8")
        assert text.startswith("diff --git "), f"{patch} is not a git patch"
        assert "\r\n" not in text, (
            f"{patch} contains CRLF; it would not apply to an LF-normalised clone"
        )


def test_each_pico_pio_usb_patch_stays_inside_its_reviewed_surface():
    expected = {
        "0001-upstream-ep0-ack-turnaround.patch": {
            "src/pio_usb.c",
            "src/pio_usb_ll.h",
        },
        "0002-duo-input-host-fixes.patch": {"src/pio_usb_host.c"},
        "0003-duo-input-control-trace.patch": {"src/pio_usb_host.c"},
    }
    for patch in sorted((REPOSITORY_ROOT / "patches" / "pico-pio-usb").glob("*.patch")):
        text = patch.read_text(encoding="utf-8")
        touched = {
            line.split(" b/")[1].strip()
            for line in text.splitlines()
            if line.startswith("diff --git ")
        }
        assert patch.name in expected, f"unreviewed Pico-PIO-USB patch: {patch.name}"
        assert touched == expected[patch.name], (
            f"{patch.name} escaped its reviewed surface: {sorted(touched)}"
        )


def test_the_tinyusb_patch_touches_only_the_three_host_files():
    text = (
        REPOSITORY_ROOT / "patches" / "tinyusb" / "0001-duo-input-host-fixes.patch"
    ).read_text(encoding="utf-8")
    touched = {line.split(" b/")[1].strip() for line in text.splitlines() if line.startswith("diff --git ")}
    assert touched == {
        "src/class/hid/hid_host.c",
        "src/host/hub.c",
        "src/host/usbh.c",
    }, f"unexpected files in the TinyUSB patch: {sorted(touched)}"


# --------------------------------------------------------------------- lock


def test_the_lock_pins_the_patched_revisions():
    lock = _lock_text()
    assert TINYUSB_PATCHED_REVISION in lock
    assert PICO_PIO_USB_PATCHED_REVISION in lock


def test_the_lock_still_records_the_upstream_bases():
    lock = _lock_text()
    assert TINYUSB_BASE_REVISION in lock
    assert PICO_PIO_USB_BASE_REVISION in lock


def test_patched_revisions_differ_from_their_bases():
    assert TINYUSB_PATCHED_REVISION != TINYUSB_BASE_REVISION
    assert PICO_PIO_USB_PATCHED_REVISION != PICO_PIO_USB_BASE_REVISION


def test_patched_revisions_are_full_sha1s():
    for revision in (TINYUSB_PATCHED_REVISION, PICO_PIO_USB_PATCHED_REVISION):
        assert len(revision) == 40
        assert all(character in "0123456789abcdef" for character in revision)


def test_the_lock_still_refuses_a_dirty_clone():
    """The guard that caught hand-edited clones must survive this change."""
    lock = _lock_text()
    assert "git status --porcelain" in lock
    assert "FATAL_ERROR" in lock


# ---------------------------------------------------------------- bootstrap


def test_the_bootstrap_applies_the_tracked_patches():
    bootstrap = _bootstrap_text()
    assert "patches" in bootstrap
    assert "git apply" in bootstrap or "Invoke-GitApply" in bootstrap


def test_the_bootstrap_fixes_everything_the_commit_sha_depends_on():
    bootstrap = _bootstrap_text()
    for required in (
        "GIT_AUTHOR_DATE",
        "GIT_COMMITTER_DATE",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_EMAIL",
        PATCH_COMMIT_DATE,
        PATCH_COMMIT_IDENTITY,
        PATCH_COMMIT_MESSAGE,
    ):
        assert required in bootstrap, f"bootstrap does not fix {required}"


def test_the_bootstrap_normalises_line_endings_before_patching():
    bootstrap = _bootstrap_text()
    assert "core.autocrlf" in bootstrap, (
        "without disabling autocrlf the clone stores CRLF on Windows and the "
        "patched commit gets a different SHA than on Linux"
    )


def test_the_bootstrap_verifies_the_patched_revision_it_produced():
    bootstrap = _bootstrap_text()
    assert TINYUSB_PATCHED_REVISION in bootstrap
    assert PICO_PIO_USB_PATCHED_REVISION in bootstrap


def test_patches_are_exempt_from_line_ending_conversion():
    """A patch checked out with CRLF will not apply to an LF-normalised clone.

    Git converts line endings on checkout wherever ``core.autocrlf`` says so,
    which on Windows is the default. Without an attribute pinning these files,
    a fresh clone on one machine bootstraps fine and on another fails at
    ``git apply`` with no obvious cause.
    """
    attributes = (REPOSITORY_ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "patches/**/*.patch -text" in attributes


# ------------------------------------------------- the control-transfer trace


def _patched_file(patch: Path, path: str) -> str:
    """The post-image of one file as a patch leaves it, hunks only.

    A diff carries context lines as well as added ones, so with enough context
    the ordering of the shipped code is readable straight out of the patch -
    which is exactly what has to be asserted here. Placement is this
    instrumentation's whole design: recording before the handshake broke
    enumeration on this hardware, recording after it did not.
    """
    lines: list[str] = []
    in_file = False
    in_hunk = False
    for line in patch.read_text(encoding="utf-8").splitlines():
        if line.startswith("diff --git "):
            in_file = line.split(" b/")[1].strip() == path
            in_hunk = False
            continue
        if not in_file:
            continue
        if line.startswith("@@"):
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith("+") or line.startswith(" "):
            lines.append(line[1:])
    assert lines, f"{patch} carries no hunk for {path}"
    return "\n".join(lines)


CONTROL_TRACE_PATCH = (
    REPOSITORY_ROOT / "patches" / "pico-pio-usb" / "0003-duo-input-control-trace.patch"
)


def _control_trace_source() -> str:
    return _patched_file(CONTROL_TRACE_PATCH, "src/pio_usb_host.c")


def test_the_control_trace_records_after_the_handshake_never_before():
    """The one placement rule this instrument has.

    ``pio_usb_bus_receive_packet_and_handshake`` sends the ACK before it
    returns, so the return site is already past the timing-critical window.
    Anything recorded before it is recorded inside that window, and this
    project has measured what that costs: instrumentation before the ACK broke
    enumeration outright.
    """
    source = _control_trace_source()

    receive_call = "pio_usb_bus_receive_packet_and_handshake(pp, USB_PID_ACK)"
    assert receive_call in source, (
        "the shipped patch lacks enough real call-site context to prove that "
        "the DATA trace is after the handshake call"
    )
    transaction_start = source.index(receive_call)
    transaction_end = source.index("(usb_out_transaction)(", transaction_start)
    transaction = source[transaction_start:transaction_end]
    handshake = transaction.index(receive_call)
    continued = transaction.index("pio_usb_ll_transfer_continue(ep, receive_len)")
    recorded = transaction.index(
        "ctrl_trace_record(PIO_USB_CTRL_TRACE_KIND_DATA"
    )
    assert handshake < recorded, (
        "the DATA packet is recorded before its handshake has been sent"
    )
    assert continued < recorded, (
        "the DATA packet is recorded before pio_usb_ll_transfer_continue, so "
        "ep->actual_len and ep->total_len would be the values from before it"
    )

    setup_start = source.index("pio_usb_bus_wait_handshake(pp)")
    setup_end = source.index("(handle_endpoint_irq)(", setup_start)
    setup = source[setup_start:setup_end]
    setup_wait = setup.index("pio_usb_bus_wait_handshake(pp)")
    setup_recorded = setup.index(
        "ctrl_trace_record(PIO_USB_CTRL_TRACE_KIND_SETUP"
    )
    assert setup_wait < setup_recorded, (
        "the SETUP packet is recorded before its handshake was waited for"
    )


def test_the_control_trace_ring_is_deep_enough_for_pre_cdc_enumeration():
    source = _control_trace_source()
    assert "#define PIO_USB_CTRL_TRACE_CAPACITY 2048u" in source
    assert "#define PIO_USB_CTRL_TRACE_MASK (PIO_USB_CTRL_TRACE_CAPACITY - 1u)" in source


def test_the_control_trace_copy_is_bounded_by_the_entry_payload():
    source = _control_trace_source()
    record_start = source.index("ctrl_trace_record)(")
    body = source[record_start : source.index("pio_usb_host_ctrl_trace_take")]
    assert "(len < PIO_USB_CTRL_TRACE_BYTES) ? len" in body
    assert ": PIO_USB_CTRL_TRACE_BYTES;" in body
    assert "idx < copied" in body
    assert "entry->byte_count = (uint8_t)copied;" in body


def test_the_control_trace_packet_fields_come_from_the_call_arguments():
    source = _control_trace_source()
    record_start = source.index("ctrl_trace_record)(")
    body = source[record_start : source.index("pio_usb_host_ctrl_trace_take")]
    assert "entry->pid = pid;" in body
    assert "entry->len = len;" in body

    receive_call = "pio_usb_bus_receive_packet_and_handshake(pp, USB_PID_ACK)"
    assert receive_call in source
    transaction_start = source.index(receive_call)
    transaction_end = source.index("(usb_out_transaction)(", transaction_start)
    transaction = source[transaction_start:transaction_end]
    assert (
        "ctrl_trace_record(PIO_USB_CTRL_TRACE_KIND_DATA, ep, receive_pid,\n"
        "                      (uint16_t)receive_len, &pp->usb_rx_buffer[2]);"
        in transaction
    )


def test_a_refused_record_does_not_consume_a_sequence_number():
    source = _control_trace_source()
    record_start = source.index("ctrl_trace_record)(")
    body = source[record_start : source.index("pio_usb_host_ctrl_trace_take")]
    full = body.index("head - tail >= PIO_USB_CTRL_TRACE_CAPACITY")
    refused = body.index("return;", full)
    increment = body.index("ctrl_trace_seq++")
    assert refused < increment, (
        "the sequence is consumed before the ring-full decision; CTRL_LOST "
        "already reports refused records, so accepted entries must stay contiguous"
    )


def test_the_done_entry_is_recorded_at_the_end_of_usb_in_transaction():
    source = _control_trace_source()
    receive_call = "pio_usb_bus_receive_packet_and_handshake(pp, USB_PID_ACK)"
    assert receive_call in source
    transaction_start = source.index(receive_call)
    transaction_end = source.index("(usb_out_transaction)(", transaction_start)
    transaction = source[transaction_start:transaction_end]
    inactive = transaction.index("if (!ep->has_transfer)")
    done = transaction.index(
        "ctrl_trace_record(PIO_USB_CTRL_TRACE_KIND_DONE, ep, receive_pid, 0, NULL);"
    )
    assert inactive < done


def test_the_control_trace_admits_only_control_endpoints():
    """Interrupt IN report traffic must never enter the ring.

    A single keyboard produces thousands of interrupt IN packets a second. Any
    of them in the ring buries the enumeration and the two descriptor
    experiments the ring exists to capture.
    """
    source = _control_trace_source()
    record_start = source.index("ctrl_trace_record)(")
    body = source[record_start : source.index("pio_usb_host_ctrl_trace_take")]
    assert "(ep->ep_num & 0x7f) != 0" in body, (
        "the record site does not filter on the control endpoint number"
    )
    assert body.index("(ep->ep_num & 0x7f) != 0") < body.index("ctrl_trace_head"), (
        "the filter runs after the ring has already been touched"
    )


def test_the_control_trace_counts_what_it_could_not_keep():
    """A silently dropped packet reads exactly like one the device never sent."""
    source = _control_trace_source()
    assert "ctrl_trace_lost" in source
    assert "uint32_t pio_usb_host_ctrl_trace_lost(void)" in source, (
        "the overflow count is not readable, so it can never be reported"
    )
    record_start = source.index("ctrl_trace_record)(")
    body = source[record_start : source.index("pio_usb_host_ctrl_trace_take")]
    assert "ctrl_trace_lost++" in body or "ctrl_trace_lost += 1" in body, (
        "a refused record is dropped without being counted"
    )


def test_the_control_trace_records_the_setup_bytes_that_caused_each_run():
    source = _control_trace_source()
    assert "PIO_USB_CTRL_TRACE_KIND_SETUP" in source
    assert "ctrl_trace_record(PIO_USB_CTRL_TRACE_KIND_SETUP, ep, handshake, 8," in source, (
        "the SETUP entry does not carry the eight request bytes, so a run of "
        "DATA packets cannot be attributed to the request that caused it"
    )


def test_the_control_trace_reports_the_endpoint_fields_it_claims_to():
    """ep->size is what turns EP0 = 8 versus EP0 = 64 into a measurement."""
    source = _control_trace_source()
    record_start = source.index("ctrl_trace_record)(")
    body = source[record_start : source.index("pio_usb_host_ctrl_trace_take")]
    for field, assignment in (
        ("ep_size", "entry->ep_size = ep->size;"),
        ("actual_len", "entry->actual_len = ep->actual_len;"),
        ("total_len", "entry->total_len = ep->total_len;"),
        ("dev_addr", "entry->dev_addr = ep->dev_addr;"),
        ("ep_num", "entry->ep_num = ep->ep_num;"),
    ):
        assert assignment in body, f"{field} is not taken from its own endpoint field"


def test_the_control_trace_does_no_formatting_or_blocking_at_the_record_site():
    """Copying sixteen bytes and a few scalars is the entire permitted cost."""
    source = _control_trace_source()
    record_start = source.index("ctrl_trace_record)(")
    body = source[record_start : source.index("pio_usb_host_ctrl_trace_take")]
    for forbidden in ("printf", "snprintf", "malloc", "busy_wait", "sleep_", "while ("):
        assert forbidden not in body, (
            f"{forbidden} appears at the record site, which runs on the host "
            "core inside the transaction path"
        )


def test_the_control_trace_exposes_a_drain_api_without_a_shared_layout():
    """The firmware must not have to mirror a struct it cannot include.

    The control-trace patch itself stays out of the headers, so the drain has
    to hand back scalars rather than an entry whose layout two files would
    have to agree on.
    """
    source = _control_trace_source()
    assert "bool pio_usb_host_ctrl_trace_take(" in source
    take = source[source.index("bool pio_usb_host_ctrl_trace_take(") :]
    for out_param in (
        "out_seq",
        "out_kind",
        "out_dev_addr",
        "out_ep_num",
        "out_pid",
        "out_len",
        "out_ep_size",
        "out_actual_len",
        "out_total_len",
        "out_bytes",
        "out_byte_count",
    ):
        assert out_param in take, f"the drain never returns {out_param}"
    assert "pio_usb_ctrl_trace_entry_t" not in take.split("{", 1)[0], (
        "the drain's signature exposes the private entry layout"
    )


def test_the_control_trace_entry_kinds_are_the_numbers_the_firmware_expects():
    """Two files agree on these three numbers and cannot include each other."""
    source = _control_trace_source()
    for name, value in (
        ("PIO_USB_CTRL_TRACE_KIND_SETUP", "0"),
        ("PIO_USB_CTRL_TRACE_KIND_DATA", "1"),
        ("PIO_USB_CTRL_TRACE_KIND_DONE", "2"),
    ):
        assert f"#define {name} {value}u" in source, (
            f"{name} is not defined as {value}"
        )

    callbacks = (
        REPOSITORY_ROOT / "firmware" / "u1_reference" / "host_callbacks.cpp"
    ).read_text(encoding="utf-8")
    assert "static_assert" in callbacks and "ReferenceControlTraceKind" in callbacks, (
        "nothing pins the firmware enum to the numbers the patch produces"
    )
