"""Подсистема должна оставаться отделимой от интерфейса: только QtCore/QtGui/QtNetwork, никогда QtWidgets."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path("src", "duo_input", "clipboard")

_NATIVE_PREFIXES = ("AppKit", "Foundation", "objc", "PyObjCTools", "Cocoa")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text("utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _has_native_import(path: Path) -> bool:
    return any(
        name.split(".")[0] in _NATIVE_PREFIXES for name in _imported_modules(path)
    )


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


def test_only_macos_pasteboard_touches_pyobjc():
    offenders = {
        path.name
        for path in PACKAGE.glob("*.py")
        if path.name != "macos_pasteboard.py" and _has_native_import(path)
    }

    assert offenders == set(), (
        "pyobjc/AppKit разрешён только в macos_pasteboard.py — вся нативная "
        "грязь должна быть в одном месте"
    )


def test_backend_module_does_not_import_concrete_backends():
    imported = _imported_modules(PACKAGE / "backend.py")

    assert not any(
        name.endswith("windows_backend") or name.endswith("macos_backend")
        for name in imported
    ), "backend.py — нейтральный контракт, он не должен знать реализации"


def test_windows_backend_stays_free_of_native_macos_imports():
    assert not _has_native_import(PACKAGE / "windows_backend.py")
