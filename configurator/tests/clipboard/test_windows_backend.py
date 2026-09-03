"""Снимок локального буфера: что берём, что пропускаем, чего не трогаем."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from duo_input.clipboard.backend import ORIGIN_MIME
from duo_input.clipboard.offer import MAX_CONTENT_BYTES
from duo_input.clipboard.windows_backend import is_private, snapshot_from


class _FakeMimeData:
    """Утиная замена QMimeData: только то, что читает снимок."""

    def __init__(self, payloads: dict[str, bytes]) -> None:
        self._payloads = payloads

    def formats(self) -> list[str]:
        return list(self._payloads)

    def data(self, mime: str) -> bytes:
        return self._payloads.get(mime, b"")


def test_a_password_manager_marker_makes_the_clipboard_private():
    formats = [
        "text/plain",
        'application/x-qt-windows-mime;value="ExcludeClipboardContentFromMonitorProcessing"',
    ]

    assert is_private(formats) is True


def test_an_ordinary_clipboard_is_not_private():
    assert is_private(["text/plain", "text/html"]) is False


def test_our_own_marker_makes_the_clipboard_private_to_us():
    assert is_private(["text/plain", ORIGIN_MIME]) is True


def test_snapshot_keeps_only_the_formats_we_synchronise():
    data = _FakeMimeData({"text/plain": b"hello", "text/html": b"<b>hello</b>"})

    snapshot = snapshot_from(data)

    assert set(snapshot.payloads) == {"text/plain"}


def test_snapshot_of_an_oversized_payload_is_empty():
    data = _FakeMimeData({"image/png": b"x" * (MAX_CONTENT_BYTES + 1)})

    assert snapshot_from(data).payloads == {}


def test_snapshot_of_an_empty_clipboard_is_empty():
    assert snapshot_from(_FakeMimeData({})).payloads == {}


def test_snapshot_ignores_empty_payloads():
    """Format exists but content is empty - should be skipped."""
    data = _FakeMimeData({"text/plain": b""})

    assert snapshot_from(data).payloads == {}
