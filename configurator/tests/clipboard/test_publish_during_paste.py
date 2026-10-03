"""Новое объявление, пришедшее посреди вставки, не заменяет читаемый объект.

Windows читает RemoteMimeData через OLE delayed rendering: retrieveData
крутит вложенный цикл событий, пока содержимое едет по сети, и в этом цикле
приходит следующее объявление. QClipboard.setMimeData() удаляет прежний
QMimeData - тот самый, чей retrieveData сейчас на стеке, - и процесс молча
умирает с access violation (два падения в журнале, repro 3/3 в
.superpowers/sdd/2026-10-03-clipboard-replace-during-paste).

Фейковый буфер здесь проверяет ровно этот инвариант: в момент каждой
замены он смотрит, не читается ли заменяемый объект.
"""

from __future__ import annotations

import logging
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QObject, Signal

from duo_input.clipboard.backend import ORIGIN_MIME, RemoteMimeData
from duo_input.clipboard.offer import ClipboardOffer, describe
from duo_input.clipboard.windows_backend import WindowsClipboardBackend

THEIRS = "2" * 32


class _ReadingClipboard(QObject):
    """Буфер, который помнит каждую замену и то, читался ли заменённый объект."""

    dataChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.current = None
        self.replacements = 0
        self.replaced_while_reading = 0
        #: Объекты, чей retrieveData сейчас на стеке (как у OLE при вставке).
        self.being_read: list = []
        self.owned = False

    def setMimeData(self, data) -> None:  # noqa: N802 - Qt API
        if any(reading is self.current for reading in self.being_read):
            self.replaced_while_reading += 1
        self.current = data
        self.owned = True
        self.replacements += 1
        self.dataChanged.emit()

    def mimeData(self):  # noqa: N802 - Qt API
        return self.current

    def ownsClipboard(self) -> bool:  # noqa: N802 - Qt API
        return self.owned

    def external_copy(self, text: bytes) -> None:
        """Пользователь скопировал в другой программе: буфер больше не наш."""
        data = QMimeData()
        data.setData("text/plain", text)
        self.current = data
        self.owned = False
        self.dataChanged.emit()

    def current_seq(self) -> int:
        marker = bytes(self.current.data(ORIGIN_MIME)).decode("ascii")
        return int(marker.rsplit(":", 1)[1])


def _offer(seq: int) -> ClipboardOffer:
    return ClipboardOffer(THEIRS, seq, describe({"text/plain": b"x", "text/html": b"<x>"}))


def _backend() -> tuple[WindowsClipboardBackend, _ReadingClipboard]:
    clipboard = _ReadingClipboard()
    backend = WindowsClipboardBackend(clipboard)
    backend.start()
    return backend, clipboard


def _paste(clipboard: _ReadingClipboard, mime: str = "text/plain") -> bytes:
    """Вставка так, как её делает Qt: data() -> виртуальный retrieveData."""
    data = clipboard.current
    clipboard.being_read.append(data)
    try:
        return bytes(data.data(mime))
    finally:
        clipboard.being_read.remove(data)


def test_an_offer_during_a_paste_is_published_only_after_the_paste_returns(qapp):
    backend, clipboard = _backend()
    seen_during_paste: list[int] = []

    def fetcher(mime: str) -> bytes:
        backend.publish(_offer(2), lambda m: b"second")
        qapp.processEvents()  # вложенный цикл _fetch прокачивает события
        seen_during_paste.append(clipboard.current_seq())
        return b"first"

    backend.publish(_offer(1), fetcher)
    assert _paste(clipboard) == b"first"

    assert seen_during_paste == [1]
    assert clipboard.replaced_while_reading == 0
    assert clipboard.current_seq() == 1  # синхронно из retrieveData - никогда
    qapp.processEvents()
    assert clipboard.current_seq() == 2
    assert _paste(clipboard) == b"second"
    backend.stop()


