"""Журнал на границах буфера обмена: где именно потерялась копия.

Без этих строк случай «Windows→Mac работает, Mac→Windows нет» нельзя было
разобрать ни по одному из двух журналов: сервис не писал ни отправку, ни приём
объявления, ни запрос содержимого. Каждая строка здесь - одна граница, и по
последней строке в цепочке видно, где цепочка оборвалась.

Содержимое буфера в журнал не попадает НИКОГДА: только seq, типы, размеры и
причины. Последний тест проверяет это на всём пути разом.
"""

from __future__ import annotations

import logging
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QMimeData, QObject, QTimer, Signal

from duo_input.clipboard.backend import ORIGIN_MIME, ClipboardSnapshot
from duo_input.clipboard.macos_backend import MacOSClipboardBackend
from duo_input.clipboard.offer import MAX_CONTENT_BYTES, ClipboardOffer, describe
from duo_input.clipboard.service import ClipboardService
from duo_input.clipboard.windows_backend import RETRY_LIMIT, WindowsClipboardBackend
from duo_input.clipboard.wire import Message, MessageType

OURS = "1" * 32
THEIRS = "2" * 32

#: Строка, которой не должно быть ни в одной записи журнала.
SECRET = b"secret-clipboard-text-4242"


@pytest.fixture
def boundary_log(caplog):
    caplog.set_level(logging.INFO, logger="duo_input.clipboard")
    return caplog


def _lines(caplog, key: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(key)]


class _FakeBackend:
    def __init__(self) -> None:
        self.published: list[ClipboardOffer] = []

    def publish(self, offer, fetcher) -> None:
        self.published.append(offer)

    def payload(self, mime):
        return None


class _FakeLink(QObject):
    message_received = Signal(object)
    disconnected = Signal(str)

    def __init__(self, generation: str = "gen-1") -> None:
        super().__init__()
        self.sent: list[Message] = []
        self.connection_generation = generation

    def send(self, message: Message) -> None:
        self.sent.append(message)


def _service(*, backend: bool = True) -> ClipboardService:
    service = ClipboardService(own_origin_id=OURS)
    if backend:
        service.attach_backend(_FakeBackend())
    return service


# ---------------------------------------------------------------------- локальная копия → объявление


def test_a_local_copy_logs_the_created_offer(boundary_log):
    service = _service()

    service.on_local_snapshot(ClipboardSnapshot({"text/plain": SECRET}))

    [line] = _lines(boundary_log, "clipboard_offer_created")
    assert "seq=1" in line
    assert "mimes=text/plain" in line
    assert f"bytes={len(SECRET)}" in line
    assert "linked=False" in line


def test_an_echo_of_what_we_received_is_logged_as_skipped(boundary_log):
    service = _service()
    service.on_remote_offer(ClipboardOffer(THEIRS, 1, describe({"text/plain": SECRET})))

    service.on_local_snapshot(ClipboardSnapshot({"text/plain": SECRET}))

    [line] = _lines(boundary_log, "clipboard_offer_skipped")
    assert "reason=echo" in line
    assert _lines(boundary_log, "clipboard_offer_created") == []


def test_an_undescribable_snapshot_is_logged_as_skipped(boundary_log):
    service = _service()

    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"x" * (MAX_CONTENT_BYTES + 1)}))

    [line] = _lines(boundary_log, "clipboard_offer_skipped")
    assert "reason=undescribable" in line


def test_an_offer_without_a_link_is_logged_as_dropped(boundary_log):
    """Именно так тихо терялась копия: offer_ready не подключён, пока нет связи."""
    service = _service()

    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"hello"}))

    [line] = _lines(boundary_log, "clipboard_offer_dropped")
    assert "seq=1" in line
    assert "reason=no_link" in line


def test_an_offer_buffered_for_a_reconnect_is_logged_as_buffered(boundary_log):
    service = _service()
    service.prepare_for_reconnect()

    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"hello"}))

    [line] = _lines(boundary_log, "clipboard_offer_buffered")
    assert "seq=1" in line
    assert _lines(boundary_log, "clipboard_offer_dropped") == []


