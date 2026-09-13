"""Подсистема должна оставаться отделимой от интерфейса: только QtCore/QtGui/QtNetwork, никогда QtWidgets."""

from __future__ import annotations

import ast
from pathlib import Path

# Anchored to this file, not to the working directory. Relative to the cwd it
# resolved only when pytest was run from inside configurator/: from the
# repository root - which is how the release build and the full gate run it -
# the two tests that open a file by name failed, and the three that glob the
# package silently passed over an empty directory, checking nothing at all.
PACKAGE = Path(__file__).resolve().parents[2] / "src" / "duo_input" / "clipboard"


def _package_modules() -> list[Path]:
    """Every module in the package, refusing to report success on none.

    An empty sweep is what made these boundaries look green while they were
    being enforced against nothing.
    """
    modules = sorted(PACKAGE.glob("*.py"))
    assert modules, f"no modules found under {PACKAGE}"
    return modules

_NATIVE_PREFIXES = ("AppKit", "Foundation", "objc", "PyObjCTools", "Cocoa")


def _package_parts(path: Path) -> list[str]:
    """Положение модуля в пакете, посчитанное по __init__.py вверх по дереву.

    Для .../src/duo_input/transfer/windows_com.py это ["duo_input",
    "transfer"]: выше transfer/ лежит duo_input/ с __init__.py, а над ним
    src/ уже без него.
    """
    parts: list[str] = []
    parent = path.parent
    while (parent / "__init__.py").exists():
        parts.append(parent.name)
        parent = parent.parent
    return list(reversed(parts))


def _imported_modules(path: Path) -> set[str]:
    """Имена импортируемых модулей, все до единого в абсолютном виде.

    Относительные импорты разрешаются по node.level и положению самого
    модуля в пакете. Пока этого не делалось, в разборщике было две дыры, и
    обе - ровно по форме самого короткого нарушения:

    - `from . import сосед` оставляет node.module = None, и условие
      `and node.module` выбрасывало такой узел целиком: импорт не попадал
      никуда, и ни одно правило поверх не могло его увидеть;
    - `from .сосед import X` записывался как голое "сосед", без пакета, так
      что правило вида name.startswith("duo_input.") или ("PySide6") на
      относительный импорт не срабатывало в принципе.

    Абсолютные имена дают всем правилам одно пространство имён - и делают
    возможным обход по графу (см. _transitive_imports).
    """
    tree = ast.parse(path.read_text("utf-8"))
    package = _package_parts(path)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # level=1 - текущий пакет, level=2 - на один выше, и так далее.
                base = package[: len(package) - (node.level - 1)]
            else:
                base = []
            target = [*base, node.module] if node.module else base
            if target:
                names.add(".".join(target))
            # `from pkg import mod` и `from mod import symbol` в AST
            # неразличимы, поэтому имя пишется и в уточнённом виде тоже.
            # Лишняя строка "duo_input.transfer.model.TransferEntry" ни
            # одному правилу не мешает, а пропущенный
            # "duo_input.transfer.windows_files" - мешает.
            names.update(".".join([*target, alias.name]) for alias in node.names)
    return names


def _module_source(name: str, root: Path) -> Path | None:
    """Файл модуля с таким абсолютным именем под root, если он там есть."""
    base = root.joinpath(*name.split("."))
    package_init = base / "__init__.py"
    if package_init.exists():
        return package_init
    module = base.with_suffix(".py")
    return module if module.exists() else None


def _transitive_imports(path: Path, root: Path, package: str) -> set[str]:
    """Всё, до чего модуль дотягивается через импорты внутри своего пакета.

    Обход идёт только по модулям пакета ``package`` (чужие библиотеки не
    разбираются - их имена просто записываются), файлы запоминаются, так что
    цикл импортов заканчивает обход, а не подвешивает его.
    """
    names: set[str] = set()
    seen: set[Path] = {path}
    queue = [path]
    while queue:
        current = queue.pop()
        for name in _imported_modules(current):
            names.add(name)
            if name != package and not name.startswith(package + "."):
                continue
            source = _module_source(name, root)
            if source is not None and source not in seen:
                seen.add(source)
                queue.append(source)
    return names


