"""Спека §15: байты файлов - никогда, полные пути - нигде.

Это требование, которое соблюдают все модули в день написания и нарушает один
модуль через шесть недель, во время отладки. Тест делает нарушение видимым
сразу, а не на следующем ревью.
"""

from __future__ import annotations

import logging
import os
import sys

import pytest

from duo_input.clipboard.wire import CAPABILITY_CLIPBOARD, CAPABILITY_FILES, Message, MessageType
from duo_input.transfer.paths import UnsafePath
from duo_input.transfer.scanner import scan
from duo_input.transfer.service import FileTransferService
from duo_input.transfer.source import REASON_SOURCE_MISSING, SnapshotRegistry

SECRET = b"SUPER-SECRET-FILE-CONTENTS-0123456789"
CAPS = frozenset({CAPABILITY_CLIPBOARD, CAPABILITY_FILES})

#: E_OUTOFMEMORY: отказ OleSetClipboard по существу, а не занятость буфера.
E_OUTOFMEMORY = 0x8007000E - (1 << 32)


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


# ============================================ §15 над COM-модулями (задачи 2.1-2.5)
#
# Задача 1.15 написала этот файл, когда windows_com.py и windows_files.py ещё
# не существовали, - и §15 их не покрывала. Оба логируют: отказ в публикации,
# код возврата OleFlushClipboard, потолок гашения апартамента, причину
# PipeClosed. Каждая из этих записей - место, куда однажды допишут "а какой
# файл-то?".
#
# Каждая проверка ниже сперва утверждает, что нужная запись в журнале ВООБЩЕ
# есть: "полного пути не нашли" над пустым журналом не доказывает ничего.

WINDOWS_ONLY = pytest.mark.skipif(
    sys.platform != "win32", reason="COM-модули существуют только на Windows"
)

#: Имя внутри объявления. Оно относительное по построению, но приехало оно от
#: пира, и дописать в лог проще всего именно его.
SECRET_NAME = "квартальная-премия.xlsx"


def _secret_manifest(transfer_id: str = "t-1"):
    from duo_input.transfer.model import ENTRY_FILE, TransferEntry, TransferManifest

    return TransferManifest(
        transfer_id=transfer_id,
        entries=(
            TransferEntry(
                path=SECRET_NAME, kind=ENTRY_FILE, size=len(SECRET), mtime_ns=1
            ),
        ),
    )


def _logged(caplog) -> str:
    """Сообщения и трассировки: logger.exception прячет половину во второй."""
    return "\n".join(
        record.getMessage() + "\n" + (record.exc_text or "")
        for record in caplog.records
    )


@WINDOWS_ONLY
def test_a_com_stream_never_logs_the_bytes_or_the_reason_it_was_closed(caplog):
    from duo_input.transfer.pipe import ChunkPipe
    from duo_input.transfer.windows_com import (
        S_OK,
        STG_E_READFAULT,
        PipeStream,
        call_stream_read,
    )

    caplog.set_level(logging.DEBUG)
    pipe = ChunkPipe(capacity_chunks=1)
    stream = PipeStream(
        pipe, size=len(SECRET), request=lambda *_args: None, timeout=0.05
    )
    pipe.push(SECRET)
    payload, result = call_stream_read(stream.pointer, len(SECRET))

    assert (payload, result) == (SECRET, S_OK), "чтение должно было пройти насквозь"

    # Закрытая труба - вторая ветка с записью в журнале. Причина нарочно
    # несёт и путь, и байты: это ровно то, что попадёт в журнал, если
    # "поток закрыт" однажды станет "поток закрыт: %s".
    stream.position = 0
    pipe.close("обрыв на C:\\Users\\victim\\" + SECRET_NAME + ": " + SECRET.decode())
    _, closed = call_stream_read(stream.pointer, len(SECRET))

    assert closed == STG_E_READFAULT
    logged = _logged(caplog)
    assert "поток закрыт" in logged, (
        "закрытая труба обязана оставить запись - без неё проверки ниже "
        "смотрят в пустоту"
    )
    assert "SUPER-SECRET" not in logged
    assert SECRET_NAME not in logged
    assert "victim" not in logged


@WINDOWS_ONLY
def test_a_com_stream_that_timed_out_logs_the_deadline_and_nothing_else(caplog):
    from duo_input.transfer.pipe import ChunkPipe
    from duo_input.transfer.windows_com import (
        STG_E_READFAULT,
        PipeStream,
        call_stream_read,
    )

    caplog.set_level(logging.DEBUG)
    stream = PipeStream(
        ChunkPipe(capacity_chunks=1),
        size=len(SECRET),
        request=lambda *_args: None,
        timeout=0.05,
    )

    _, result = call_stream_read(stream.pointer, len(SECRET))

    assert result == STG_E_READFAULT
    logged = _logged(caplog)
    assert "чанк не пришёл" in logged, "истёкший срок обязан оставить предупреждение"
    assert "SUPER-SECRET" not in logged