def test_an_offer_sent_over_the_link_is_logged_with_its_generation(boundary_log, qapp):
    service = _service()
    link = _FakeLink("gen-42")
    service.attach_link(link)

    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"hello"}))

    [line] = _lines(boundary_log, "clipboard_offer_sent")
    assert "seq=1" in line
    assert "connection_generation=gen-42" in line
    assert [m.type for m in link.sent if m.type is MessageType.OFFER] == [MessageType.OFFER]
    assert _lines(boundary_log, "clipboard_offer_dropped") == []
    service.detach_link()


# ---------------------------------------------------------------------- объявление от пира


def test_a_received_and_published_offer_is_logged(boundary_log):
    service = _service()

    service.on_remote_offer(ClipboardOffer(THEIRS, 7, describe({"text/plain": b"hi"})))

    [received] = _lines(boundary_log, "clipboard_offer_received")
    assert "seq=7" in received
    assert "mimes=text/plain" in received
    [published] = _lines(boundary_log, "clipboard_offer_published")
    assert "seq=7" in published


def test_an_offer_with_our_own_origin_is_logged_as_ignored(boundary_log):
    service = _service()

    service.on_remote_offer(ClipboardOffer(OURS, 1, describe({"text/plain": b"hi"})))

    [line] = _lines(boundary_log, "clipboard_offer_ignored")
    assert "reason=own" in line


def test_a_stale_offer_is_logged_as_ignored_with_the_last_seq(boundary_log):
    service = _service()
    service.on_remote_offer(ClipboardOffer(THEIRS, 5, describe({"text/plain": b"a"})))

    service.on_remote_offer(ClipboardOffer(THEIRS, 3, describe({"text/plain": b"b"})))

    [line] = _lines(boundary_log, "clipboard_offer_ignored")
    assert "seq=3" in line
    assert "reason=stale" in line
    assert "last_seq=5" in line


def test_an_offer_without_a_backend_is_logged_as_ignored(boundary_log):
    service = _service(backend=False)

    service.on_remote_offer(ClipboardOffer(THEIRS, 1, describe({"text/plain": b"a"})))

    [line] = _lines(boundary_log, "clipboard_offer_ignored")
    assert "reason=no_backend" in line


# ---------------------------------------------------------------------- запрос содержимого (сторона-источник)


def test_a_served_fetch_is_logged(boundary_log, qapp):
    service = _service()
    link = _FakeLink()
    service.attach_link(link)
    service.on_local_snapshot(ClipboardSnapshot({"text/plain": SECRET}))

    service.handle_message(Message(MessageType.FETCH, {"seq": 1, "mime": "text/plain"}, b""))

    [line] = _lines(boundary_log, "clipboard_fetch_served")
    assert "seq=1" in line
    assert "mime=text/plain" in line
    assert f"bytes={len(SECRET)}" in line
    service.detach_link()


def test_a_fetch_for_an_old_offer_is_logged_as_refused_stale(boundary_log, qapp):
    service = _service()
    link = _FakeLink()
    service.attach_link(link)
    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"one"}))
    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"two"}))

    service.handle_message(Message(MessageType.FETCH, {"seq": 1, "mime": "text/plain"}, b""))

    [line] = _lines(boundary_log, "clipboard_fetch_refused")
    assert "seq=1" in line
    assert "reason=stale_seq" in line
    assert "last_sent_seq=2" in line
    service.detach_link()


def test_a_fetch_for_a_format_we_did_not_offer_is_logged_as_refused(boundary_log, qapp):
    service = _service()
    link = _FakeLink()
    service.attach_link(link)
    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"one"}))

    service.handle_message(Message(MessageType.FETCH, {"seq": 1, "mime": "image/png"}, b""))

    [line] = _lines(boundary_log, "clipboard_fetch_refused")
    assert "reason=unknown_mime" in line
    service.detach_link()


# ---------------------------------------------------------------------- запрос содержимого (сторона-вставка)


