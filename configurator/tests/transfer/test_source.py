"""Удерживаемый дескриптор с обнаружением изменений - не "неизменяемый снимок".

Измерено на целевой платформе (спека §2, факт 7): пока мы держим дескриптор,
другой процесс МОЖЕТ писать и усекать файл, но НЕ МОЖЕТ удалить или
переименовать его. Отсюда обе половины этих тестов - и то, что мы защищаем, и
то, чего мы не обещаем.
"""

from __future__ import annotations

import os
import sys
import time

import pytest

from duo_input.transfer.model import (
    ENTRY_DIRECTORY,
    ENTRY_FILE,
    TransferEntry,
    TransferManifest,
)
from duo_input.transfer.source import (
    RETENTION,
    SnapshotRegistry,
    SourceChanged,
    SourceMissing,
)


def _publish(registry, tmp_path, transfer_id="t-1", name="a.bin", payload=b"0123456789"):
    source = tmp_path / name
    source.write_bytes(payload)
    entry = TransferEntry(
        path=name,
        kind=ENTRY_FILE,
        size=len(payload),
        mtime_ns=os.stat(source).st_mtime_ns,
    )
    manifest = TransferManifest(transfer_id=transfer_id, entries=(entry,))
    registry.publish(manifest, {name: source})
    return source


