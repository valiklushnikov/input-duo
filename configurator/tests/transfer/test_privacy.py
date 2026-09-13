"""Спека §15: байты файлов - никогда, полные пути - нигде.

Это требование, которое соблюдают все модули в день написания и нарушает один
модуль через шесть недель, во время отладки. Тест делает нарушение видимым
сразу, а не на следующем ревью.
"""

from __future__ import annotations

import logging
import os

import pytest

from duo_input.clipboard.wire import CAPABILITY_CLIPBOARD, CAPABILITY_FILES, Message, MessageType
from duo_input.transfer.paths import UnsafePath
from duo_input.transfer.scanner import scan
from duo_input.transfer.service import FileTransferService
from duo_input.transfer.source import REASON_SOURCE_MISSING, SnapshotRegistry

SECRET = b"SUPER-SECRET-FILE-CONTENTS-0123456789"
CAPS = frozenset({CAPABILITY_CLIPBOARD, CAPABILITY_FILES})


class _Link:
    """Минимальная связь: журнал проверяется, сеть здесь не нужна."""

    def __init__(self) -> None:
        self.sent: list[Message] = []
        self.disconnected = _Signal()

    def send(self, message: Message) -> None:
        self.sent.append(message)


class _Signal:
    def connect(self, _slot) -> None: ...


def test_scanning_never_logs_a_full_path(caplog, tmp_path):
    # На Windows os.chmod(dir, 0o000) не мешает os.listdir - директория
    # читается как ни в чём не бывало, скрытых записей нет, лог пуст, и
    # `str(tmp_path) not in logged` проходит против пустой строки, ничего не
    # доказывая. Несуществующий путь ловит тот же logger.warning в _walk и
    # действительно оставляет запись, которую есть смысл проверять (см.
    # ruling 1 в брифе задачи 1.15).
    caplog.set_level(logging.DEBUG)
    missing = tmp_path / "нельзя-найти"

    with pytest.raises(UnsafePath):
        scan([missing], transfer_id="t")

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert logged, "путь не читается - в журнале должна остаться хоть одна запись"
    assert str(tmp_path) not in logged, (
        "полный путь попал в журнал - он раскрывает структуру диска "
        "пользователя, а для диагностики достаточно имени"
    )


def test_reading_a_file_never_logs_its_bytes(caplog, tmp_path):
    caplog.set_level(logging.DEBUG)
    source = tmp_path / "secret.bin"
    source.write_bytes(SECRET)
    manifest, sources = scan([source], transfer_id="t")
    registry = SnapshotRegistry()
    registry.publish(manifest, sources)

    payload = registry.read("t", 0, 0, len(SECRET))

    assert payload == SECRET
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert b"SUPER-SECRET" not in logged.encode()
    assert "SUPER-SECRET" not in logged


def test_a_source_that_vanished_is_reported_by_name_not_by_path(caplog, qapp, tmp_path):
    # §15 обещает свойство ЖУРНАЛА, а журнал пишется на уровне сервиса
    # (service.py: `except (SourceMissing, OSError) ... logger.warning(...)`
    # ), а не в самом SnapshotRegistry - тот только кидает исключение и
    # ничего не логирует. Тест, который дёргает registry.read() напрямую,
    # не проходит мимо сервиса, а обходит его целиком: он ничего не может
    # сказать про то, что попадёт в файл журнала. Поэтому здесь - реальный
    # путь: offer -> удаление файла -> FILE_READ через handle_message, тот
    # же путь, которым идёт настоящий Проводник.
    caplog.set_level(logging.DEBUG)
    service = FileTransferService()
    link = _Link()
    service.attach_link(link)
    service.set_peer_capabilities(CAPS)
    source = tmp_path / "gone.bin"
    source.write_bytes(SECRET)
    transfer_id = service.offer_local_files([source])
    os.remove(source)

    service.handle_message(
        Message(
            MessageType.FILE_READ,
            {"transfer_id": transfer_id, "entry_index": 0, "offset": 0, "length": 4},
            b"",
        )
    )

    assert link.sent, "пропавший источник должен породить FILE_ERROR, а не тишину"
    error = link.sent[-1]
    assert error.type is MessageType.FILE_ERROR
    assert error.header["reason"] == REASON_SOURCE_MISSING, (
        "это доказывает, что сработал именно путь source_missing, а не "
        "какая-то более ранняя ошибка"
    )

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert logged, "пропавший источник должен оставить диагностику - без неё нечего проверять"
    assert str(tmp_path) not in logged


def test_a_rejected_manifest_is_logged_without_the_offending_path_in_full(
    caplog, qapp
):
    caplog.set_level(logging.DEBUG)
    service = FileTransferService()

    service.handle_message(
        Message(
            MessageType.FILE_OFFER,
            {
                "transfer_id": "t",
                "entries": [
                    {
                        "path": "..\\\\..\\\\Users\\\\victim\\\\secret.docx",
                        "kind": "file",
                        "size": 1,
                        "mtime_ns": 1,
                    }
                ],
            },
            b"",
        )
    )

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert logged, "отвергнутое объявление не оставило следа вовсе - это тоже плохо"
    assert "victim" not in logged, (
        "путь враждебного пира попал в журнал целиком; в нём может быть что "
        "угодно, включая то, что не должно оказаться в файле журнала"
    )


def test_a_chunk_is_never_logged_even_at_debug_level(caplog, qapp, tmp_path):
    caplog.set_level(logging.DEBUG)
    service = FileTransferService()
    link = _Link()
    service.attach_link(link)
    service.set_peer_capabilities(CAPS)
    source = tmp_path / "secret.bin"
    source.write_bytes(SECRET)
    transfer_id = service.offer_local_files([source])

    service.handle_message(
        Message(
            MessageType.FILE_READ,
            {
                "transfer_id": transfer_id,
                "entry_index": 0,
                "offset": 0,
                "length": len(SECRET),
            },
            b"",
        )
    )

    assert link.sent[-1].blob == SECRET
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "SUPER-SECRET" not in logged