def test_two_offers_during_one_paste_publish_only_the_newest(qapp):
    backend, clipboard = _backend()

    def fetcher(mime: str) -> bytes:
        backend.publish(_offer(2), lambda m: b"second")
        qapp.processEvents()
        backend.publish(_offer(3), lambda m: b"third")
        qapp.processEvents()
        return b"first"

    backend.publish(_offer(1), fetcher)
    _paste(clipboard)
    qapp.processEvents()

    assert clipboard.replaced_while_reading == 0
    assert clipboard.replacements == 2  # первое объявление и самое новое
    assert clipboard.current_seq() == 3
    backend.stop()


def test_a_fetch_that_fails_still_releases_the_clipboard(qapp):
    backend, clipboard = _backend()

    def fetcher(mime: str) -> bytes:
        backend.publish(_offer(2), lambda m: b"second")
        raise TimeoutError("объявление устарело")

    backend.publish(_offer(1), fetcher)
    assert _paste(clipboard) == b""
    qapp.processEvents()

    assert clipboard.replaced_while_reading == 0
    assert clipboard.current_seq() == 2
    backend.stop()


def test_a_nested_read_of_another_format_keeps_the_object_busy(qapp):
    """Вставка просит два формата, второй - пока первый ещё ждёт сеть."""
    backend, clipboard = _backend()

    def fetcher(mime: str) -> bytes:
        if mime == "text/plain":
            backend.publish(_offer(2), lambda m: b"second")
            _paste(clipboard, "text/html")  # вложенное чтение закончилось...
            qapp.processEvents()  # ...а внешнее ещё на стеке
            assert clipboard.current_seq() == 1
        return b"first"

    backend.publish(_offer(1), fetcher)
    _paste(clipboard)
    qapp.processEvents()

    assert clipboard.replaced_while_reading == 0
    assert clipboard.current_seq() == 2
    backend.stop()


def test_a_read_that_starts_before_the_deferred_publish_runs_postpones_it(qapp, caplog):
    """Между концом вставки и отложенной публикацией началась новая вставка."""
    caplog.set_level(logging.INFO, logger="duo_input.clipboard")
    backend, clipboard = _backend()

    def fetcher(mime: str) -> bytes:
        if mime == "text/plain":
            backend.publish(_offer(2), lambda m: b"second")
        else:
            qapp.processEvents()  # отложенная публикация уже в очереди
        return b"first"

    backend.publish(_offer(1), fetcher)
    _paste(clipboard, "text/plain")
    _paste(clipboard, "text/html")  # до того, как очередь событий дошла
    assert clipboard.replaced_while_reading == 0
    qapp.processEvents()

    assert clipboard.replaced_while_reading == 0
    assert clipboard.current_seq() == 2
    published = [r.getMessage() for r in caplog.records
                 if r.getMessage() == "clipboard_offer_published seq=2"]
    assert len(published) == 1  # и ни одного ложного, пока ждали вторую вставку
    backend.stop()


def test_a_newer_offer_published_directly_supersedes_the_deferred_one(qapp):
    """Вставка кончилась, отложенная публикация в очереди - и пришло ещё одно."""
    backend, clipboard = _backend()

    def fetcher(mime: str) -> bytes:
        backend.publish(_offer(2), lambda m: b"second")
        return b"first"

    backend.publish(_offer(1), fetcher)
    _paste(clipboard)
    assert backend.publish(_offer(3), lambda m: b"third") is True
    qapp.processEvents()

    assert clipboard.current_seq() == 3
    backend.stop()


def test_stop_discards_a_deferred_offer(qapp):
    backend, clipboard = _backend()

    def fetcher(mime: str) -> bytes:
        backend.publish(_offer(2), lambda m: b"second")
        return b"first"

    backend.publish(_offer(1), fetcher)
    _paste(clipboard)
    backend.stop()  # отложенная публикация уже в очереди событий
    qapp.processEvents()

    assert clipboard.replacements == 1
    assert clipboard.current_seq() == 1


def test_an_offer_without_a_paste_in_progress_is_published_at_once(qapp):
    backend, clipboard = _backend()

    assert backend.publish(_offer(1), lambda m: b"first") is True
    assert backend.publish(_offer(2), lambda m: b"second") is True

    assert clipboard.replacements == 2
    assert clipboard.current_seq() == 2
    backend.stop()