def _has_native_import(path: Path) -> bool:
    return any(
        name.split(".")[0] in _NATIVE_PREFIXES for name in _imported_modules(path)
    )


def test_the_clipboard_package_never_imports_qtwidgets():
    offenders = {
        path.name
        for path in _package_modules()
        if any(name.startswith("PySide6.QtWidgets") for name in _imported_modules(path))
    }

    assert offenders == set(), (
        "clipboard/ должен зависеть только от QtCore и QtNetwork, иначе его "
        "нельзя будет вынести в отдельный процесс вместе с передачей файлов"
    )


def test_the_clipboard_package_never_imports_the_ui():
    offenders = {
        path.name
        for path in _package_modules()
        if any(name.startswith("duo_input.ui") for name in _imported_modules(path))
    }

    assert offenders == set()


def test_only_macos_pasteboard_touches_pyobjc():
    offenders = {
        path.name
        for path in _package_modules()
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


# ======================================================== сам разборщик импортов
#
# Каждое правило в этом файле стоит на _imported_modules. Пока разборщик врёт,
# правило зелено ровно постольку, поскольку нарушения написаны в той форме,
# которую он умеет видеть, - а это не свойство кода, а совпадение.

FIXTURES_ROOT = Path(__file__).resolve().parent
FIXTURES = FIXTURES_ROOT / "boundary_fixtures"


def test_a_bare_relative_import_of_a_sibling_is_recorded_at_all():
    # `from . import qt_sibling` оставляет node.module = None. Разборщик,
    # который отбрасывает такой узел, не видит самой короткой дороги из
    # модуля к соседу - и ни одно правило поверх него эту дорогу не закроет.
    imported = _imported_modules(FIXTURES / "bare_relative.py")

    assert "boundary_fixtures.qt_sibling" in imported, (
        "относительный импорт вида `from . import сосед` потерян разборщиком"
    )


def test_a_named_relative_import_keeps_its_package():
    # `from .qt_sibling import X` даёт node.module = "qt_sibling" - голое имя
    # без пакета. Правило вида name.startswith("duo_input.") на нём не
    # сработает никогда.
    imported = _imported_modules(FIXTURES / "named_relative.py")

    assert "boundary_fixtures.qt_sibling" in imported, (
        f"относительный импорт записан без пакета: {sorted(imported)}"
    )


def test_a_parent_relative_import_climbs_exactly_one_package():
    imported = _imported_modules(FIXTURES / "nested" / "parent_relative.py")

    assert "boundary_fixtures.qt_sibling" in imported, (
        f"`from ..сосед import X` разрешён неверно: {sorted(imported)}"
    )


def test_an_absolute_import_is_left_exactly_as_written():
    # Плюс уточнённое имя: `from PySide6.QtWidgets import QWidget` в AST
    # неотличим от импорта подмодуля, и обе формы попадают в набор.
    imported = _imported_modules(FIXTURES / "qt_sibling.py")

    assert imported == {"PySide6.QtWidgets", "PySide6.QtWidgets.QWidget"}


def test_the_transitive_sweep_sees_qt_reached_through_a_sibling():
    # Дорога, ради которой всё это: модуль сам Qt не импортирует, а тянет
    # соседа, который импортирует. Проверка на один уровень тут зелена.
    one_level = _imported_modules(FIXTURES / "bare_relative.py")
    assert not any(name.startswith("PySide6") for name in one_level), (
        "фикстура должна доставать Qt только через соседа, иначе она ничего "
        "не доказывает про транзитивность"
    )

    reachable = _transitive_imports(
        FIXTURES / "bare_relative.py", FIXTURES_ROOT, "boundary_fixtures"
    )

    assert any(name.startswith("PySide6") for name in reachable), (
        "обход не пошёл по относительному импорту - правило для COM-модуля "
        "проверяет только первый уровень и молчит про остальные"
    )


def test_the_transitive_sweep_survives_a_cycle():
    # Пакет duo_input циклов сейчас не содержит, но обход, который на них
    # виснет, - это тест, который однажды не покраснеет, а зависнет.
    reachable = _transitive_imports(
        FIXTURES / "cycle_a.py", FIXTURES_ROOT, "boundary_fixtures"
    )

    assert "boundary_fixtures.cycle_b" in reachable
