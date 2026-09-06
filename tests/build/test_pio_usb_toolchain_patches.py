"""What the patched PIO USB toolchain must promise, verbatim.

Four defects in the pinned TinyUSB and Pico-PIO-USB revisions stop this
hardware from enumerating and reporting at all; they are recorded in
``docs/superpowers/records/2026-09-03-pio-usb-hub-v1-record.md``. Rather than
hand-editing the clones - which
``cmake/pio_usb_toolchain_lock.cmake`` rightly refuses to build against - the
fixes live here as patches under version control, and the bootstrap applies
them and commits them into the clone.

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
PICO_PIO_USB_PATCHED_REVISION = "a2a076497ab6f373ae1c9e98777bf3a0c6f4a40e"

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


def test_every_patched_dependency_has_exactly_one_patch():
    for name in PATCHED_DEPENDENCIES:
        patches = sorted((REPOSITORY_ROOT / "patches" / name).glob("*.patch"))
        assert len(patches) == 1, f"{name}: expected one patch, found {patches}"


def test_patches_are_tracked_text_and_not_empty():
    for patch in _patch_paths():
        text = patch.read_text(encoding="utf-8")
        assert text.startswith("diff --git "), f"{patch} is not a git patch"
        assert "\r\n" not in text, (
            f"{patch} contains CRLF; it would not apply to an LF-normalised clone"
        )


def test_the_pico_pio_usb_patch_touches_only_the_host_transaction_file():
    text = (
        REPOSITORY_ROOT / "patches" / "pico-pio-usb" / "0001-duo-input-host-fixes.patch"
    ).read_text(encoding="utf-8")
    touched = {line.split(" b/")[1].strip() for line in text.splitlines() if line.startswith("diff --git ")}
    assert touched == {"src/pio_usb_host.c"}, (
        "the PIO programs and bus timing are deliberately left alone; "
        f"this patch touches {sorted(touched)}"
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
