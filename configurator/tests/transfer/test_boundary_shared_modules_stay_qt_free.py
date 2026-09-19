"""Граница COM/Qt: общие модули не видят Qt - проверено, а не обещано.

model.py, paths.py и pipe.py живут на обеих сторонах барьера: COM-поток
может импортировать любой из них так же свободно, как GUI-поток. Если один
из них потянет за собой PySide6 (или ``duo_input.transfer.windows_*``, где у
нас живёт Qt-обвязка транспорта), COM-поток однажды утащит QSslSocket за
компанию - и это не всплывёт на ревью, потому что на день написания правило
все соблюдают. Оно всплывёт через шесть недель, в разгар отладки.

Раньше это правило было только прозой в docstring'ах. Здесь оно становится
тестом: разбирается исходный код модуля через ``ast`` (без исполнения, чтобы
факт наличия PySide6 в окружении не маскировал нарушение и не создавал его),
и проверяются только настоящие операторы import - не слово "PySide6" в
комментарии или docstring.

Список модулей ниже - единственное место, которое трогает Task 1.5, когда
добавляет scanner.py: одна строка.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

# Каждый новый "общий" модуль (по обе стороны барьера COM/Qt) дописывается
# сюда одной строкой.
SHARED_MODULES = [
    "duo_input.transfer.model",
    "duo_input.transfer.paths",
    "duo_input.transfer.pipe",
    "duo_input.transfer.scanner",
    "duo_input.transfer.fileprovider_replica",
    # windows_com живёт не по обе стороны барьера, а прямо на COM-стороне, и
    # запрет на Qt для него не мягче, а строже: его код исполняется В
    # COM-потоке. Канонический boundary-тест для COM-модулей - задача 2.6;
    # здесь модуль стоит с первого дня, чтобы запрет работал уже сейчас.
    "duo_input.transfer.windows_com",
    # windows_files сюда НЕ входит, и это решение, а не упущение: в нём
    # живёт WindowsFileClipboardBackend(QObject), то есть Qt он видит по
    # замыслу. Настоящий инвариант - не "модуль не импортирует Qt", а "код,
    # исполняемый в COM-потоке, не трогает Qt напрямую", и держит его
    # post_to_service, единственная дорога обратно, а не запрет на импорт.
]


def _imported_names_from_source(
    source: str, package: str, filename: str = "<source>"
) -> set[str]:
    """Имена импортируемых модулей, приведённые к абсолютному виду.

    ``package`` - пакет, в котором лежит разбираемый модуль; относительно
    него разрешается node.level. Без этого правило ниже не видело ни
    ``from . import windows_files`` (node.module = None, узел выбрасывался
    целиком), ни ``from .windows_com import S_OK`` (записывалось голое имя,
    без пакета - а проверка ищет префикс "duo_input.transfer.windows_").
    """
    tree = ast.parse(source, filename=filename)
    parts = package.split(".") if package else []

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # Выше корня пакета подниматься некуда, а срез без пола
                # ушёл бы в минус и вернул правдоподобное, но неверное имя.
                assert node.level <= len(parts), (
                    f"{filename}: относительный импорт уровня {node.level} "
                    f"выходит за корень пакета {package or '<вне пакета>'}"
                )
                base = parts[: max(0, len(parts) - (node.level - 1))]
            else:
                base = []
            target = [*base, node.module] if node.module else base
            if target:
                names.add(".".join(target))
            # `from pkg import mod` и `from mod import symbol` в AST
            # неразличимы, поэтому имя записывается и в уточнённом виде:
            # лишняя строка вида "...model.TransferEntry" никакому правилу
            # не мешает, а пропущенный "...transfer.windows_files" - мешает.
            names.update(".".join([*target, alias.name]) for alias in node.names)
    return names


def _imported_module_names(module_name: str) -> set[str]:
    """Имена модулей из import/from-import верхнего уровня, без исполнения."""
    spec = importlib.util.find_spec(module_name)
    assert spec is not None and spec.origin is not None, (
        f"не удалось найти исходник модуля {module_name}"
    )
    source = Path(spec.origin).read_text(encoding="utf-8")
    # spec.parent - пакет модуля (для самого пакета это он сам), то есть
    # ровно то, относительно чего Python считает точки в импорте.
    return _imported_names_from_source(source, spec.parent or "", spec.origin)


def _crosses_the_boundary(imported_module: str) -> bool:
    if imported_module == "PySide6" or imported_module.startswith("PySide6."):
        return True
    if imported_module.startswith("duo_input.transfer.windows_"):
        return True
    return False


@pytest.mark.parametrize("module_name", SHARED_MODULES)
def test_shared_module_imports_neither_qt_nor_the_windows_transport(module_name):
    imported = _imported_module_names(module_name)
    offenders = sorted(name for name in imported if _crosses_the_boundary(name))

    assert not offenders, (
        f"{module_name} импортирует {offenders} - это ломает границу COM/Qt: "
        "этот модуль обязан оставаться пригодным для импорта из COM-потока "
        "без Qt-рантайма"
    )


# Разбор относительных импортов: та же дыра, что была в
# clipboard/test_boundaries.py, и в том же правиле она значила то же самое.
# `from . import windows_files` не попадал в набор вовсе (node.module = None),
# а `from .windows_com import S_OK` записывался голым "windows_com" - и
# _crosses_the_boundary, которая ищет префикс "duo_input.transfer.windows_",
# не срабатывала ни на одном, ни на другом.
_RELATIVE_FORMS = (
    "from . import windows_files\n"
    "from .windows_com import S_OK\n"
    "from ..clipboard import wire\n"
)


def test_the_parser_resolves_relative_imports_to_absolute_names():
    names = _imported_names_from_source(_RELATIVE_FORMS, "duo_input.transfer")

    assert names == {
        "duo_input.transfer",
        "duo_input.transfer.windows_files",
        "duo_input.transfer.windows_com",
        "duo_input.transfer.windows_com.S_OK",
        "duo_input.clipboard",
        "duo_input.clipboard.wire",
    }


def test_a_relative_import_of_the_windows_transport_crosses_the_boundary():
    names = _imported_names_from_source(
        "from . import windows_files\n", "duo_input.transfer"
    )

    assert sorted(name for name in names if _crosses_the_boundary(name)) == [
        "duo_input.transfer.windows_files"
    ], "самая короткая дорога из общего модуля в Qt-обвязку осталась незамеченной"


def test_a_relative_import_above_the_package_root_fails_loudly():
    # Не достижимо из импортируемого исходника, но разрешать такой импорт в
    # правдоподобное неверное имя значит проверить правилом не тот модуль и
    # остаться зелёным - ровно тот молчаливый промах, который эта функция и
    # существует, чтобы убрать.
    with pytest.raises(AssertionError, match="выходит за корень пакета"):
        _imported_names_from_source("from ... import x", "duo_input.transfer")
