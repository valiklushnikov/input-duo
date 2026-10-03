"""Тесты не пишут в журнал и crash.log настоящего пользователя.

Настоящий %LOCALAPPDATA%\\DuoInput\\logs\\duo-input.log содержал
``ERROR duo_input.app общий буфер не запустился`` с трейсбеком из ``_boom``
тестов test_runtime_wiring.py (2026-09-27 22:37:15): main() в тестах звал
configure_logging() с настоящим LOCALAPPDATA, а обработчик оставался
подключённым к логгеру ``duo_input`` до конца сессии - и каждый следующий
тест дописывал туда свои строки. В журнале, по которому разбирают падения
программы, это неотличимо от работы программы.

Два теста ниже идут в этом порядке намеренно: первый подключает журнал,
второй проверяет, что подключение не пережило теста.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

from duo_input.persistence import locations

#: Каталог, который тесты обязаны не трогать. Снят при импорте, до того как
#: фикстура conftest подменила переменную на время теста.
REAL_LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")


def test_the_application_directory_is_not_the_users(tmp_path_factory):
    directory = locations.application_directory()

    assert REAL_LOCALAPPDATA, "conftest должен запомнить настоящий LOCALAPPDATA"
    # Не сам LOCALAPPDATA: временный каталог pytest тоже лежит под ним.
    assert not directory.is_relative_to(Path(REAL_LOCALAPPDATA) / locations.APPLICATION_DIRECTORY_NAME)
    assert directory.is_relative_to(tmp_path_factory.getbasetemp())


def test_a_test_may_start_the_application_log():
    path = locations.configure_logging()

    assert path.is_file()


def test_the_application_log_did_not_outlive_the_previous_test():
    handlers = logging.getLogger(locations.LOGGER_NAME).handlers

    assert [h for h in handlers if isinstance(h, logging.handlers.RotatingFileHandler)] == []


def test_a_test_may_start_the_crash_log():
    path = locations.enable_crash_log()

    assert path.is_file()


def test_the_crash_log_did_not_outlive_the_previous_test():
    # Открытый дескриптор не даёт удалить файл на Windows - и не дал бы
    # pytest убрать свой временный каталог.
    locations.crash_log_path().unlink()
