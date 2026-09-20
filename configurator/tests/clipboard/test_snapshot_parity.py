"""Снимок одного и того же буфера одинаков на Windows и на macOS."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtGui import QImage

from duo_input.clipboard.macos_backend import snapshot_from as mac_snapshot
from duo_input.clipboard.windows_backend import snapshot_from as win_snapshot


def _rich_mime_data() -> QMimeData:
    data = QMimeData()
    data.setData("text/plain", "привет".encode("utf-8"))
    data.setData("text/html", b"<b>hi</b>")
    data.setUrls([QUrl("https://example.com"), QUrl("file:///tmp/secret")])
    image = QImage(2, 2, QImage.Format.Format_RGB32)
    image.fill(0x00FF00)
    data.setImageData(image)
    return data


def test_windows_and_macos_snapshots_are_byte_identical():
    data = _rich_mime_data()

    win = win_snapshot(data).payloads
    mac = mac_snapshot(data).payloads

    assert win == mac
    # image/png отсутствует: при наличии локального file:// URL сопутствующий
    # image трактуется как иконка file-copy (Finder кладёт её на pasteboard) и
    # не синхронизируется - см. png_bytes. Поведение общее для обеих платформ,
    # поэтому паритет сохраняется.
    assert set(win) == {"text/plain", "text/html", "text/uri-list"}
    assert win["text/uri-list"] == b"https://example.com\r\n"  # file:// вырезан


def test_image_without_local_files_stays_in_both_snapshots():
    """Настоящее изображение без file:// URL синхронизируется на обеих
    платформах байт-в-байт (эвристика file-icon сюда не применяется)."""
    data = QMimeData()
    data.setData("text/plain", b"hi")
    image = QImage(2, 2, QImage.Format.Format_RGB32)
    image.fill(0x00FF00)
    data.setImageData(image)

    win = win_snapshot(data).payloads
    mac = mac_snapshot(data).payloads

    assert win == mac
    assert "image/png" in win
    assert win["image/png"].startswith(b"\x89PNG\r\n\x1a\n")