def test_a_fetch_that_gets_content_logs_sent_and_done(boundary_log, qapp):
    service = _service()
    link = _FakeLink()
    service.attach_link(link)
    offer = ClipboardOffer(THEIRS, 3, describe({"text/plain": SECRET}))
    QTimer.singleShot(
        0,
        lambda: service.handle_message(
            Message(MessageType.CONTENT, {"seq": 3, "mime": "text/plain"}, SECRET)
        ),
    )

    assert service._fetch("text/plain", offer) == SECRET

    [sent] = _lines(boundary_log, "clipboard_fetch_sent")
    assert "seq=3" in sent and "mime=text/plain" in sent
    [done] = _lines(boundary_log, "clipboard_fetch_done")
    assert f"bytes={len(SECRET)}" in done
    assert "ms=" in done
    service.detach_link()


def test_a_fetch_the_peer_refused_logs_failed_with_the_reason(boundary_log, qapp):
    service = _service()
    link = _FakeLink()
    service.attach_link(link)
    offer = ClipboardOffer(THEIRS, 3, describe({"text/plain": b"x"}))
    QTimer.singleShot(
        0,
        lambda: service.handle_message(
            Message(
                MessageType.CONTENT_ERROR,
                {"seq": 3, "mime": "text/plain", "reason": "объявление устарело"},
                b"",
            )
        ),
    )

    with pytest.raises(TimeoutError):
        service._fetch("text/plain", offer)

    [line] = _lines(boundary_log, "clipboard_fetch_failed")
    assert "seq=3" in line
    assert "объявление устарело" in line
    service.detach_link()


def test_a_fetch_without_a_link_logs_failed_no_link(boundary_log):
    service = _service()
    offer = ClipboardOffer(THEIRS, 3, describe({"text/plain": b"x"}))

    with pytest.raises(TimeoutError):
        service._fetch("text/plain", offer)

    [line] = _lines(boundary_log, "clipboard_fetch_failed")
    assert "reason=no_link" in line


# ---------------------------------------------------------------------- граница macOS


class _FakePasteboard:
    def __init__(self, count: int = 10) -> None:
        self._count = count
        self._concealed = False

    def change_count(self) -> int:
        return self._count

    def set_count(self, value: int) -> None:
        self._count = value

    def is_concealed(self) -> bool:
        return self._concealed

    def set_concealed(self, value: bool) -> None:
        self._concealed = value

    def publish_with_origin(self, offer, fetcher) -> int:
        self._count += 1
        return self._count

    def observe_wake(self, callback):
        return object()

    def stop_observing_wake(self, observer) -> None:
        pass


class _FakeMimeData:
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


class _FakeClipboard:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.mime = _FakeMimeData(payloads)

    def mimeData(self):  # noqa: N802 - Qt API
        return self.mime


def _mac(payloads: dict[str, bytes]) -> tuple[MacOSClipboardBackend, _FakePasteboard]:
    pasteboard = _FakePasteboard(10)
    backend = MacOSClipboardBackend(_FakeClipboard(payloads), pasteboard=pasteboard)
    backend.start()
    return backend, pasteboard


def test_macos_local_change_is_logged(boundary_log):
    backend, pasteboard = _mac({"text/plain": SECRET})

    pasteboard.set_count(11)
    backend._poll()

    [line] = _lines(boundary_log, "clipboard_local_change")
    assert "platform=macos" in line
    assert "change_count=11" in line
    assert "mimes=text/plain" in line
    assert f"bytes={len(SECRET)}" in line
    backend.stop()


def test_macos_our_own_publish_is_logged_as_skipped(boundary_log):
    # Защитная ветка current == own != last_seen (как в test_macos_backend):
    # publish() сам обновляет last_seen, так что обычный опрос сюда не доходит.
    backend, pasteboard = _mac({"text/plain": b"x"})
    backend._own_change_count = 25
    pasteboard.set_count(25)

    backend._poll()

    [line] = _lines(boundary_log, "clipboard_local_skipped")
    assert "platform=macos" in line
    assert "reason=own_publish" in line
    backend.stop()


