"""Выбор реализации границы платформы. Единственный sys.platform в подсистеме.

Импорты ленивые и внутри веток: Windows-сборка никогда не импортирует
macos_backend, а значит и macos_pasteboard, а значит и pyobjc. В runtime-графе
Windows нативного macOS-кода нет вовсе - это важно для Nuitka.
"""

from __future__ import annotations

import sys

from .backend import ClipboardBackend


class UnsupportedPlatformError(Exception):
    """Платформа, для которой нет реализации буфера обмена."""


def create_backend(clipboard, parent=None) -> ClipboardBackend:
    if sys.platform == "win32":
        from .windows_backend import WindowsClipboardBackend

        return WindowsClipboardBackend(clipboard, parent)
    if sys.platform == "darwin":
        from .macos_backend import MacOSClipboardBackend

        return MacOSClipboardBackend(clipboard, parent)
    raise UnsupportedPlatformError(sys.platform)


__all__ = ["UnsupportedPlatformError", "create_backend"]
