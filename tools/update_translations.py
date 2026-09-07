"""Re-scan the interface for translatable strings and compile the catalogues.

Run this after adding or changing any ``tr()`` string:

    python tools/update_translations.py

New sources are added to both ``.ts`` files as unfinished. Fill the Russian
ones in by hand - a translation is a decision, not something this script is
entitled to invent - then run it again. It exits non-zero while anything is
still unfinished, which is the same thing
``configurator/tests/ui/test_localization.py`` asserts.

The English catalogue is different: its source text *is* English, so an entry
whose translation is empty is filled with the source and marked finished.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "configurator" / "src"
PACKAGE = Path("duo_input")
TRANSLATIONS = PACKAGE / "resources" / "translations"

LANGUAGES = ("ru", "en")

#: Every module whose strings the operator reads, relative to the package.
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


def _tool(name: str) -> str:
    """The Qt tool, preferring the one in the active virtual environment."""
    for candidate in (
        Path(sys.executable).parent / f"pyside6-{name}.exe",
        Path(sys.executable).parent / f"pyside6-{name}",
    ):
        if candidate.is_file():
            return str(candidate)
    return f"pyside6-{name}"


def update() -> None:
    sources = [str(PACKAGE / module) for module in TRANSLATED_MODULES]
    catalogues: list[str] = []
    for language in LANGUAGES:
        catalogues.extend(["-ts", str(TRANSLATIONS / f"duo_input_{language}.ts")])
    subprocess.run(
        [_tool("lupdate"), *sources, *catalogues], cwd=SOURCE_ROOT, check=True
    )


def fill_english() -> None:
    """Give every English entry its own source text and mark it finished."""
    path = SOURCE_ROOT / TRANSLATIONS / "duo_input_en.ts"
    tree = ET.parse(path)
    for message in tree.iter("message"):
        node = message.find("translation")
        if node is None:
            continue
        if not (node.text or ""):
            node.text = message.find("source").text or ""
        node.attrib.pop("type", None)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def unfinished(language: str) -> list[str]:
    path = SOURCE_ROOT / TRANSLATIONS / f"duo_input_{language}.ts"
    tree = ET.parse(path)
    return [
        message.find("source").text or ""
        for message in tree.iter("message")
        if message.find("translation").get("type") == "unfinished"
        or not (message.find("translation").text or "")
    ]


def compile_catalogues() -> None:
    for language in LANGUAGES:
        source = SOURCE_ROOT / TRANSLATIONS / f"duo_input_{language}.ts"
        subprocess.run(
            [_tool("lrelease"), str(source), "-qm", str(source.with_suffix(".qm"))],
            check=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report unfinished entries without rescanning or compiling",
    )
    arguments = parser.parse_args()

    if not arguments.check:
        update()
        fill_english()

    missing = {language: unfinished(language) for language in LANGUAGES}
    if any(missing.values()):
        for language, sources in missing.items():
            for source in sources:
                print(f"{language}: untranslated: {source!r}")
        return 1

    if not arguments.check:
        compile_catalogues()
    print("every catalogue is complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
