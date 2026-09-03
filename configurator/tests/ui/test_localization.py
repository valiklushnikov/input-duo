"""Both catalogues are complete, and protocol identifiers stay untranslated."""

from __future__ import annotations

import ast
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QLabel

from duo_input.i18n import (
    DEFAULT_LANGUAGE,
    LANGUAGES,
    SETTINGS_LANGUAGE_KEY,
    TranslationManager,
    catalogue_path,
    translations_directory,
)
from duo_input.ui.settings import SettingsPage
from duo_input.ui import theme

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "duo_input"

#: Every module whose strings the operator reads.
TRANSLATED_MODULES = (
    "app.py",
    "i18n.py",
    "ui/main_window.py",
    "ui/overview.py",
    "ui/profiles.py",
    "ui/bindings.py",
    "ui/macros.py",
    "ui/mouse.py",
    "ui/diagnostics.py",
    "ui/settings.py",
    "ui/theme.py",
    "ui/models/binding_table.py",
    "ui/models/macro_steps.py",
    "ui/tray.py",
    "ui/clipboard_page.py",
)


def _source_strings() -> set[str]:
    """Every literal the interface asks Qt to translate.

    The modules are parsed rather than scanned, so a message written as two
    adjacent literals reaches this set as the single string Qt looks up.
    """
    found: set[str] = set()
    for name in TRANSLATED_MODULES:
        tree = ast.parse((SOURCE_ROOT / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            target = node.func
            if isinstance(target, ast.Attribute) and target.attr == "tr":
                argument = node.args[0]
            elif isinstance(target, ast.Attribute) and target.attr == "translate":
                # QCoreApplication.translate(context, literal). Its context
                # argument is a literal too, so the message is args[1]; calls
                # that pass a variable there are skipped by the check below.
                argument = node.args[1] if len(node.args) > 1 else None
            elif isinstance(target, ast.Name) and target.id == "QT_TRANSLATE_NOOP":
                argument = node.args[1] if len(node.args) > 1 else None
            else:
                continue
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                found.add(argument.value)
    return found


def _catalogue(language: str) -> dict[str, str]:
    tree = ET.parse(catalogue_path(language).with_suffix(".ts"))
    return {
        message.find("source").text or "": (message.find("translation").text or "")
        for message in tree.iter("message")
    }


def _unfinished(language: str) -> list[str]:
    tree = ET.parse(catalogue_path(language).with_suffix(".ts"))
    return [
        message.find("source").text or ""
        for message in tree.iter("message")
        if message.find("translation").get("type") == "unfinished"
    ]


# ------------------------------------------------------------------ coverage


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_interface_string_is_in_the_catalogue(language):
    catalogue = _catalogue(language)

    missing = sorted(source for source in _source_strings() if source not in catalogue)

    assert missing == []


@pytest.mark.parametrize("language", LANGUAGES)
def test_no_entry_is_left_unfinished(language):
    assert _unfinished(language) == []


@pytest.mark.parametrize("language", LANGUAGES)
def test_no_entry_is_left_empty(language):
    empty = sorted(source for source, text in _catalogue(language).items() if not text)

    assert empty == []


def test_the_russian_catalogue_is_actually_russian():
    catalogue = _catalogue("ru")

    cyrillic = [text for text in catalogue.values() if re.search("[а-яА-ЯёЁ]", text)]

    assert len(cyrillic) > len(catalogue) // 2


@pytest.mark.parametrize("language", LANGUAGES)
def test_a_compiled_catalogue_ships_beside_its_source(language):
    assert catalogue_path(language).is_file()
    assert catalogue_path(language).parent == translations_directory()


# ------------------------------------------------- what is never translated


@pytest.mark.parametrize(
    "identifier",
    (
        "TOGGLE_MOUSE_ROUTE",
        "SET_KEYBOARD_ROUTE",
        "RUN_MACRO",
        "KEY_TAP",
        "REPLACE",
        "INHERIT",
        "disconnected",
        "write_config",
        "INVALID_REQUEST",
    ),
)
def test_a_protocol_identifier_is_never_offered_for_translation(identifier):
    assert identifier not in _source_strings()


def test_the_stop_button_keeps_one_wording_everywhere():
    # Emergency wording is deliberately the same phrase in every language, so
    # a screenshot from any operator is recognisable.
    assert "STOP AND RELEASE ALL" not in _source_strings()


def test_placeholders_survive_translation():
    catalogue = _catalogue("ru")

    for source, text in catalogue.items():
        assert set(re.findall(r"\{\d+", source)) == set(re.findall(r"\{\d+", text)), source


# ----------------------------------------------------------------- manager


@pytest.fixture
def settings(tmp_path, monkeypatch) -> QSettings:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    store = QSettings(str(tmp_path / "duo-input.ini"), QSettings.Format.IniFormat)
    store.clear()
    return store


def test_russian_is_what_a_first_run_gets(qapp, settings):
    manager = TranslationManager(qapp, settings=settings)

    assert manager.load_saved() == DEFAULT_LANGUAGE
    assert DEFAULT_LANGUAGE == "ru"


def test_a_chosen_language_is_remembered(qapp, settings):
    manager = TranslationManager(qapp, settings=settings)

    assert manager.set_language("en") is True

    assert settings.value(SETTINGS_LANGUAGE_KEY) == "en"
    assert TranslationManager(qapp, settings=settings).load_saved() == "en"


def test_an_unsupported_language_is_refused(qapp, settings):
    manager = TranslationManager(qapp, settings=settings)

    with pytest.raises(ValueError):
        manager.set_language("fr")


def test_a_remembered_language_this_build_dropped_falls_back(qapp, settings):
    settings.setValue(SETTINGS_LANGUAGE_KEY, "fr")

    assert TranslationManager(qapp, settings=settings).load_saved() == DEFAULT_LANGUAGE


def test_choosing_a_language_translates_the_running_application(qapp, settings):
    manager = TranslationManager(qapp, settings=settings)

    manager.set_language("ru")

    assert qapp.translate("MainWindow", "Overview") == "Обзор"


def test_switching_back_to_english_restores_the_source_text(qapp, settings):
    manager = TranslationManager(qapp, settings=settings)
    manager.set_language("ru")

    manager.set_language("en")

    assert qapp.translate("MainWindow", "Overview") == "Overview"


# ------------------------------------------------------------------ settings


@pytest.fixture
def page(qtbot, qapp, settings) -> SettingsPage:
    page = SettingsPage(TranslationManager(qapp, settings=settings), settings=settings)
    qtbot.addWidget(page)
    return page


def test_the_settings_page_offers_both_languages(page):
    offered = [page.language_combo.itemData(row) for row in range(page.language_combo.count())]

    assert offered == list(LANGUAGES)


def test_choosing_a_language_stores_it(page, settings):
    page.select_language("en")

    assert settings.value(SETTINGS_LANGUAGE_KEY) == "en"


def test_a_language_change_says_what_happens_next(page):
    assert page.restart_label.text() == ""

    page.select_language("en")

    assert page.restart_label.text()


def test_the_project_directory_is_remembered(page, settings, tmp_path):
    page.set_project_directory(tmp_path / "projects")

    assert Path(settings.value(page.PROJECT_DIRECTORY_KEY)) == tmp_path / "projects"
    assert page.project_directory() == tmp_path / "projects"


def test_the_log_level_is_remembered_and_applied(page, settings):
    import logging

    page.select_log_level("DEBUG")

    assert settings.value(page.LOG_LEVEL_KEY) == "DEBUG"
    assert logging.getLogger("duo_input").level == logging.DEBUG


def test_every_control_carries_an_accessible_name(page):
    for widget in (
        page.language_combo,
        page.project_directory_edit,
        page.browse_button,
        page.log_level_combo,
    ):
        assert widget.accessibleName()


def test_settings_use_the_shared_visual_hierarchy(page):
    titles = [
        label.text()
        for label in page.findChildren(QLabel)
        if label.property("role") == theme.ROLE_PAGE_TITLE
    ]

    assert titles == ["Settings"]
    assert page.restart_label.property("role") == theme.ROLE_NOTE


# ------------------------------------------------------------- screenshots


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("scale", ("1", "1.5"))
def test_no_page_clips_a_control_or_shows_stale_english(language, scale, tmp_path):
    """Render every page at 1280x800 and read what came out.

    Qt fixes its scale factor when the application is created, so 100% and
    150% cannot both be checked inside this process; each combination gets its
    own run of ``screenshot_smoke.py``.
    """
    import os
    import subprocess
    import sys

    script = Path(__file__).with_name("screenshot_smoke.py")
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QT_SCALE_FACTOR"] = scale
    environment["PYTHONPATH"] = str(SOURCE_ROOT.parent)

    finished = subprocess.run(
        [sys.executable, str(script), "--language", language, "--output", str(tmp_path)],
        capture_output=True,
        text=True,
        env=environment,
        timeout=180,
    )

    assert finished.returncode == 0, finished.stdout + finished.stderr
    assert list(tmp_path.glob("*.png"))
