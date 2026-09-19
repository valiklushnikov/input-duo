"""Единственная ветка по платформе в подсистеме передачи."""

from __future__ import annotations

import shutil
import sys
import uuid
from pathlib import Path

import pytest

from duo_input.transfer.platform_files import UnsupportedPlatformError, create_file_backend


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-ветка")
def test_on_windows_the_windows_backend_is_chosen(qapp):
    from duo_input.transfer.windows_files import WindowsFileClipboardBackend

    assert isinstance(create_file_backend(), WindowsFileClipboardBackend)


def test_an_unknown_platform_is_refused_loudly_rather_than_silently(monkeypatch):
    monkeypatch.setattr(sys, "platform", "haiku")

    with pytest.raises(UnsupportedPlatformError):
        create_file_backend()


@pytest.mark.skipif(sys.platform != "darwin", reason="darwin only")
def test_darwin_returns_mac_receive_router_wrapping_mac_receiver():
    """Task 16: darwin now always returns the ``MacReceiveRouter`` facade
    (per-offer File Provider/staging selection), not a bare ``MacFileReceiver``
    - but with no File Provider backend wired in (the zero-arg call from
    every OTHER test in this module and from ``test_macos_receiver.py``),
    the router always selects the same staging receiver it wraps, so staging
    behavior is unchanged."""
    from duo_input.transfer.macos_files import MacFileReceiver
    from duo_input.transfer.platform_files import MacReceiveRouter

    backend = create_file_backend()

    assert isinstance(backend, MacReceiveRouter)
    assert isinstance(backend._staging, MacFileReceiver)
    assert backend._fp is None


@pytest.mark.skipif(sys.platform != "darwin", reason="darwin only")
def test_darwin_factory_recovers_leftover_incomplete_dir_at_startup():
    """Spec invariant: an incomplete transfer dir left by a crash is removed
    on startup recovery, before the receiver is handed to the caller.

    This targets the SAME root ``create_file_backend`` uses in production
    (``~/Library/Caches/duo-input/incoming``) since the factory has no seam
    to redirect it to a tmp root. To keep this safe against the operator's
    real cache directory, the leftover dir uses a unique uuid4 name (never
    collides with a real transfer id) and is asserted on by that exact name
    only - nothing else under the real root is touched or inspected.
    """
    root = Path.home() / "Library" / "Caches" / "duo-input" / "incoming"
    root.mkdir(parents=True, exist_ok=True)
    leftover = root / f"test-leftover-{uuid.uuid4().hex}"
    leftover.mkdir()
    (leftover / ".incomplete").touch()
    try:
        create_file_backend()

        assert not leftover.exists(), "incomplete staging dir must be recovered at startup"
    finally:
        shutil.rmtree(leftover, ignore_errors=True)
