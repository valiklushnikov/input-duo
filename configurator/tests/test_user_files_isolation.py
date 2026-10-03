"""Тесты не пишут в журнал и crash.log настоящего пользователя.

Настоящий %LOCALAPPDATA%\\DuoInput\\logs\\duo-input.log содержал
``ERROR duo_input.app общий буфер не запустился`` с трейсбеком из ``_boom``
тестов test_runtime_wiring.py (2026-09-27 22:37:15): main() в тестах звал
configure_logging() с настоящим LOCALAPPDATA, а обработчик оставался
подключённым к логгеру ``duo_input`` до конца сессии - и каждый следующий
тест дописывал туда свои строки.

Каждый тест здесь самодостаточен: он сам снимает охрану (``release()``) и
проверяет результат, а не надеется на порядок запуска.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import subprocess
import sys
from pathlib import Path

from duo_input.persistence import locations

#: Каталог, который тесты обязаны не трогать. Снят при импорте, до того как
#: фикстура подменила переменную на время теста.
REAL_LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")

TESTS = Path(__file__).resolve().parent
SOURCE = TESTS.parent / "src"


def _file_handlers() -> list[logging.Handler]:
    handlers = logging.getLogger(locations.LOGGER_NAME).handlers
    return [h for h in handlers if isinstance(h, logging.handlers.RotatingFileHandler)]


def test_the_application_directory_is_not_the_users(tmp_path_factory):
    directory = locations.application_directory()

    assert REAL_LOCALAPPDATA, "LOCALAPPDATA должен быть задан на этой машине"
    # Не сам LOCALAPPDATA: временный каталог pytest тоже лежит под ним.
    real = Path(REAL_LOCALAPPDATA) / locations.APPLICATION_DIRECTORY_NAME
    assert not directory.is_relative_to(real)
    assert directory.is_relative_to(tmp_path_factory.getbasetemp())


def test_the_guard_removes_the_application_log_a_test_started(isolated_user_files):
    path = locations.configure_logging()
    assert path.is_file()
    assert _file_handlers() != []

    isolated_user_files.release()

    assert _file_handlers() == []


def test_the_guard_closes_the_crash_log_a_test_started(isolated_user_files):
    path = locations.enable_crash_log()

    isolated_user_files.release()

    # Открытый дескриптор не даёт удалить файл на Windows - и не дал бы
    # pytest убрать свой временный каталог.
    path.unlink()


#: Первый тест включает crash.log, второй падает нативно. Отчёт о втором
#: обязан дойти до настоящего stderr pytest, а не в файл перехвата вывода.
_CRASHING_SUITE = '''
import faulthandler
from duo_input.persistence.locations import enable_crash_log


def test_enables_the_crash_log():
    enable_crash_log()


def test_dies_natively():
    faulthandler._sigsegv()
'''


def test_after_the_guard_a_crash_is_still_reported_on_pytests_stderr(tmp_path):
    (tmp_path / "conftest.py").write_text(
        f"import sys\nsys.path.insert(0, {str(TESTS)!r})\n"
        "from user_files_guard import _isolated_localappdata, isolated_user_files  # noqa\n",
        encoding="utf-8",
    )
    (tmp_path / "test_crash.py").write_text(_CRASHING_SUITE, encoding="utf-8")
    environment = dict(os.environ, PYTHONPATH=str(SOURCE))

    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(tmp_path), "-q", "-p", "no:cacheprovider",
         "--rootdir", str(tmp_path)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        timeout=120,
    )

    assert result.returncode != 0
    stderr = result.stderr.decode(errors="replace")
    assert "Fatal Python error" in stderr or "Windows fatal exception" in stderr
    assert "test_dies_natively" in stderr
