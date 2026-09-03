"""Автозапуск: ярлык, который видно и который убирается руками."""

from __future__ import annotations

from pathlib import Path

from duo_input.persistence import autostart


def test_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(autostart, "startup_directory", lambda: tmp_path)

    assert autostart.is_enabled() is False


def test_enabling_creates_a_file_in_the_startup_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(autostart, "startup_directory", lambda: tmp_path)

    autostart.enable(Path("C:/Program Files/Duo Input/DuoInput.exe"))

    assert autostart.is_enabled() is True
    assert autostart.shortcut_path().exists()


def test_disabling_removes_it(tmp_path, monkeypatch):
    monkeypatch.setattr(autostart, "startup_directory", lambda: tmp_path)
    autostart.enable(Path("C:/Program Files/Duo Input/DuoInput.exe"))

    autostart.disable()

    assert autostart.is_enabled() is False


def test_disabling_twice_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(autostart, "startup_directory", lambda: tmp_path)

    autostart.disable()
    autostart.disable()

    assert autostart.is_enabled() is False