def _write_until_mtime_moves(source, baseline_ns, *, payload=b"X", timeout=2.0):
    """Ждать, пока запись реально сдвинет st_mtime_ns - не спать фиксированный срок.

    Метки времени файловой системы дискретны (спека §10, дополнение по задаче
    1.9): запись, попавшая в тот же тик, что и baseline, неотличима от него по
    st_mtime_ns. Измерено при диагностике этой задачи: перезапись сразу после
    первого чтения оставляла st_mtime_ns побитово идентичным в трёх подряд
    прогонах, а гранулярность часов на этой машине - около 1,2 мс. Фиксированная
    пауза была бы ставкой на гранулярность чужого диска и превратилась бы в
    флейк там, где тик грубее (Windows временами тикает раз в ~15,6 мс) - не
    "упрощай обратно в sleep", а жди условие с ограничением по времени.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with open(source, "r+b") as handle:
            handle.write(payload)
        if os.stat(source).st_mtime_ns != baseline_ns:
            return
    pytest.fail(
        "часы файловой системы не сдвинулись за отведённое время - "
        "тест не может ничего утверждать о свежести mtime"
    )


def test_reading_an_offset_range_returns_exactly_those_bytes(tmp_path):
    registry = SnapshotRegistry()
    _publish(registry, tmp_path)

    assert registry.read("t-1", 0, 3, 4) == b"3456"


def test_reads_are_idempotent_so_the_same_range_twice_returns_the_same_bytes(tmp_path):
    # На этом стоит поддержка Seek и повторного Ctrl+V: чтения адресуются
    # смещением, а не позицией в потоке.
    registry = SnapshotRegistry()
    _publish(registry, tmp_path)

    assert registry.read("t-1", 0, 2, 3) == registry.read("t-1", 0, 2, 3)


def test_reads_may_arrive_out_of_order(tmp_path):
    registry = SnapshotRegistry()
    _publish(registry, tmp_path)

    tail = registry.read("t-1", 0, 8, 2)
    head = registry.read("t-1", 0, 0, 2)

    assert (head, tail) == (b"01", b"89")


def test_a_read_past_the_end_returns_only_what_is_there(tmp_path):
    registry = SnapshotRegistry()
    _publish(registry, tmp_path)

    assert registry.read("t-1", 0, 8, 100) == b"89"


def test_a_read_at_or_past_the_end_is_a_request_error(tmp_path):
    # Получатель не спрашивает за концом файла никогда: IStream отвечает на
    # такое чтение S_FALSE сам. Значит, такой запрос - ошибка пира, и 10**30
    # не должен доехать до lseek, где он поднял бы OverflowError.
    registry = SnapshotRegistry()
    _publish(registry, tmp_path)

    for offset in (10, 11, 10**30):
        with pytest.raises(ValueError):
            registry.read("t-1", 0, offset, 10)


def test_a_zero_byte_file_has_no_readable_offset(tmp_path):
    registry = SnapshotRegistry()
    _publish(registry, tmp_path, name="empty.bin", payload=b"")

    with pytest.raises(ValueError):
        registry.read("t-1", 0, 0, 10)


def test_a_read_running_past_the_end_returns_only_the_tail(tmp_path):
    registry = SnapshotRegistry()
    _publish(registry, tmp_path)

    assert len(registry.read("t-1", 0, 8, 10)) == 2


def test_the_first_read_marks_the_snapshot_as_serving(tmp_path):
    registry = SnapshotRegistry()
    _publish(registry, tmp_path)

    assert registry.serving == frozenset()

    registry.read("t-1", 0, 0, 1)

    assert registry.serving == frozenset({"t-1"})


def test_a_file_written_under_us_with_a_different_length_is_detected_immediately(tmp_path):
    # Изменение длины ловится по st_size сразу - без зависимости от
    # гранулярности часов файловой системы (см. следующий тест).
    registry = SnapshotRegistry()
    source = _publish(registry, tmp_path)
    registry.read("t-1", 0, 0, 1)

    with open(source, "r+b") as handle:
        # Оригинал - 10 байт ("0123456789"); дописываем сверх этой длины, а не
        # перезаписываем префикс тем же числом байт, иначе размер не изменится.
        handle.write(b"XXXXXXXXXXXX")

    with pytest.raises(SourceChanged):
        registry.read("t-1", 0, 0, 4)


def test_a_same_length_write_under_us_is_detected_once_the_clock_ticks(tmp_path):
    # Запись той же длины меняет только mtime, а не размер, и тикает
    # дискретно - см. _write_until_mtime_moves про то, почему это не sleep().
    registry = SnapshotRegistry()
    source = _publish(registry, tmp_path)
    registry.read("t-1", 0, 0, 1)

    baseline = os.stat(source).st_mtime_ns
    _write_until_mtime_moves(source, baseline)

    with pytest.raises(SourceChanged):
        registry.read("t-1", 0, 0, 4)


def test_a_file_truncated_under_us_is_detected_and_refused(tmp_path):
    registry = SnapshotRegistry()
    source = _publish(registry, tmp_path)
    registry.read("t-1", 0, 0, 1)

    with open(source, "r+b") as handle:
        handle.truncate(2)

    with pytest.raises(SourceChanged):
        registry.read("t-1", 0, 0, 4)


def test_a_source_that_vanished_before_the_first_read_is_reported_as_missing(tmp_path):
    registry = SnapshotRegistry()
    source = _publish(registry, tmp_path)
    source.unlink()

    with pytest.raises(SourceMissing):
        registry.read("t-1", 0, 0, 4)


def test_an_unknown_transfer_id_is_reported_as_missing_not_as_a_crash(tmp_path):
    registry = SnapshotRegistry()
    _publish(registry, tmp_path)

    with pytest.raises(SourceMissing):
        registry.read("no-such-transfer", 0, 0, 4)


def test_an_entry_index_outside_the_manifest_is_reported_as_missing(tmp_path):
    registry = SnapshotRegistry()
    _publish(registry, tmp_path)

    with pytest.raises(SourceMissing):
        registry.read("t-1", 99, 0, 4)


def test_a_directory_entry_is_not_read_as_a_file(tmp_path):
    backing_file = tmp_path / "backing.bin"
    backing_file.write_bytes(b"contents that must not be served")
    entry = TransferEntry(
        path="folder",
        kind=ENTRY_DIRECTORY,
        size=backing_file.stat().st_size,
        mtime_ns=backing_file.stat().st_mtime_ns,
    )
    registry = SnapshotRegistry()
    registry.publish(
        TransferManifest(transfer_id="t-1", entries=(entry,)),
        {entry.path: backing_file},
    )

    with pytest.raises(SourceMissing):
        registry.read("t-1", 0, 0, 4)


@pytest.mark.skipif(sys.platform != "win32", reason="поведение разделения доступа - Windows")
def test_while_we_hold_the_descriptor_the_path_cannot_be_substituted(tmp_path):
    # Измеренное свойство: os.remove и os.rename отказывают с winerror 32.
    # Это и есть защита от TOCTOU - путь открывается один раз, дальше работа
    # идёт с файловым объектом, а не с именем.
    registry = SnapshotRegistry()
    source = _publish(registry, tmp_path)
    registry.read("t-1", 0, 0, 1)

    with pytest.raises(OSError):
        os.remove(source)
    with pytest.raises(OSError):
        os.rename(source, tmp_path / "swapped.bin")


def test_releasing_a_snapshot_closes_its_descriptor_and_forgets_it(tmp_path):
    registry = SnapshotRegistry()
    source = _publish(registry, tmp_path)
    registry.read("t-1", 0, 0, 1)
    # Освобождаем именно ОБСЛУЖИВАЕМЫЙ снимок - release не должен требовать,
    # чтобы передача сперва закончилась сама.
    assert registry.serving == frozenset({"t-1"})

    registry.release("t-1")

    assert registry.transfer_ids == ()
    assert registry.serving == frozenset()
    # Дескриптор закрыт - значит путь снова можно удалить.
    os.remove(source)


def test_releasing_a_transfer_id_twice_is_a_harmless_noop(tmp_path):
    registry = SnapshotRegistry()
    _publish(registry, tmp_path)
    registry.read("t-1", 0, 0, 1)

    registry.release("t-1")
    registry.release("t-1")  # не должно бросать KeyError или что-либо ещё

    assert registry.transfer_ids == ()


def test_publishing_the_same_transfer_id_twice_closes_the_first_snapshots_descriptor(tmp_path):
    # Повторная публикация transfer_id заменяет снимок, а не отказывает (это
    # уже подразумевал сброс позиции в _order). Но если бы прежний дескриптор
    # не закрывался при замене, first.bin остался бы заблокирован для удаления
    # до конца жизни процесса (спека §15) - именно тот леак, на который
    # указывает риск задачи 1.9.
    registry = SnapshotRegistry()
    first_source = _publish(registry, tmp_path, transfer_id="t-1", name="first.bin")
    registry.read("t-1", 0, 0, 1)

    _publish(
        registry, tmp_path, transfer_id="t-1", name="second.bin", payload=b"ZZZZZZZZZZ"
    )

    os.remove(first_source)  # дескриптор на first.bin закрыт - удаление проходит
    assert registry.read("t-1", 0, 0, 4) == b"ZZZZ"


def test_the_newest_snapshot_and_every_serving_one_are_kept(tmp_path):
    registry = SnapshotRegistry()
    _publish(registry, tmp_path, transfer_id="old", name="old.bin")
    registry.read("old", 0, 0, 1)  # old становится SERVING
    for index in range(RETENTION + 2):
        _publish(registry, tmp_path, transfer_id=f"new-{index}", name=f"n{index}.bin")

    assert "old" in registry.transfer_ids, (
        "снимок с активной передачей вытеснен - сценарий 1 сломан: смена буфера "
        "обмена оборвала бы идущую передачу"
    )
    assert f"new-{RETENTION + 1}" in registry.transfer_ids


def test_snapshots_that_are_neither_newest_nor_serving_are_evicted(tmp_path):
    registry = SnapshotRegistry()
    for index in range(RETENTION + 3):
        _publish(registry, tmp_path, transfer_id=f"t-{index}", name=f"f{index}.bin")

    assert len(registry.transfer_ids) <= RETENTION
    assert "t-0" not in registry.transfer_ids
