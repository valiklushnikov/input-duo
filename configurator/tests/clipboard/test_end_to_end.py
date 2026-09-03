"""Два сервиса в одном процессе: копия на одном становится вставкой на другом."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time

import pytest
from PySide6.QtCore import QObject, QTimer, Signal

from duo_input.clipboard.backend import ClipboardSnapshot
from duo_input.clipboard.identity import load_or_create
from duo_input.clipboard.listener import PeerListener
from duo_input.clipboard.offer import ClipboardOffer, describe
from duo_input.clipboard.peer import PeerLink
from duo_input.clipboard.service import ClipboardService
from duo_input.clipboard.wire import Message, MessageType

OURS = "1" * 32
THEIRS = "2" * 32


class _RecordingBackend:
    def __init__(self) -> None:
        self.published: list[ClipboardOffer] = []
        self.fetchers: list[object] = []

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def publish(self, offer, fetcher) -> None:
        self.published.append(offer)
        self.fetchers.append(fetcher)

    def payload(self, mime):
        return None


@pytest.fixture
def pair(qtbot, tmp_path):
    """Два узла, соединённых настоящим TLS на loopback."""
    server_identity = load_or_create(tmp_path / "server")
    client_identity = load_or_create(tmp_path / "client")

    listener = PeerListener(server_identity)
    listener.listen(0)

    server_service = ClipboardService(server_identity.origin_id)
    server_backend = _RecordingBackend()
    server_service.attach_backend(server_backend)

    links: list[PeerLink] = []
    listener.link_ready.connect(links.append)

    client_service = ClipboardService(client_identity.origin_id)
    client_backend = _RecordingBackend()
    client_service.attach_backend(client_backend)

    client_link = PeerLink(client_identity)
    with qtbot.waitSignal(client_link.connected, timeout=5000):
        client_link.connect_to("127.0.0.1", listener.port, server_identity.fingerprint)
    qtbot.waitUntil(lambda: bool(links), timeout=5000)

    client_service.attach_link(client_link)
    server_service.attach_link(links[0])

    yield server_service, server_backend, client_service, client_backend

    listener.stop()
    client_link.close()


def test_a_copy_on_one_side_is_offered_to_the_other(qtbot, pair):
    server_service, server_backend, client_service, _ = pair

    client_service.on_local_snapshot(ClipboardSnapshot({"text/plain": "привет".encode("utf-8")}))

    qtbot.waitUntil(lambda: bool(server_backend.published), timeout=5000)
    assert server_backend.published[0].mimes() == ("text/plain",)


def test_content_arrives_only_when_it_is_asked_for(qtbot, pair):
    server_service, server_backend, client_service, _ = pair
    payload = "привет".encode("utf-8")

    client_service.on_local_snapshot(ClipboardSnapshot({"text/plain": payload}))
    qtbot.waitUntil(lambda: bool(server_backend.published), timeout=5000)

    fetched = server_backend.fetchers[0]("text/plain")

    assert fetched == payload


def test_asking_for_a_stale_offer_fails_rather_than_returning_the_wrong_thing(qtbot, pair):
    server_service, server_backend, client_service, _ = pair

    client_service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"first"}))
    qtbot.waitUntil(lambda: bool(server_backend.published), timeout=5000)
    stale_fetcher = server_backend.fetchers[0]

    client_service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"second"}))
    qtbot.waitUntil(lambda: len(server_backend.published) == 2, timeout=5000)

    with pytest.raises(TimeoutError):
        stale_fetcher("text/plain")


class _FakeLink(QObject):
    """Заменитель `PeerLink`, не трогающий сеть - только сигналы и учёт отправленного."""

    message_received = Signal(object)
    disconnected = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[Message] = []

    def send(self, message: Message) -> None:
        self.sent.append(message)


def test_a_fetch_without_a_link_fails_instantly_rather_than_going_to_the_network(qtbot):
    """Факт 1 из брифа: Windows переспрашивает содержимое при закрытии процесса.

    Если бы запрос не отказывал мгновенно при отсутствии связи, выход из
    программы после разрыва ушёл бы во вложенный цикл событий и ждал бы
    TRANSFER_TIMEOUT_MS (30 с) вместо немедленного отказа.
    """
    service = ClipboardService(OURS)
    offer = ClipboardOffer(THEIRS, 1, describe({"text/plain": b"x"}))

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        service._fetch("text/plain", offer)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0


def test_the_fetch_loop_ends_when_the_link_is_lost_while_waiting(qtbot):
    """Разрыв ВО ВРЕМЯ ожидания содержимого обязан отпустить вложенный цикл.

    Заменитель связи никогда не отвечает сам - единственное, что может
    завершить `loop.exec()` до TRANSFER_TIMEOUT_MS (30 с), это обработка
    разрыва в `_on_link_lost`.
    """
    service = ClipboardService(OURS)
    link = _FakeLink()
    service.attach_link(link)
    offer = ClipboardOffer(THEIRS, 1, describe({"text/plain": b"x"}))

    QTimer.singleShot(50, lambda: link.disconnected.emit("порвалось"))

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        service._fetch("text/plain", offer)
    elapsed = time.monotonic() - started

    assert elapsed < 5.0


def test_a_stale_fetch_request_gets_a_content_error_not_the_old_payload(qtbot):
    """Отказ отдавать содержимое по устаревшему номеру объявления - на уровне сообщений.

    Проверяет `_answer_fetch` напрямую: запрос с номером объявления, которое
    уже перекрыто новым, должен получить CONTENT_ERROR, а не CONTENT со
    старыми байтами.
    """
    service = ClipboardService(OURS)
    link = _FakeLink()
    service.attach_link(link)

    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"first"}))
    first_seq = service.last_sent_offer.seq
    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"second"}))

    link.sent.clear()
    service.handle_message(Message(MessageType.FETCH, {"seq": first_seq, "mime": "text/plain"}, b""))

    assert len(link.sent) == 1
    assert link.sent[0].type is MessageType.CONTENT_ERROR


def test_a_fresh_fetch_request_gets_the_current_payload(qtbot):
    service = ClipboardService(OURS)
    link = _FakeLink()
    service.attach_link(link)

    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"hello"}))
    seq = service.last_sent_offer.seq

    link.sent.clear()
    service.handle_message(Message(MessageType.FETCH, {"seq": seq, "mime": "text/plain"}, b""))

    assert len(link.sent) == 1
    assert link.sent[0].type is MessageType.CONTENT
    assert link.sent[0].blob == b"hello"


def test_a_ping_is_answered_with_a_pong(qtbot):
    service = ClipboardService(OURS)
    link = _FakeLink()
    service.attach_link(link)

    service.handle_message(Message(MessageType.PING, {}, b""))

    assert len(link.sent) == 1
    assert link.sent[0].type is MessageType.PONG


def test_attaching_a_link_starts_the_heartbeat(qapp):
    """QTimer.isActive() лжёт без живого QApplication - здесь он есть (фикстура qapp)."""
    service = ClipboardService(OURS)
    link = _FakeLink()

    assert service._heartbeat.isActive() is False
    service.attach_link(link)
    assert service._heartbeat.isActive() is True


def test_detaching_a_link_stops_the_heartbeat(qapp):
    service = ClipboardService(OURS)
    link = _FakeLink()
    service.attach_link(link)

    service.detach_link()

    assert service._heartbeat.isActive() is False


def test_losing_the_link_stops_the_heartbeat_too(qapp):
    service = ClipboardService(OURS)
    link = _FakeLink()
    service.attach_link(link)

    link.disconnected.emit("порвалось")

    assert service._heartbeat.isActive() is False


def test_link_state_changed_reports_connect_and_disconnect(qapp):
    service = ClipboardService(OURS)
    link = _FakeLink()
    states: list[str] = []
    service.link_state_changed.connect(states.append)

    service.attach_link(link)
    link.disconnected.emit("порвалось")

    assert states == ["connected", "disconnected: порвалось"]


def test_content_for_the_wrong_mime_is_ignored_while_waiting(qtbot):
    """CONTENT для другого формата, чем тот, что ждут, не должен подсунуть чужой ответ.

    В обычной работе `_pending_fetch` заведён под конкретный mime: пришедшее
    CONTENT с другим mime - это не тот ответ, который ждали, и подставлять
    его содержимое было бы неверно.
    """
    service = ClipboardService(OURS)
    link = _FakeLink()
    service.attach_link(link)
    service._pending_fetch = {"mime": "text/plain", "payload": None, "error": None}

    service.handle_message(Message(MessageType.CONTENT, {"seq": 1, "mime": "text/html"}, "чужое".encode("utf-8")))

    assert service._pending_fetch["payload"] is None


def test_the_content_requested_signal_was_a_stub_and_is_gone() -> None:
    """Бриф требует удалить сигнал `content_requested`: его больше никто не слушает."""
    assert not hasattr(ClipboardService, "content_requested")
