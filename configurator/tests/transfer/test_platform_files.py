"""Единственная ветка по платформе в подсистеме передачи."""

from __future__ import annotations

import sys

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


def test_macos_is_refused_because_m1_does_not_implement_it(monkeypatch):
    # Спека §18: ядро от Windows не зависит, но адаптера для Finder нет, и
    # молчаливая заглушка выглядела бы как работающая фича.
    monkeypatch.setattr(sys, "platform", "darwin")

    with pytest.raises(UnsupportedPlatformError):
        create_file_backend()