def test_a_deferred_offer_is_logged_as_deferred_then_as_published(qapp, caplog):
    caplog.set_level(logging.INFO, logger="duo_input.clipboard")
    backend, clipboard = _backend()
    results: list[bool] = []

    def fetcher(mime: str) -> bytes:
        results.append(backend.publish(_offer(2), lambda m: b"second"))
        return b"first"

    backend.publish(_offer(1), fetcher)
    _paste(clipboard)
    messages = [r.getMessage() for r in caplog.records]
    assert results == [False]
    assert "clipboard_offer_deferred seq=2 reason=paste_in_progress" in messages
    assert not any(m.startswith("clipboard_offer_published seq=2") for m in messages)

    qapp.processEvents()

    messages = [r.getMessage() for r in caplog.records]
    assert "clipboard_offer_published seq=2" in messages
    backend.stop()


def _dropped(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if r.getMessage().startswith("clipboard_offer_dropped")]


def test_a_local_copy_during_the_paste_drops_the_deferred_remote_offer(qapp, caplog):
    """Свежая локальная копия новее отложенного объявления: B не должен её затереть."""
    caplog.set_level(logging.INFO, logger="duo_input.clipboard")
    backend, clipboard = _backend()

    def fetcher(mime: str) -> bytes:
        backend.publish(_offer(2), lambda m: b"second")
        clipboard.external_copy(b"typed here")
        return b"first"

    backend.publish(_offer(1), fetcher)
    _paste(clipboard)
    qapp.processEvents()

    assert bytes(clipboard.current.data("text/plain")) == b"typed here"
    assert clipboard.replacements == 1
    assert _dropped(caplog) == ["clipboard_offer_dropped seq=2 reason=local_change"]
    backend.stop()


def test_a_late_echo_of_our_own_publish_keeps_the_deferred_offer(qapp, caplog):
    """dataChanged, пока буфер всё ещё наш, - эхо собственной публикации, а не копия."""
    caplog.set_level(logging.INFO, logger="duo_input.clipboard")
    backend, clipboard = _backend()

    def fetcher(mime: str) -> bytes:
        backend.publish(_offer(2), lambda m: b"second")
        clipboard.dataChanged.emit()
        return b"first"

    backend.publish(_offer(1), fetcher)
    _paste(clipboard)
    qapp.processEvents()

    assert clipboard.current_seq() == 2
    assert _dropped(caplog) == []
    backend.stop()


def test_a_deferred_offer_superseded_by_a_newer_one_is_logged(qapp, caplog):
    caplog.set_level(logging.INFO, logger="duo_input.clipboard")
    backend, clipboard = _backend()

    def fetcher(mime: str) -> bytes:
        backend.publish(_offer(2), lambda m: b"second")
        backend.publish(_offer(3), lambda m: b"third")
        return b"first"

    backend.publish(_offer(1), fetcher)
    _paste(clipboard)
    qapp.processEvents()

    assert _dropped(caplog) == ["clipboard_offer_dropped seq=2 reason=superseded"]
    backend.stop()


def test_a_deferred_offer_superseded_by_a_direct_publish_is_logged(qapp, caplog):
    caplog.set_level(logging.INFO, logger="duo_input.clipboard")
    backend, clipboard = _backend()

    def fetcher(mime: str) -> bytes:
        backend.publish(_offer(2), lambda m: b"second")
        return b"first"

    backend.publish(_offer(1), fetcher)
    _paste(clipboard)
    backend.publish(_offer(3), lambda m: b"third")  # отложенная ещё в очереди
    qapp.processEvents()

    assert _dropped(caplog) == ["clipboard_offer_dropped seq=2 reason=superseded"]
    backend.stop()


def test_a_flushed_deferred_offer_is_not_logged_as_superseded(qapp, caplog):
    caplog.set_level(logging.INFO, logger="duo_input.clipboard")
    backend, clipboard = _backend()

    def fetcher(mime: str) -> bytes:
        backend.publish(_offer(2), lambda m: b"second")
        return b"first"

    backend.publish(_offer(1), fetcher)
    _paste(clipboard)
    qapp.processEvents()

    assert clipboard.current_seq() == 2
    assert _dropped(caplog) == []
    backend.stop()


