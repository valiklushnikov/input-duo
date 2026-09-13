"""Единственное, что разделяют COM-поток и GUI-поток Qt.

Здесь нет ни Qt, ни сокетов, ни COM - поэтому очередь проверяется настоящими
потоками, а не имитацией, и утверждения о блокировке и пробуждении являются
утверждениями о том, что произойдёт в бою.
"""

from __future__ import annotations

import threading
import time

import pytest

from duo_input.transfer.pipe import ChunkPipe, PipeClosed, PipeOverflow


def test_a_fresh_pipe_is_empty_and_hands_back_nothing_without_blocking():
    pipe = ChunkPipe()

    assert pipe.depth == 0
    assert pipe.take(4096) == b""


def test_what_was_pushed_comes_back_in_order():
    pipe = ChunkPipe(capacity_chunks=2)

    pipe.push(b"abc")
    pipe.push(b"de")

    assert pipe.take(10) == b"abc"
    assert pipe.take(10) == b"de"


def test_take_never_returns_more_than_asked_and_keeps_the_remainder():
    pipe = ChunkPipe()
    pipe.push(b"abcdef")

    assert pipe.take(2) == b"ab"
    assert pipe.take(2) == b"cd"
    assert pipe.take(99) == b"ef"


def test_pushing_above_capacity_is_refused_rather_than_buffered():
    # Это и есть доказуемый потолок памяти: очередь физически не может вырасти
    # больше окна, сколько бы отправитель ни присылал.
    pipe = ChunkPipe(capacity_chunks=1)
    pipe.push(b"first")

    with pytest.raises(PipeOverflow):
        pipe.push(b"second")


def test_the_high_water_mark_records_the_deepest_the_queue_ever_got():
    pipe = ChunkPipe(capacity_chunks=3)

    pipe.push(b"a")
    pipe.push(b"b")
    pipe.take(1)
    pipe.take(1)

    assert pipe.depth == 0
    assert pipe.high_water == 2


def test_wait_returns_false_on_timeout_instead_of_hanging_for_ever():
    pipe = ChunkPipe()

    started = time.perf_counter()
    assert pipe.wait(timeout=0.05) is False
    assert time.perf_counter() - started < 1.0


def test_a_blocked_waiter_wakes_when_a_chunk_arrives():
    pipe = ChunkPipe()
    woke = threading.Event()

    def consumer() -> None:
        pipe.wait(timeout=5.0)
        woke.set()

    thread = threading.Thread(target=consumer, daemon=True)
    thread.start()
    time.sleep(0.05)
    pipe.push(b"payload")

    assert woke.wait(timeout=2.0), "push не разбудил ожидающего"
    thread.join(timeout=2.0)


def test_a_blocked_waiter_wakes_immediately_when_the_pipe_is_closed():
    # Отзывчивость отмены держится ровно на этом: заблокированный Read обязан
    # вернуть управление сразу, а не через таймаут.
    pipe = ChunkPipe()
    result: list[str] = []

    def consumer() -> None:
        try:
            pipe.wait(timeout=30.0)
        except PipeClosed as error:
            result.append(error.reason)

    thread = threading.Thread(target=consumer, daemon=True)
    thread.start()
    time.sleep(0.05)
    started = time.perf_counter()
    pipe.close("cancelled")
    thread.join(timeout=2.0)

    assert result == ["cancelled"]
    assert time.perf_counter() - started < 1.0, "закрытие ждало таймаута вместо notify_all"


def test_every_waiter_wakes_on_close_not_only_the_first():
    pipe = ChunkPipe()
    woken = []
    lock = threading.Lock()

    def consumer() -> None:
        try:
            pipe.wait(timeout=30.0)
        except PipeClosed:
            with lock:
                woken.append(1)

    threads = [threading.Thread(target=consumer, daemon=True) for _ in range(3)]
    for thread in threads:
        thread.start()
    time.sleep(0.1)
    pipe.close("link lost")
    for thread in threads:
        thread.join(timeout=2.0)

    assert len(woken) == 3, "close() пробудил не всех - notify() вместо notify_all()"


def test_take_after_close_raises_rather_than_pretending_the_stream_ended():
    pipe = ChunkPipe()
    pipe.close("link lost")

    with pytest.raises(PipeClosed):
        pipe.take(10)


def test_buffered_bytes_are_still_readable_after_finish():
    pipe = ChunkPipe(capacity_chunks=2)
    pipe.push(b"tail")
    pipe.finish()

    assert pipe.take(10) == b"tail"
    assert pipe.take(10) == b""
    assert pipe.finished is True


def test_wait_after_finish_returns_at_once_without_raising():
    pipe = ChunkPipe()
    pipe.finish()

    assert pipe.wait(timeout=5.0) is True


def test_pushing_after_finish_is_refused():
    pipe = ChunkPipe()
    pipe.finish()

    with pytest.raises(PipeClosed):
        pipe.push(b"late")


def test_closing_twice_keeps_the_first_reason():
    pipe = ChunkPipe()

    pipe.close("cancelled")
    pipe.close("link lost")

    assert pipe.closed_reason == "cancelled", (
        "второй close перезаписал причину - пользователь увидел бы не то, что произошло"
    )


def test_the_queue_never_exceeds_its_capacity_under_a_real_producer_and_consumer():
    pipe = ChunkPipe(capacity_chunks=4)
    stop = threading.Event()

    def producer() -> None:
        while not stop.is_set():
            try:
                pipe.push(b"x" * 1024)
            except PipeOverflow:
                time.sleep(0.001)

    thread = threading.Thread(target=producer, daemon=True)
    thread.start()
    for _ in range(2000):
        pipe.take(1024)
    stop.set()
    thread.join(timeout=2.0)

    assert pipe.high_water <= 4, (
        f"глубина доходила до {pipe.high_water} при окне 4 - потолок памяти не доказан"
    )
