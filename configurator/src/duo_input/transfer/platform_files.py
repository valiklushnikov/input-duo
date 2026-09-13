"""Выбор реализации границы платформы для передачи файлов.

Единственный sys.platform в подсистеме, по образцу
clipboard/platform_backend.py. Импорты ленивые и внутри ветвей: сборка под
macOS никогда не импортирует windows_files, а значит и windows_com, а значит и
ctypes-описания COM. В runtime-графе macOS Windows-кода нет вовсе.

macOS отвергается явно, а не заглушкой: аналог здесь -
NSFilePromiseProvider, его в M1 нет, и молчаливая заглушка выглядела бы как
работающая фича (спека §18).
"""

from __future__ import annotations

import sys


class UnsupportedPlatformError(Exception):
    """Платформа, для которой передачи файлов пока нет."""


def create_file_backend(parent=None):
    if sys.platform == "win32":
        from .windows_files import WindowsFileClipboardBackend

        return WindowsFileClipboardBackend(parent)
    raise UnsupportedPlatformError(sys.platform)


__all__ = ["UnsupportedPlatformError", "create_file_backend"]
