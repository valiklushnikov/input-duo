"""Снимок локального буфера: что берём, что пропускаем, чего не трогаем."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QMimeData, QObject, Signal

from duo_input.clipboard.backend import ORIGIN_MIME, ClipboardSnapshot
from duo_input.clipboard.offer import MAX_CONTENT_BYTES, ClipboardOffer, ContentDescriptor
from duo_input.clipboard.windows_backend import (
    DEBOUNCE_MS,
    RETRY_LIMIT,
    WindowsClipboardBackend,
    is_private,
    snapshot_from,
)


class _FakeMimeData:
    """Утиная замена QMimeData: только то, что читает снимок."""

    def __init__(self, payloads: dict[str, bytes]) -> None:
        self._payloads = payloads

    def formats(self) -> list[str]:
        return list(self._payloads)

    def data(self, mime: str) -> bytes:
        return self._payloads.get(mime, b"")


class _FakeClipboard(QObject):
    """Фейковый буфер обмена с сигналом изменения и переключаемым содержимым."""

    dataChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._mime_data = QMimeData()
        self._published_data = None

    def mimeData(self) -> QMimeData:
        """Вернуть текущее содержимое буфера."""
        return self._mime_data

    def setMimeData(self, data: QMimeData) -> None:
        """Установить содержимое и испустить сигнал об изменении."""
        self._published_data = data
        self._mime_data = data
        self.dataChanged.emit()

    def set_raw_data(self, payloads: dict[str, bytes]) -> None:
        """Установить сырые данные без испускания сигнала (для инициализации)."""
        data = QMimeData()
        for mime, payload in payloads.items():
            data.setData(mime, payload)
        self._mime_data = data

    def published_data(self) -> QMimeData | None:
        """Вернуть последние опубликованные данные."""
        return self._published_data


def test_a_password_manager_marker_makes_the_clipboard_private():
    formats = [
        "text/plain",
        'application/x-qt-windows-mime;value="ExcludeClipboardContentFromMonitorProcessing"',
    ]

    assert is_private(formats) is True


def test_an_ordinary_clipboard_is_not_private():
    assert is_private(["text/plain", "text/html"]) is False


def test_our_own_marker_makes_the_clipboard_private_to_us():
    assert is_private(["text/plain", ORIGIN_MIME]) is True


def test_snapshot_keeps_only_the_formats_we_synchronise():
    data = _FakeMimeData({"text/plain": b"hello", "text/html": b"<b>hello</b>"})

    snapshot = snapshot_from(data)

    assert set(snapshot.payloads) == {"text/plain"}


def test_snapshot_of_an_oversized_payload_is_empty():
    data = _FakeMimeData({"image/png": b"x" * (MAX_CONTENT_BYTES + 1)})

    assert snapshot_from(data).payloads == {}


def test_snapshot_accepts_payload_exactly_at_size_limit():
    """Граница должна быть закреплена: ровно потолок допустим."""
    data = _FakeMimeData({"text/plain": b"x" * MAX_CONTENT_BYTES})

    snapshot = snapshot_from(data)

    assert snapshot.payloads == {"text/plain": b"x" * MAX_CONTENT_BYTES}


def test_snapshot_of_an_empty_clipboard_is_empty():
    assert snapshot_from(_FakeMimeData({})).payloads == {}


def test_snapshot_ignores_empty_payloads():
    """Format exists but content is empty - should be skipped."""
    data = _FakeMimeData({"text/plain": b""})

    assert snapshot_from(data).payloads == {}


# ============================================================================
# Тесты класса WindowsClipboardBackend
# ============================================================================


class TestWindowsClipboardBackendDebounce:
    """Дебаунс: несколько сигналов об изменении дают РОВНО ОДИН снимок."""

    def test_multiple_signals_produce_single_snapshot(self, qapp, qtbot):
        """Одна операция Ctrl+C порождает несколько событий dataChanged."""
        clipboard = _FakeClipboard()
        backend = WindowsClipboardBackend(clipboard)

        snapshots = []
        backend.snapshot_taken.connect(lambda snapshot: snapshots.append(snapshot))

        backend.start()

        # Отправим несколько сигналов об изменении подряд
        clipboard.set_raw_data({"text/plain": b"first"})
        clipboard.dataChanged.emit()
        clipboard.dataChanged.emit()
        clipboard.dataChanged.emit()

        # Дождёмся дебаунса
        qtbot.wait(DEBOUNCE_MS + 50)

        # Должно быть РОВНО ОДНО событие, несмотря на три сигнала
        assert len(snapshots) == 1
        assert snapshots[0].payloads == {"text/plain": b"first"}

        backend.stop()


class TestWindowsClipboardBackendRetry:
    """Ограниченные повторы при пустом чтении."""

    def test_empty_reads_retry_but_limit_is_respected(self, qapp, qtbot):
        """Если буфер заперт (пустой), повторяются попытки, но не бесконечно."""
        clipboard = _FakeClipboard()
        backend = WindowsClipboardBackend(clipboard)

        snapshots = []
        backend.snapshot_taken.connect(lambda snapshot: snapshots.append(snapshot))

        backend.start()

        # Отправим сигнал об изменении
        clipboard.dataChanged.emit()

        # Буфер остаётся пустым, ждём несколько дебаунсов
        qtbot.wait((RETRY_LIMIT + 1) * DEBOUNCE_MS + 100)

        # Никакого снимка не должно быть
        assert len(snapshots) == 0

        backend.stop()

        # После stop сигналы не должны обрабатываться
        clipboard.set_raw_data({"text/plain": b"hello"})
        clipboard.dataChanged.emit()
        qtbot.wait(DEBOUNCE_MS + 50)

        # Всё ещё нет снимков
        assert len(snapshots) == 0

    def test_timer_stops_after_retry_limit_exhausted(self, qapp, qtbot):
        """Таймер должен остановиться после исчерпания лимита."""
        clipboard = _FakeClipboard()
        backend = WindowsClipboardBackend(clipboard)

        backend.start()
        clipboard.dataChanged.emit()

        # Дождёмся того, чтобы таймер исчерпал повторы
        qtbot.wait((RETRY_LIMIT + 1) * DEBOUNCE_MS + 100)

        # Проверим, что таймер не работает
        assert not backend._debounce.isActive()

        backend.stop()

    def test_successful_read_stops_retries(self, qapp, qtbot):
        """Если чтение успешно, повторы прекращаются и таймер останавливается."""
        clipboard = _FakeClipboard()
        backend = WindowsClipboardBackend(clipboard)

        snapshots = []
        backend.snapshot_taken.connect(lambda snapshot: snapshots.append(snapshot))

        backend.start()

        # Сначала установим успешное содержимое
        clipboard.set_raw_data({"text/plain": b"success"})
        clipboard.dataChanged.emit()

        # Дождёмся дебаунса
        qtbot.wait(DEBOUNCE_MS + 50)

        # Должно быть ровно одно событие со успешным снимком
        assert len(snapshots) == 1
        assert snapshots[-1].payloads == {"text/plain": b"success"}

        # Таймер должен быть остановлен
        assert not backend._debounce.isActive()

        backend.stop()


class TestWindowsClipboardBackendPublishSuppression:
    """Подавление собственной публикации."""

    def test_publish_does_not_trigger_snapshot(self, qapp, qtbot):
        """Вызов publish НЕ должен приводить к собственному снимку."""
        clipboard = _FakeClipboard()
        backend = WindowsClipboardBackend(clipboard)

        snapshots = []
        backend.snapshot_taken.connect(lambda snapshot: snapshots.append(snapshot))

        backend.start()

        # Сначала установим реальные данные и получим первый снимок
        clipboard.set_raw_data({"text/plain": b"original"})
        clipboard.dataChanged.emit()
        qtbot.wait(DEBOUNCE_MS + 50)
        assert len(snapshots) == 1

        # Теперь опубликуем содержимое второго компьютера
        # Это НЕ должно создать новый снимок благодаря _suspended флагу
        offer = ClipboardOffer(
            origin_id="other-machine",
            seq=1,
            descriptors=(ContentDescriptor(mime="text/plain", size=5, sha256="abc"),),
        )

        def fetcher(mime: str) -> bytes:
            return b"hello"

        backend.publish(offer, fetcher)

        # Дождёмся дебаунса
        qtbot.wait(DEBOUNCE_MS + 50)

        # Должно остаться ровно один снимок (оригинальный)
        assert len(snapshots) == 1

        backend.stop()


class TestWindowsClipboardBackendIdempotence:
    """Идемпотентность подписки и отписки."""

    def test_repeated_start_does_not_double_subscribe(self, qapp, qtbot):
        """Повторный start не должен подписывать на сигнал второй раз."""
        clipboard = _FakeClipboard()
        backend = WindowsClipboardBackend(clipboard)

        snapshots = []
        backend.snapshot_taken.connect(lambda snapshot: snapshots.append(snapshot))

        backend.start()
        backend.start()  # Второй раз

        clipboard.set_raw_data({"text/plain": b"hello"})
        clipboard.dataChanged.emit()

        qtbot.wait(DEBOUNCE_MS + 50)

        # Должно быть РОВНО ОДНО событие, несмотря на двойную подписку
        assert len(snapshots) == 1

        backend.stop()

    def test_stop_unsubscribes_and_stops_timer(self, qapp, qtbot):
        """stop() должен отписать от сигнала и остановить таймер."""
        clipboard = _FakeClipboard()
        backend = WindowsClipboardBackend(clipboard)

        snapshots = []
        backend.snapshot_taken.connect(lambda snapshot: snapshots.append(snapshot))

        backend.start()

        clipboard.set_raw_data({"text/plain": b"hello"})
        clipboard.dataChanged.emit()

        qtbot.wait(DEBOUNCE_MS + 50)

        assert len(snapshots) == 1

        backend.stop()

        # После stop сигнал об изменении не должен приводить к снимку
        clipboard.set_raw_data({"text/plain": b"world"})
        clipboard.dataChanged.emit()

        qtbot.wait(DEBOUNCE_MS + 50)

        # Снимок не был добавлен
        assert len(snapshots) == 1

        # Таймер должен быть остановлен
        assert not backend._debounce.isActive()

    def test_repeated_stop_is_safe(self, qapp, qtbot):
        """Повторный stop не должен падать."""
        clipboard = _FakeClipboard()
        backend = WindowsClipboardBackend(clipboard)

        backend.start()
        backend.stop()
        backend.stop()  # Не должно быть ошибки
