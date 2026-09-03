r"""Запуск вместе с системой - ярлыком, а не ключом реестра.

Ярлык в папке автозагрузки виден в диспетчере задач и удаляется руками. Ключ в
реестре не виден никому, кроме того, кто знает, где искать. Программа, которая
незаметно прописывается в автозапуск, выглядит ровно как то, чего пользователей
учат опасаться, - а прав администратора не требует ни то, ни другое.
"""

from __future__ import annotations

import os
from pathlib import Path

SHORTCUT_NAME = "Duo Input.cmd"

#: Признак скрытого старта - точка входа обязана прочитать его и не
#: показывать окно (§4: "При автозапуске окно не показывается"). Единственное
#: назначение этого ярлыка - автозапуск, поэтому флаг вписан в него всегда, а
#: не по отдельному параметру.
HIDDEN_START_ARGUMENT = "--hidden"


def startup_directory() -> Path:
    r"""Папка автозагрузки текущего пользователя."""
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path() -> Path:
    return startup_directory() / SHORTCUT_NAME


def is_enabled() -> bool:
    return shortcut_path().is_file()


def enable(target: Path) -> None:
    """Создать запись автозагрузки, запускающую программу скрытой.

    Раньше запись запускала программу вовсе без флагов, а точка входа
    безусловно показывала окно - автозапуск и "не показывать окно" были не
    связаны между собой. Флаг дописан прямо в команду ярлыка: это
    единственное место, где решается, как стартует программа из автозагрузки.
    """
    path = shortcut_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'@start "" "{target}" {HIDDEN_START_ARGUMENT}\n', "utf-8")


def disable() -> None:
    shortcut_path().unlink(missing_ok=True)


__all__ = [
    "SHORTCUT_NAME",
    "disable",
    "enable",
    "is_enabled",
    "shortcut_path",
    "startup_directory",
]
