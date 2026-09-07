"""Подсистема должна оставаться отделимой от интерфейса."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path("src", "duo_input", "clipboard")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text("utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_clipboard_package_never_imports_qtwidgets():
    offenders = {
        path.name
        for path in PACKAGE.glob("*.py")
        if any(name.startswith("PySide6.QtWidgets") for name in _imported_modules(path))
    }

    assert offenders == set(), (
        "clipboard/ должен зависеть только от QtCore и QtNetwork, иначе его "
        "нельзя будет вынести в отдельный процесс вместе с передачей файлов"
    )


def test_the_clipboard_package_never_imports_the_ui():
    offenders = {
        path.name
        for path in PACKAGE.glob("*.py")
        if any(name.startswith("duo_input.ui") for name in _imported_modules(path))
    }

    assert offenders == set()
