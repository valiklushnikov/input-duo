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
]


def _imported_module_names(module_name: str) -> set[str]:
    """Имена модулей из import/from-import верхнего уровня, без исполнения."""
    spec = importlib.util.find_spec(module_name)
    assert spec is not None and spec.origin is not None, (
        f"не удалось найти исходник модуля {module_name}"
    )
    source = Path(spec.origin).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=spec.origin)

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


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