def test_macos_concealed_change_is_logged_as_skipped(boundary_log):
    backend, pasteboard = _mac({"text/plain": SECRET})
    pasteboard.set_concealed(True)

    pasteboard.set_count(11)
    backend._poll()

    [line] = _lines(boundary_log, "clipboard_local_skipped")
    assert "reason=concealed" in line
    backend.stop()


def test_macos_our_origin_marker_is_logged_as_skipped(boundary_log):
    backend, pasteboard = _mac({"text/plain": b"x", ORIGIN_MIME: b"1"})

    pasteboard.set_count(11)
    backend._poll()

    [line] = _lines(boundary_log, "clipboard_local_skipped")
    assert "reason=own_marker" in line
    backend.stop()


def test_macos_change_without_supported_formats_is_logged_with_the_formats(boundary_log):
    backend, pasteboard = _mac({"application/x-unknown": b"x"})

    pasteboard.set_count(11)
    backend._poll()

    [line] = _lines(boundary_log, "clipboard_local_skipped")
    assert "reason=no_supported_formats" in line
    assert "formats=application/x-unknown" in line
    backend.stop()


# ---------------------------------------------------------------------- граница Windows


class _QtClipboard(QObject):
    dataChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._mime_data = QMimeData()

    def mimeData(self) -> QMimeData:  # noqa: N802 - Qt API
        return self._mime_data

    def setMimeData(self, data) -> None:  # noqa: N802 - Qt API
        self._mime_data = data
        self.dataChanged.emit()

    def set_raw(self, payloads: dict[str, bytes]) -> None:
        data = QMimeData()
        for mime, payload in payloads.items():
            data.setData(mime, payload)
        self._mime_data = data


def test_windows_local_change_is_logged(boundary_log, qapp):
    clipboard = _QtClipboard()
    backend = WindowsClipboardBackend(clipboard)
    clipboard.set_raw({"text/plain": SECRET})

    backend._take_snapshot()

    [line] = _lines(boundary_log, "clipboard_local_change")
    assert "platform=windows" in line
    assert "mimes=text/plain" in line
    assert f"bytes={len(SECRET)}" in line


def test_windows_our_own_publish_is_logged_as_skipped(boundary_log, qapp):
    clipboard = _QtClipboard()
    backend = WindowsClipboardBackend(clipboard)
    backend.start()

    backend.publish(ClipboardOffer(THEIRS, 1, describe({"text/plain": b"x"})), lambda mime: b"x")

    [line] = _lines(boundary_log, "clipboard_local_skipped")
    assert "platform=windows" in line
    assert "reason=own_publish" in line
    backend.stop()


def test_windows_change_without_supported_formats_is_logged_once_after_retries(boundary_log, qapp):
    clipboard = _QtClipboard()
    backend = WindowsClipboardBackend(clipboard)
    clipboard.set_raw({"application/x-unknown": b"x"})
    backend._attempts = RETRY_LIMIT

    backend._take_snapshot()

    [line] = _lines(boundary_log, "clipboard_local_skipped")
    assert "reason=no_supported_formats" in line


# ---------------------------------------------------------------------- приватность


def test_clipboard_content_never_reaches_the_log(boundary_log, qapp):
    """Весь путь разом - копия, объявление, запрос, ответ - и ни байта содержимого."""
    service = _service()
    link = _FakeLink()
    service.attach_link(link)
    service.on_local_snapshot(ClipboardSnapshot({"text/plain": SECRET}))
    service.handle_message(Message(MessageType.FETCH, {"seq": 1, "mime": "text/plain"}, b""))
    offer = ClipboardOffer(THEIRS, 9, describe({"text/plain": SECRET}))
    service.on_remote_offer(offer)
    QTimer.singleShot(
        0,
        lambda: service.handle_message(
            Message(MessageType.CONTENT, {"seq": 9, "mime": "text/plain"}, SECRET)
        ),
    )
    service._fetch("text/plain", offer)
    backend, pasteboard = _mac({"text/plain": SECRET})
    pasteboard.set_count(11)
    backend._poll()

    assert boundary_log.records, "журнал вообще не писался - проверка ничего не доказала"
    assert SECRET.decode() not in boundary_log.text
    service.detach_link()
    backend.stop()