def test_a_new_backend_does_not_replace_an_object_the_old_one_left_being_read(qapp):
    """Общий буфер выключили и включили, пока вставка ждала сеть (Minor 3)."""
    old, clipboard = _backend()
    fresh: list[WindowsClipboardBackend] = []

    def fetcher(mime: str) -> bytes:
        old.stop()
        backend = WindowsClipboardBackend(clipboard)
        backend.start()
        fresh.append(backend)
        assert backend.publish(_offer(2), lambda m: b"second") is False
        qapp.processEvents()
        return b"first"

    old.publish(_offer(1), fetcher)
    _paste(clipboard)
    assert clipboard.replaced_while_reading == 0
    qapp.processEvents()

    assert clipboard.replaced_while_reading == 0
    assert clipboard.current_seq() == 2
    fresh[0].stop()


def test_the_offscreen_clipboard_hands_back_our_own_object(real_clipboard):
    """QClipboard.mimeData() возвращает наш объект - на offscreen-платформе.

    Доказывает это только для offscreen QPlatformClipboard, на котором идут
    тесты. Для настоящего Windows опора другая: Qt 6.10.1
    QWindowsClipboard::mimeData() возвращает ``m_data->mimeData()``, пока
    ownsClipboard(), и прогон на настоящем буфере (repro_fixed_r1_run.log)
    показал ``clipboard_offer_deferred`` - занятость нашлась через буфер.
    """
    backend = WindowsClipboardBackend(real_clipboard)
    backend.start()
    backend.publish(_offer(1), lambda m: b"first")

    current = real_clipboard.mimeData()

    assert isinstance(current, RemoteMimeData)
    assert bytes(current.data(ORIGIN_MIME)) == f"{THEIRS}:1".encode("ascii")
    backend.stop()


def test_an_idle_callback_runs_once_after_the_last_read_only(qapp):
    """Однократно: остановленная граница не должна получать вызовы вечно."""
    calls: list[str] = []
    data = RemoteMimeData(_offer(1), lambda m: b"x")
    data.call_when_idle(lambda: calls.append("idle"))
    data.call_when_idle(lambda: calls.append("idle2"))

    data.data("text/plain")
    data.data("text/html")

    assert calls == ["idle", "idle2"]


def test_the_same_idle_callback_registered_twice_runs_once(qapp):
    calls: list[str] = []

    def callback() -> None:
        calls.append("idle")

    data = RemoteMimeData(_offer(1), lambda m: b"x")
    data.call_when_idle(callback)
    data.call_when_idle(callback)

    data.data("text/plain")

    assert calls == ["idle"]


def test_a_deferred_offer_is_dropped_if_the_clipboard_was_lost_during_the_paste(qapp, caplog):
    """Чужое копирование во время вставки: его WM_CLIPBOARDUPDATE задержан до
    конца чтения, и dataChanged придёт уже после отложенной публикации. Она
    сама обязана увидеть, что буфер больше не наш, и не затирать копию."""
    caplog.set_level(logging.INFO, logger="duo_input.clipboard")
    backend, clipboard = _backend()

    def fetcher(mime: str) -> bytes:
        backend.publish(_offer(2), lambda m: b"second")
        clipboard.owned = False  # другая программа взяла буфер; сигнал задержан
        return b"first"

    backend.publish(_offer(1), fetcher)
    _paste(clipboard)
    qapp.processEvents()

    assert clipboard.replacements == 1
    assert _dropped(caplog) == ["clipboard_offer_dropped seq=2 reason=local_change"]
    backend.stop()


def test_our_last_object_still_being_read_counts_as_busy_after_losing_the_clipboard(qapp):
    """mimeData() уже отдаёт чужое, а Qt при setMimeData удалит m_data - наш
    последний объект, который всё ещё читают."""
    backend, clipboard = _backend()
    results: list[bool] = []

    def fetcher(mime: str) -> bytes:
        clipboard.current = QMimeData()  # чужое содержимое, без сигнала
        clipboard.owned = False
        results.append(backend.publish(_offer(2), lambda m: b"second"))
        return b"first"

    backend.publish(_offer(1), fetcher)
    _paste(clipboard)

    assert results == [False]
    assert clipboard.replacements == 1
    backend.stop()
