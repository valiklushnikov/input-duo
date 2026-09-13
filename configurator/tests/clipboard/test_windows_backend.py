"""Снимок локального буфера: что берём, что пропускаем, чего не трогаем."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QByteArray, QMimeData, QObject, QUrl, Signal

from duo_input.clipboard.backend import ORIGIN_MIME, ClipboardSnapshot
from duo_input.clipboard.offer import MAX_CONTENT_BYTES, ClipboardOffer, ContentDescriptor
from duo_input.clipboard.windows_backend import (
    DEBOUNCE_MS,
    PRIVATE_MARKERS,
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

    def hasFormat(self, mime: str) -> bool:  # noqa: N802 - Qt API
        return mime in self._payloads

    def data(self, mime: str) -> bytes:
        return self._payloads.get(mime, b"")

    def hasUrls(self) -> bool:  # noqa: N802 - Qt API
        return False

    def urls(self) -> list:
        return []

    def hasImage(self) -> bool:  # noqa: N802 - Qt API
        return False


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
    data = _FakeMimeData({"text/plain": b"hello", "text/rtf": b"{\\rtf1}"})

    snapshot = snapshot_from(data)

    assert set(snapshot.payloads) == {"text/plain"}


def test_snapshot_keeps_text_html():
    data = QMimeData()
    data.setData("text/plain", b"hello")
    data.setData("text/html", b"<b>hello</b>")

    snapshot = snapshot_from(data)

    assert snapshot.payloads["text/html"] == b"<b>hello</b>"


def test_snapshot_uri_list_keeps_only_web_urls():
    data = QMimeData()
    data.setUrls([QUrl("https://example.com"), QUrl("file:///C:/secret.txt")])

    snapshot = snapshot_from(data)

    assert snapshot.payloads["text/uri-list"] == b"https://example.com\r\n"


def test_snapshot_of_only_file_urls_has_no_uri_list():
    data = QMimeData()
    data.setUrls([QUrl("file:///C:/a.txt"), QUrl("file:///C:/b.txt")])

    snapshot = snapshot_from(data)

    assert "text/uri-list" not in snapshot.payloads


def test_snapshot_of_an_oversized_payload_is_empty():
    data = _FakeMimeData({"image/png": b"x" * (MAX_CONTENT_BYTES + 1)})

    assert snapshot_from(data).payloads == {}


def test_snapshot_of_an_oversized_payload_is_logged(caplog):
    """I5: скопировали большую картинку - должно быть видно почему её не отправило."""
    data = _FakeMimeData({"image/png": b"x" * (MAX_CONTENT_BYTES + 1)})

    with caplog.at_level("WARNING", logger="duo_input.clipboard.windows_backend"):
        snapshot_from(data)

    assert any("image/png" in record.message for record in caplog.records)


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
        """Повторный start не должен подписывать на сигнал второй раз.

        Тест считает ЧИСЛО ВЫЗОВОВ обработчика, а не число снимков,
        потому что побочные эффекты _on_data_changed идемпотентны.
        """
        clipboard = _FakeClipboard()
        backend = WindowsClipboardBackend(clipboard)

        handler_call_count = [0]
        original_on_data_changed = backend._on_data_changed

        def counting_handler():
            handler_call_count[0] += 1
            original_on_data_changed()

        # Подменим обработчик на счётчик
        backend._on_data_changed = counting_handler

        backend.start()
        backend.start()  # Повторный start - НЕ должен подписываться ещё раз

        clipboard.set_raw_data({"text/plain": b"hello"})
        clipboard.dataChanged.emit()

        qtbot.wait(DEBOUNCE_MS + 50)

        # Обработчик должен быть вызван РОВНО ОДИН раз,
        # несмотря на двойный start()
        assert handler_call_count[0] == 1

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


def test_a_file_only_copy_produces_a_snapshot_carrying_the_paths(qapp, tmp_path):
    source = tmp_path / "notes.txt"
    source.write_bytes(b"x")
    mime_data = QMimeData()
    mime_data.setUrls([QUrl.fromLocalFile(str(source))])

    snapshot = snapshot_from(mime_data)

    assert snapshot.payloads == {}
    assert snapshot.file_paths == (str(source),)


def test_a_file_only_copy_is_emitted_rather_than_retried_into_silence(qapp, tmp_path):
    # До этой правки _take_snapshot возвращался на `not snapshot.payloads`,
    # трижды пробовал заново и замолкал: копирование файла не порождало
    # ни одного события.
    source = tmp_path / "notes.txt"
    source.write_bytes(b"x")
    clipboard = qapp.clipboard()
    mime_data = QMimeData()
    mime_data.setUrls([QUrl.fromLocalFile(str(source))])
    clipboard.setMimeData(mime_data)
    backend = WindowsClipboardBackend(clipboard)
    taken: list[object] = []
    backend.snapshot_taken.connect(taken.append)

    backend._take_snapshot()

    assert taken, "снимок с файлами и без payload не был объявлен вовсе"
    assert taken[0].file_paths == (str(source),)


def test_a_private_clipboard_reports_neither_payloads_nor_paths(qapp, tmp_path):
    source = tmp_path / "secret.txt"
    source.write_bytes(b"x")
    mime_data = QMimeData()
    mime_data.setUrls([QUrl.fromLocalFile(str(source))])
    mime_data.setData(PRIVATE_MARKERS[0], QByteArray(b"0"))

    snapshot = snapshot_from(mime_data)

    assert snapshot.payloads == {}
    assert snapshot.file_paths == (), (
        "маркер приватности обошёл путь файлов - менеджер паролей, "
        "копирующий файл, отправил бы его"
    )
