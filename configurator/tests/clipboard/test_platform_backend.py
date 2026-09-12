"""Фабрика выбирает бэкенд по платформе и не тянет чужие импорты."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from duo_input.clipboard import platform_backend
from duo_input.clipboard.platform_backend import (
    UnsupportedPlatformError,
    create_backend,
)


def test_windows_platform_builds_the_windows_backend(monkeypatch):
    monkeypatch.setattr(platform_backend.sys, "platform", "win32")
    from duo_input.clipboard.windows_backend import WindowsClipboardBackend

    backend = create_backend(clipboard=object())

    assert isinstance(backend, WindowsClipboardBackend)


import sys as _sys


@pytest.mark.skipif(_sys.platform != "darwin", reason="macos_backend тянет macos_pasteboard")
def test_darwin_platform_builds_the_macos_backend(monkeypatch):
    monkeypatch.setattr(platform_backend.sys, "platform", "darwin")
    from duo_input.clipboard.macos_backend import MacOSClipboardBackend

    backend = create_backend(clipboard=object())

    assert isinstance(backend, MacOSClipboardBackend)


def test_an_unknown_platform_is_refused(monkeypatch):
    monkeypatch.setattr(platform_backend.sys, "platform", "sunos5")

    with pytest.raises(UnsupportedPlatformError, match="sunos5"):
        create_backend(clipboard=object())


def test_importing_the_factory_does_not_pull_in_concrete_backends():
    """Сам импорт фабрики не должен грузить ни один конкретный бэкенд."""
    import importlib
    import sys

    for name in (
        "duo_input.clipboard.platform_backend",
        "duo_input.clipboard.windows_backend",
        "duo_input.clipboard.macos_backend",
        "duo_input.clipboard.macos_pasteboard",
    ):
        sys.modules.pop(name, None)

    importlib.import_module("duo_input.clipboard.platform_backend")

    assert "duo_input.clipboard.windows_backend" not in sys.modules
    assert "duo_input.clipboard.macos_backend" not in sys.modules
    assert "duo_input.clipboard.macos_pasteboard" not in sys.modules


def test_the_windows_path_never_loads_macos_modules(monkeypatch):
    """Windows-ветка не должна затягивать macOS/pyobjc в runtime-граф.

    Это доказывает инвариант фактически, а не по тексту исходников: даже на
    macOS-хосте выбор win32-ветки не импортирует нативные модули.
    """
    import sys

    sys.modules.pop("duo_input.clipboard.macos_backend", None)
    sys.modules.pop("duo_input.clipboard.macos_pasteboard", None)
    monkeypatch.setattr(platform_backend.sys, "platform", "win32")

    create_backend(clipboard=object())

    assert "duo_input.clipboard.macos_backend" not in sys.modules
    assert "duo_input.clipboard.macos_pasteboard" not in sys.modules