@WINDOWS_ONLY
def test_an_open_pipe_that_failed_is_logged_by_type_not_by_message(caplog, tmp_path):
    # Владелец узнаёт причину из last_open_error; в журнал идёт только имя
    # класса. Сообщение исключения приходит из чужого кода и вполне может
    # нести полный путь - как здесь.
    from duo_input.transfer.windows_com import (
        E_FAIL,
        TYMED_ISTREAM,
        call_get_data_medium,
        register_clipboard_format,
    )
    from duo_input.transfer.windows_files import (
        FORMAT_CONTENTS_NAME,
        VirtualFilesDataObject,
    )

    caplog.set_level(logging.DEBUG)
    secret_path = tmp_path / SECRET_NAME

    def open_pipe(_transfer_id, _entry_index):
        raise RuntimeError("нет источника " + str(secret_path))

    data_object = VirtualFilesDataObject(
        _secret_manifest(),
        open_pipe=open_pipe,
        request_read=lambda *_args: None,
        close_pipe=lambda *_args: None,
        origin_marker=b"origin:1",
    )

    result, _medium = call_get_data_medium(
        data_object.pointer,
        register_clipboard_format(FORMAT_CONTENTS_NAME),
        0,
        TYMED_ISTREAM,
    )

    assert result == E_FAIL
    assert isinstance(data_object.last_open_error, RuntimeError)
    logged = _logged(caplog)
    assert "RuntimeError" in logged, (
        "отказ open_pipe обязан оставить запись с типом отказа - иначе "
        "проверки ниже смотрят на чужую запись"
    )
    assert str(tmp_path) not in logged
    assert SECRET_NAME not in logged


@WINDOWS_ONLY
def test_a_refused_publication_logs_the_reason_not_the_tree(caplog, qapp):
    from duo_input.transfer.windows_files import WindowsFileClipboardBackend

    caplog.set_level(logging.DEBUG)
    backend = WindowsFileClipboardBackend()
    refusals: list[str] = []
    backend.publish_failed.connect(refusals.append)

    backend.publish(_secret_manifest(), origin_marker=b"origin:1")

    assert refusals, "публикация без потока STA обязана быть отвергнута вслух"
    logged = _logged(caplog)
    assert "публикация отклонена" in logged, "отказ обязан оставить запись"
    assert SECRET_NAME not in logged, (
        "имя из объявления попало в журнал; оно пришло от пира, и в нём может "
        "быть что угодно"
    )


@WINDOWS_ONLY
def test_a_failing_publication_logs_the_hresult_not_the_tree(
    caplog, qapp, qtbot, monkeypatch
):
    # Единственная запись здесь - logger.exception, то есть в журнал уходит и
    # трассировка. Если дерево попадёт в неё, §15 нарушена именно тут.
    from duo_input.transfer import windows_files
    from duo_input.transfer.windows_files import WindowsFileClipboardBackend

    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(
        windows_files, "_ole_set_clipboard", lambda _pointer: E_OUTOFMEMORY
    )
    backend = WindowsFileClipboardBackend()
    backend.set_callbacks(
        lambda *_args: None,
        lambda *_args: None,
        lambda *_args: None,
        lambda *_args: None,
    )
    try:
        backend.start()
        qtbot.waitUntil(lambda: backend.thread_id is not None, timeout=5000)

        with qtbot.waitSignal(backend.publish_failed, timeout=5000) as blocker:
            backend.publish(_secret_manifest(), origin_marker=b"origin:1")
    finally:
        backend.stop()

    assert "8007000E" in blocker.args[0].upper()
    logged = _logged(caplog)
    assert "не удалось опубликовать" in logged, (
        "провалившаяся публикация обязана оставить запись"
    )
    assert SECRET_NAME not in logged
    assert "SUPER-SECRET" not in logged


@WINDOWS_ONLY
def test_a_failing_apartment_is_logged_by_type_not_by_message(
    caplog, qapp, qtbot, monkeypatch, tmp_path
):
    # Пара к open_pipe выше, и по той же причине: причина отказа приходит
    # из чужого кода, а в журнал идёт её тип. Две соседние записи, одна из
    # которых печатает тип, а другая текст, - это приглашение скопировать
    # не ту.
    #
    # Проверяются сообщения записей, без трассировок: рядом стоит
    # logger.exception("OleInitialize отказал..."), и трассировка в нём -
    # сознательная диагностика отказа апартамента. Текст в ней пишет
    # Windows (OleInitialize отдаёт HRESULT), а ни дерева, ни пути в
    # области видимости того except нет вовсе.
    from duo_input.transfer import windows_files
    from duo_input.transfer.windows_files import WindowsFileClipboardBackend

    caplog.set_level(logging.ERROR)
    secret_path = tmp_path / SECRET_NAME

    def refuse() -> None:
        raise OSError("апартамент занят " + str(secret_path))

    monkeypatch.setattr(windows_files, "_ole_initialize", refuse)
    backend = WindowsFileClipboardBackend()
    try:
        backend.start()
        qtbot.waitUntil(lambda: not backend.is_running, timeout=5000)
    finally:
        backend.stop()

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "апартамент STA не поднялся: OSError" in messages, (
        "неподнявшийся апартамент обязан оставить запись с типом отказа - "
        "иначе проверки ниже смотрят на чужую запись"
    )
    assert SECRET_NAME not in messages
    assert str(tmp_path) not in messages
