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


def _to_nsurl(url: str | NSURL) -> NSURL:
    """Coerce one already-resolved URL into an ``NSURL``, passing a real
    ``NSURL`` straight through. A ``str`` with a scheme (e.g. the
    ``file://...`` a File Provider user-visible URL resolves to) goes
    through ``URLWithString_``; a bare path string goes through
    ``fileURLWithPath_`` exactly like ``arm`` above.
    """
    if isinstance(url, NSURL):
        return url
    if "://" in url:
        return NSURL.URLWithString_(url)
    return NSURL.fileURLWithPath_(url)


def arm_urls(urls: Sequence[str | NSURL]) -> int:
    """Same host-only arming as ``arm``, for URLs already resolved by the
    caller (Task 8: File Provider user-visible URLs) instead of raw
    filesystem paths. Mirrors ``arm`` exactly, including its behaviour on
    an empty list - no divergent empty-list guard here.
    """
    pb = NSPasteboard.generalPasteboard()
    pb.prepareForNewContentsWithOptions_(NSPasteboardContentsCurrentHostOnly)
    ns_urls = [_to_nsurl(u) for u in urls]
    if not pb.writeObjects_(ns_urls):
        raise PasteboardArmError("NSPasteboard.writeObjects вернул false")
    return int(pb.changeCount())


__all__ = [
    "PasteboardArmError",
    "arm",
    "arm_urls",
]
