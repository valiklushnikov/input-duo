"""Единственное место подсистемы передачи файлов, где мы говорим с AppKit.

Только host-only вооружение NSPasteboard списком file:// URL уже
существующих (staged) файлов. Ленивости здесь нет: файлы на диске, Finder
копирует их на ⌘V (спайк records/2026-09-16-macos-file-promise-spike.md).
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from AppKit import NSPasteboard, NSPasteboardContentsCurrentHostOnly
from Foundation import NSURL

logger = logging.getLogger(__name__)


class PasteboardArmError(Exception):
    """NSPasteboard отказался принять file:// URL."""


def _to_file_urls(paths: Sequence[Path | str]) -> list[str]:
    return [Path(p).absolute().as_uri() for p in paths]


def arm(paths: Sequence[Path | str]) -> int:
    pb = NSPasteboard.generalPasteboard()
    pb.prepareForNewContentsWithOptions_(NSPasteboardContentsCurrentHostOnly)
    urls = [NSURL.fileURLWithPath_(str(Path(p).absolute())) for p in paths]
    if not pb.writeObjects_(urls):
        raise PasteboardArmError("NSPasteboard.writeObjects вернул false")
    return int(pb.changeCount())


__all__ = [
    "PasteboardArmError",
    "arm",
]
