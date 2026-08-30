"""Render every page at one language and one DPI, and report what looks wrong.

This runs as its own process because Qt fixes its scale factor when the
application is created: checking 100% and 150% in one pytest process is not
possible, so the test launches this script once per combination.

It checks the two things a screenshot review is actually for:

* no control is narrower than the text inside it, at either scale;
* nothing on a Russian screen is still showing its English source string.
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

WIDTH = 1280
HEIGHT = 800


def _english_sources() -> set[str]:
    """Sources whose Russian differs, so seeing one in RU means it is stale."""
    from duo_input.i18n import catalogue_path

    tree = ET.parse(catalogue_path("ru").with_suffix(".ts"))
    stale: set[str] = set()
    for message in tree.iter("message"):
        source = message.find("source").text or ""
        translation = message.find("translation").text or ""
        if source and translation and source != translation and "{" not in source:
            stale.add(source)
    return stale


def _texts(widget) -> list[tuple[object, str]]:
    from PySide6.QtWidgets import QAbstractButton, QGroupBox, QLabel

    found: list[tuple[object, str]] = []
    # findChildren takes one type at a time, so each kind is asked for
    # separately and the results are joined here.
    for kind in (QAbstractButton, QLabel, QGroupBox):
        for child in widget.findChildren(kind):
            if not child.isVisibleTo(widget):
                continue
            text = child.title() if isinstance(child, QGroupBox) else child.text()
            if text:
                found.append((child, text))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", required=True)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()

    from PySide6.QtWidgets import QAbstractButton, QApplication

    application = QApplication(sys.argv)

    from duo_input.device.service import DeviceService
    from duo_input.i18n import TranslationManager
    from duo_input.ui.main_window import MainWindow
    from duo_input.ui.theme import apply_theme

    # Without this the offscreen platform renders every glyph as an empty box:
    # its font database starts empty, so the shipped interface has to hand it
    # the faces Windows already has before anything is grabbed.
    apply_theme(application)

    translations = TranslationManager(application)
    translations.set_language(arguments.language, remember=False)

    window = MainWindow(DeviceService())
    window.resize(WIDTH, HEIGHT)
    window.show()
    application.processEvents()

    arguments.output.mkdir(parents=True, exist_ok=True)
    stale_sources = _english_sources() if arguments.language == "ru" else set()
    problems: list[str] = []

    for page in window.PAGE_ORDER:
        window.show_page(page)
        application.processEvents()
        name = window.nav.item(page).text()
        window.grab().save(str(arguments.output / f"{arguments.language}-{page}.png"))

        for widget, text in _texts(window):
            if isinstance(widget, QAbstractButton):
                wanted = widget.sizeHint().width()
                if wanted > widget.width():
                    problems.append(
                        f"{name}: {text!r} needs {wanted}px but has {widget.width()}px"
                    )
            if text in stale_sources:
                problems.append(f"{name}: {text!r} is still in English")

    for problem in sorted(set(problems)):
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
