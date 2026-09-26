"""Координатор: связывание, задержки переподключения и закрепление доверия.

Ревью первого круга нашло критический дефект: неспаренный узел принимал ЛЮБОЕ
входящее TLS-соединение и сразу включал обмен буфером обмена, потому что
`expect(None)` действовал не только во время связывания, но постоянно, пока
пира нет. Каждый тест ниже, что относится к этому кругу, явно привязан к одной
из четырёх исправленных гарантий:

1. Чужой сертификат принимается только во время связывания.
2. Соединение времён связывания не ведёт к CONNECTED/attach_link само по себе.
3. Доверие и обмен включаются только когда согласны ОБЕ стороны (наше
   confirm_pairing И PAIR_CONFIRM от пира).
4. Отказ пира или истечение окна связывания возвращают к «не спарены».
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import duo_input.clipboard.coordinator as coordinator_module
from PySide6.QtCore import QObject, Signal

from duo_input.clipboard.coordinator import (
    RECONNECT_DELAYS_MS,
    TCP_PORT,
    ClipboardCoordinator,
    LinkState,
    reconnect_delay_ms,
)
from duo_input.clipboard.backend import ClipboardSnapshot
from duo_input.clipboard.identity import load_or_create
from duo_input.clipboard.pairing import PairingCandidate
from duo_input.clipboard.peer import PeerLink
from duo_input.clipboard.trust import TrustedPeer, TrustStore
from duo_input.clipboard.wire import (
    CAPABILITIES,
    CAPABILITY_CLIPBOARD,
    CAPABILITY_FILES,
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    Message,
    MessageType,
)

#: Гарантированно больше и гарантированно меньше любого настоящего origin_id
#: (тот - случайный 32-значный шестнадцатеричный uuid4).
LARGEST_ORIGIN_ID = "f" * 32
SMALLEST_ORIGIN_ID = "0" * 32


class _FakeLink(QObject):
    """Заменитель `PeerLink`, не трогающий сеть - только сигналы и учёт вызовов."""

    message_received = Signal(object)
    connected = Signal(str)
    disconnected = Signal(str)

    def __init__(self, peer_fingerprint: str = "", peer_address: str = "") -> None:
        super().__init__()
        self.sent: list[Message] = []
        self.closed = False
        self.peer_fingerprint = peer_fingerprint
        self.peer_address = peer_address

    def send(self, message: Message) -> None:
        self.sent.append(message)

    def close(self) -> None:
        self.closed = True

    def deliver(self, message: Message) -> None:
        """Доставить кадр так, как это сделал бы настоящий сокет."""
        self.message_received.emit(message)


class _LateSignal:
    """Signal-double, доставляющий уже поставленный в очередь callback после disconnect."""

    def __init__(self) -> None:
        self._connected: list[object] = []
        self._queued: list[object] = []

    def connect(self, callback) -> None:
        self._connected.append(callback)

    def disconnect(self, callback) -> None:
        self._connected.remove(callback)
        self._queued.append(callback)

    def emit(self, *args) -> None:
        callbacks = [*self._connected, *self._queued]
        self._queued.clear()
        for callback in callbacks:
            callback(*args)


class _LateSignalLink:
    """Link-double: close/unsubscribe не отменяет уже queued delivery сигнала."""

    def __init__(self, peer_fingerprint: str, peer_address: str = "192.168.1.5") -> None:
        self.message_received = _LateSignal()
        self.disconnected = _LateSignal()
        self.sent: list[Message] = []
        self.closed = False
        self.peer_fingerprint = peer_fingerprint
        self.peer_address = peer_address

    def send(self, message: Message) -> None:
        self.sent.append(message)

    def close(self) -> None:
        self.closed = True


class _FakeTimer:
    """Заменитель `QTimer`, который просто запоминает, чем его попросили."""

    def __init__(self) -> None:
        self.starts: list[int] = []
        self.stopped = 0
        self._active = False

    def start(self, ms: int) -> None:
        self.starts.append(ms)
        self._active = True

    def stop(self) -> None:
        self.stopped += 1
        self._active = False

    def isActive(self) -> bool:
        return self._active


class _DialLink(QObject):
    """PeerLink, который только записывает, куда его попросили позвонить."""

    connected = Signal(str)
    disconnected = Signal(str)
    message_received = Signal(object)
    made: list["_DialLink"] = []

    def __init__(self, identity, parent=None) -> None:
        super().__init__(parent)
        self.address = ""
        self.peer_address = ""
        _DialLink.made.append(self)

    def connect_to(self, address, port, expected_fingerprint) -> None:
        self.address = address
        self.peer_address = address

    def send(self, message) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.fixture
def dial(monkeypatch):
    _DialLink.made = []
    monkeypatch.setattr(coordinator_module, "PeerLink", _DialLink)
    return _DialLink.made


def _make_coordinator(tmp_path, peer_origin_id: str | None = None) -> tuple[ClipboardCoordinator, TrustStore]:
    identity = load_or_create(tmp_path / "id")
    trust = TrustStore(tmp_path / "peers.json")
    if peer_origin_id is not None:
        trust.remember(TrustedPeer(peer_origin_id, "LAPTOP-TWO", "f" * 64, "192.168.1.5"))
    coordinator = ClipboardCoordinator(identity=identity, trust=trust, machine_name="LAPTOP-ONE")
    return coordinator, trust


def _reach_pairing_candidate(
    coordinator: ClipboardCoordinator,
    origin_id: str = "2" * 32,
    machine_name: str = "LAPTOP-TWO",
    fingerprint: str = "f" * 64,
    address: str = "192.168.1.5",
    *,
    begin_pairing: bool = True,
    link=None,
) -> tuple[object, PairingCandidate]:
    """Довести парринг до момента, когда код уже показан и кандидат известен,
    но ни одна из сторон ещё не подтвердила.
    """
    if begin_pairing:
        coordinator.begin_pairing()
    if link is None:
        link = _FakeLink(peer_fingerprint=fingerprint, peer_address=address)
    coordinator._on_incoming_link(link)
    link.message_received.emit(
        Message(
            MessageType.PAIR_REQUEST,
            {
                "origin_id": origin_id,
                "machine_name": machine_name,
                "protocol_major": PROTOCOL_MAJOR,
                "protocol_minor": 0,
            },
            b"",
        )
    )
    return link, coordinator._pairing_candidate


# ---------------------------------------------------------------------- задержки переповтора


def test_reconnect_delays_grow_and_then_stop_growing():
    delays = [reconnect_delay_ms(attempt) for attempt in range(7)]

    assert delays[: len(RECONNECT_DELAYS_MS)] == list(RECONNECT_DELAYS_MS)
    assert delays[-1] == RECONNECT_DELAYS_MS[-1]
    assert delays[-2] == RECONNECT_DELAYS_MS[-1]


def test_a_negative_attempt_still_gives_the_first_delay():
    assert reconnect_delay_ms(-1) == RECONNECT_DELAYS_MS[0]


def test_repeated_drops_grow_the_retry_delay_and_then_cap_it(tmp_path):
    """Не только чистая функция: сам координатор обязан растить и упирать паузу."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    fake_retry = _FakeTimer()
    coordinator._retry = fake_retry

    for _ in range(len(RECONNECT_DELAYS_MS) + 2):
        coordinator._drop("тест")

    assert fake_retry.starts == list(RECONNECT_DELAYS_MS) + [RECONNECT_DELAYS_MS[-1]] * 2


def test_a_successful_connection_resets_the_retry_attempt_counter(tmp_path):
    """Без сброса счётчика одно короткое восстановление связи не спасло бы от потолка навсегда."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    fake_retry = _FakeTimer()
    coordinator._retry = fake_retry

    coordinator._drop("первый разрыв")
    coordinator._drop("второй разрыв")
    assert coordinator._attempt == 2

    coordinator._on_connected(_FakeLink())
    assert coordinator._attempt == 0

    coordinator._drop("третий разрыв")
    assert fake_retry.starts[-1] == RECONNECT_DELAYS_MS[0]
    coordinator.stop()


# ---------------------------------------------------------------------- состояния


def test_an_unpaired_coordinator_starts_unpaired(tmp_path):
    coordinator, _ = _make_coordinator(tmp_path)

    assert coordinator.state is LinkState.UNPAIRED
    assert coordinator.peer is None


def test_a_port_that_cannot_be_listened_on_is_reported_as_blocked(tmp_path, monkeypatch):
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id="2" * 32)
    monkeypatch.setattr(coordinator._listener, "listen", lambda port: False)

    states: list[str] = []
    coordinator.state_changed.connect(states.append)
    coordinator.start()

    assert LinkState.BLOCKED.value in states


def test_the_pairing_code_is_offered_for_confirmation(tmp_path):
    coordinator, trust = _make_coordinator(tmp_path)
    offered: list[tuple[str, object]] = []
    coordinator.pairing_code_ready.connect(lambda code, candidate: offered.append((code, candidate)))

    candidate = PairingCandidate("2" * 32, "LAPTOP-TWO", "f" * 64, "192.168.1.5", 47654)
    coordinator._offer_pairing(candidate)

    code, offered_candidate = offered[0]
    assert len(code) == 6 and code.isdigit()
    assert offered_candidate is candidate
    # Предложение кода - это ещё не доверие: закрепляет только успешное связывание.
    assert trust.peer() is None
    assert coordinator.state is LinkState.UNPAIRED


# ---------------------------------------------------------------------- кто звонит (рабочая связь)


def test_the_smaller_origin_id_calls_the_other_side(tmp_path, monkeypatch):
    """Звонит тот, чей origin_id меньше - это снимает гонку встречных соединений."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    calls: list[tuple[str, int, str | None]] = []

    class _RecordingLink(QObject):
        connected = Signal(str)
        disconnected = Signal(str)

        def __init__(self, identity, parent=None) -> None:
            super().__init__(parent)

        def connect_to(self, address, port, expected_fingerprint) -> None:
            calls.append((address, port, expected_fingerprint))

        def send(self, message) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(coordinator_module, "PeerLink", _RecordingLink)

    coordinator._try_connect()

    assert calls == [("192.168.1.5", TCP_PORT, "f" * 64)]


def test_a_failed_last_known_address_starts_discovery_and_uses_the_new_address(
    tmp_path, monkeypatch
):
    """A moved trusted peer must be rediscovered after its saved address fails."""
    coordinator, trust = _make_coordinator(
        tmp_path, peer_origin_id=LARGEST_ORIGIN_ID
    )
    calls: list[tuple[str, int, str | None]] = []
    links: list[object] = []

    class _RecordingLink(QObject):
        connected = Signal(str)
        disconnected = Signal(str)
        message_received = Signal(object)

        def __init__(self, identity, parent=None) -> None:
            super().__init__(parent)
            links.append(self)

        def connect_to(self, address, port, expected_fingerprint) -> None:
            calls.append((address, port, expected_fingerprint))

        def send(self, message) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(coordinator_module, "PeerLink", _RecordingLink)

    coordinator._try_connect()
    links[0].disconnected.emit("host unreachable")

    try:
        assert coordinator._discovery._timer.isActive() is True

        coordinator._on_peer_seen(
            coordinator_module.Beacon(
                origin_id=LARGEST_ORIGIN_ID,
                machine_name="LAPTOP-TWO",
                fingerprint="f" * 64,
                port=TCP_PORT,
                protocol_major=PROTOCOL_MAJOR,
            ),
            "192.168.1.99",
        )

        # После неудачи тикает таймер повтора - маячок его не обгоняет
        # (то же правило, что у платы), а лишь даёт адрес следующему набору.
        assert calls == [("192.168.1.5", TCP_PORT, "f" * 64)]

        coordinator._retry.stop()
        coordinator._try_connect()  # то, что сделал бы сработавший _retry

        assert calls == [
            ("192.168.1.5", TCP_PORT, "f" * 64),
            ("192.168.1.99", TCP_PORT, "f" * 64),
        ]
    finally:
        coordinator.stop()


def test_the_bigger_origin_id_waits_for_the_call(tmp_path, monkeypatch, qapp):
    """Больший origin_id не звонит сам - иначе оба узла звонили бы одновременно."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=SMALLEST_ORIGIN_ID)
    constructed: list[object] = []

    class _RecordingLink(QObject):
        connected = Signal(str)
        disconnected = Signal(str)

        def __init__(self, identity, parent=None) -> None:
            super().__init__(parent)
            constructed.append(self)

        def connect_to(self, address, port, expected_fingerprint) -> None:
            raise AssertionError("больший узел не должен звонить сам")

        def send(self, message) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(coordinator_module, "PeerLink", _RecordingLink)

    coordinator._try_connect()

    assert constructed == []
    assert coordinator._silence.isActive() is True
    coordinator.stop()


# ---------------------------------------------------------------------- версия протокола (рабочая связь)


def test_a_different_protocol_major_drops_the_link(tmp_path, qapp):
    """§11: расхождение major версии - отдельное состояние, не обычный разрыв.

    Обычный разрыв уходит в бесконечные повторы; здесь это было бы обманом -
    вторая машина не обновится сама по себе от переподключения.
    """
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    link = _FakeLink()

    coordinator._on_connected(link)
    assert coordinator.state is LinkState.CONNECTED

    coordinator._on_message(Message(MessageType.HELLO, {"protocol_major": PROTOCOL_MAJOR + 1}, b""))

    assert coordinator.state is LinkState.PROTOCOL_MISMATCH
    assert link.closed is True
    assert coordinator._retry.isActive() is False
    coordinator.stop()


def test_a_matching_protocol_major_keeps_the_link(tmp_path, qapp):
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    link = _FakeLink()

    coordinator._on_connected(link)
    coordinator._on_message(Message(MessageType.HELLO, {"protocol_major": PROTOCOL_MAJOR}, b""))

    assert coordinator.state is LinkState.CONNECTED
    assert link.closed is False
    coordinator.stop()


# ---------------------------------------------------------------------- сторож молчания (рабочая связь)


def test_the_silence_watchdog_drops_a_quiet_link(tmp_path, qtbot):
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    link = _FakeLink()
    coordinator._on_connected(link)

    # Настоящий SILENCE_LIMIT_MS (30 с) слишком долог для теста - сокращаем
    # интервал уже запущенного таймера и перезапускаем отсчёт с нуля.
    coordinator._silence.setInterval(50)
    coordinator._silence.start()

    with qtbot.waitSignal(coordinator.state_changed, timeout=2000):
        pass

    assert coordinator.state is LinkState.DISCONNECTED
    assert link.closed is True
    coordinator.stop()


def test_resume_drops_the_pre_sleep_link_before_accepting_new_copies(tmp_path):
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=SMALLEST_ORIGIN_ID)
    link = _FakeLink()
    coordinator._on_connected(link)

    coordinator.recover_after_resume()

    coordinator.service.on_local_snapshot(
        ClipboardSnapshot({"text/plain": b"copied while reconnecting"})
    )
    replacement = _FakeLink()
    coordinator._on_connected(replacement)

    assert link.closed is True
    offers = [message for message in replacement.sent if message.type is MessageType.OFFER]
    assert len(offers) == 1
    coordinator.stop()


def test_forgetting_a_peer_cancels_a_buffered_post_wake_copy(tmp_path):
    coordinator, trust = _make_coordinator(tmp_path, peer_origin_id=SMALLEST_ORIGIN_ID)
    coordinator._on_connected(_FakeLink())
    coordinator.recover_after_resume()
    coordinator.service.on_local_snapshot(
        ClipboardSnapshot({"text/plain": b"belongs to forgotten peer"})
    )

    coordinator.forget_peer()
    trust.remember(
        TrustedPeer(LARGEST_ORIGIN_ID, "NEW-PEER", "e" * 64, "192.168.1.9")
    )
    replacement = _FakeLink(peer_fingerprint="e" * 64)
    coordinator._on_connected(replacement)

    offers = [message for message in replacement.sent if message.type is MessageType.OFFER]
    assert offers == []
    coordinator.stop()


def test_an_incoming_message_resets_the_silence_watchdog(tmp_path, qtbot):
    """Любое сообщение - не только HELLO - должно отодвигать разрыв по молчанию."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    link = _FakeLink()
    coordinator._on_connected(link)
    coordinator._silence.setInterval(200)
    coordinator._silence.start()

    qtbot.wait(120)
    coordinator._on_message(Message(MessageType.PING, {}, b""))
    qtbot.wait(120)

    # Прошло 240 мс с интервалом в 200 мс, но сообщение в середине отодвинуло срок.
    assert coordinator.state is LinkState.CONNECTED
    coordinator.stop()


# ---------------------------------------------------------------------- свойство 1: чужой сертификат
# принимается ТОЛЬКО во время связывания


def test_start_without_a_peer_refuses_unknown_certificates(tmp_path, monkeypatch):
    """Без доверенного пира start() обязан закрепить за слушателем "никого",
    а не режим парринга (None)."""
    coordinator, _ = _make_coordinator(tmp_path)
    monkeypatch.setattr(coordinator._listener, "listen", lambda port: True)
    recorded: list[str | None] = []
    monkeypatch.setattr(coordinator._listener, "expect", lambda fingerprint: recorded.append(fingerprint))

    coordinator.start()

    assert recorded[-1] == ""


def test_an_incoming_link_with_the_wrong_fingerprint_is_refused(tmp_path):
    """Второй пояс защиты: даже если слушатель почему-то пропустил чужого,
    координатор сам не пускает его дальше рукопожатия."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id="2" * 32)
    link = _FakeLink(peer_fingerprint="not-the-pinned-fingerprint")

    coordinator._on_incoming_link(link)

    assert coordinator._link is None
    assert link.closed is True


def test_an_incoming_link_with_the_right_fingerprint_still_connects(tmp_path, qapp):
    """Оборотная сторона предыдущего теста: защита не должна мешать законному пиру."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id="2" * 32)
    link = _FakeLink(peer_fingerprint="f" * 64)

    coordinator._on_incoming_link(link)

    assert coordinator._link is link
    assert coordinator.state is LinkState.CONNECTED
    coordinator.stop()


def test_a_dropped_incoming_link_is_noticed_and_reconnection_is_accepted(tmp_path):
    """I1: обрыв ВХОДЯЩЕЙ связи должен замечаться, как и обрыв исходящей.

    До исправления disconnected подписывался только в _try_connect(); связь,
    пришедшая через _on_incoming_link(), не имела обработчика разрыва вообще -
    состояние застревало в CONNECTED, а новая связь от того же пира
    отвергалась, потому что self._link всё ещё указывал на мёртвый объект.
    """
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id="2" * 32)
    link = _FakeLink(peer_fingerprint="f" * 64)
    coordinator._on_incoming_link(link)
    assert coordinator.state is LinkState.CONNECTED

    link.disconnected.emit("сеть пропала")

    assert coordinator.state is LinkState.DISCONNECTED
    assert coordinator._link is None

    second = _FakeLink(peer_fingerprint="f" * 64)
    coordinator._on_incoming_link(second)

    assert coordinator._link is second
    assert coordinator.state is LinkState.CONNECTED
    coordinator.stop()


def test_a_dropped_link_from_a_finished_pairing_is_noticed(tmp_path):
    """I1: то же самое для связи, пришедшей из завершившегося связывания."""
    coordinator, trust = _make_coordinator(tmp_path)
    link, candidate = _reach_pairing_candidate(coordinator)
    coordinator.confirm_pairing(candidate)
    link.message_received.emit(Message(MessageType.PAIR_CONFIRM, {"agree": True}, b""))
    assert coordinator.state is LinkState.CONNECTED

    link.disconnected.emit("сеть пропала")

    assert coordinator.state is LinkState.DISCONNECTED
    assert coordinator._link is None
    coordinator.stop()


def test_an_unknown_certificate_outside_pairing_never_reaches_connected(tmp_path, qtbot):
    """Не заглушка: настоящее TLS-соединение от постороннего вне связывания
    отражается на уровне слушателя и не доходит до CONNECTED."""
    coordinator, _ = _make_coordinator(tmp_path)
    coordinator._listener.expect("")  # то же, что выставил бы start() без пира
    assert coordinator._listener.listen(0) is True

    stranger_identity = load_or_create(tmp_path / "stranger")
    stranger = PeerLink(stranger_identity)
    stranger.connect_to("127.0.0.1", coordinator._listener.port, None)

    qtbot.wait(500)

    assert coordinator._link is None
    assert coordinator.state is LinkState.UNPAIRED
    coordinator.stop()
    stranger.close()


def test_after_the_pairing_window_expires_the_listener_refuses_unknown_certificates_again(tmp_path, qtbot):
    """Дверь, открытая на время связывания, обязана закрыться сама - без
    какого-либо дополнительного действия пользователя."""
    coordinator, _ = _make_coordinator(tmp_path)
    link, _candidate = _reach_pairing_candidate(coordinator)
    assert coordinator._listener._expected_fingerprint is None  # во время связывания - кто угодно

    coordinator._pairing_window.setInterval(50)
    coordinator._pairing_window.start()
    qtbot.waitUntil(lambda: not coordinator._pairing, timeout=2000)

    assert coordinator._listener._expected_fingerprint == ""
    assert link.closed is True
    assert coordinator._pairing_link is None
    assert coordinator._pairing_candidate is None


def test_after_the_pairing_window_expires_a_stranger_is_refused_again(tmp_path, qtbot):
    """То же самое, но сквозным способом: настоящее TLS-соединение после
    истечения окна связывания отказано, как и до начала связывания."""
    coordinator, _ = _make_coordinator(tmp_path)
    assert coordinator._listener.listen(0) is True
    coordinator.begin_pairing()
    coordinator._pairing_window.setInterval(50)
    coordinator._pairing_window.start()

    qtbot.waitUntil(lambda: not coordinator._pairing, timeout=2000)

    stranger_identity = load_or_create(tmp_path / "stranger")
    stranger = PeerLink(stranger_identity)
    stranger.connect_to("127.0.0.1", coordinator._listener.port, None)

    qtbot.wait(500)

    assert coordinator._link is None
    coordinator.stop()
    stranger.close()


# ---------------------------------------------------------------------- свойство 2: связь времён
# связывания ведёт к коду, а не к attach_link


def test_a_pairing_link_offers_the_code_but_does_not_attach(tmp_path):
    coordinator, _ = _make_coordinator(tmp_path)
    offered: list[tuple[str, object]] = []
    coordinator.pairing_code_ready.connect(lambda code, candidate: offered.append((code, candidate)))

    link, candidate = _reach_pairing_candidate(coordinator)

    assert len(offered) == 1
    assert offered[0][1] is candidate
    assert coordinator._link is None
    assert coordinator.state is LinkState.SEARCHING


def test_a_real_incoming_connection_during_pairing_shows_the_code_not_the_link(tmp_path, qtbot):
    """Не заглушка: настоящее TLS-соединение через настоящий PeerListener
    координатора приводит к показу кода, а не к обмену данными."""
    coordinator, _ = _make_coordinator(tmp_path)
    coordinator.begin_pairing()
    assert coordinator._listener.listen(0) is True

    remote_identity = load_or_create(tmp_path / "remote")
    remote = PeerLink(remote_identity)
    with qtbot.waitSignal(remote.connected, timeout=5000):
        remote.connect_to("127.0.0.1", coordinator._listener.port, None)

    remote.send(
        Message(
            MessageType.PAIR_REQUEST,
            {
                "origin_id": "2" * 32,
                "machine_name": "LAPTOP-TWO",
                "protocol_major": PROTOCOL_MAJOR,
                "protocol_minor": 0,
            },
            b"",
        )
    )

    offered: list[tuple[str, object]] = []
    coordinator.pairing_code_ready.connect(lambda code, candidate: offered.append((code, candidate)))
    qtbot.waitUntil(lambda: bool(offered), timeout=5000)

    assert coordinator._link is None
    assert coordinator.state is not LinkState.CONNECTED

    coordinator.stop()
    remote.close()


def test_an_incoming_pairing_link_is_deduplicated(tmp_path):
    """Во время связывания звонят оба - лишнее соединение закрывается, но ни
    одно из них само по себе не становится обменом данными."""
    coordinator, _ = _make_coordinator(tmp_path)
    coordinator.begin_pairing()
    first = _FakeLink()
    second = _FakeLink()

    coordinator._on_incoming_link(first)
    coordinator._on_incoming_link(second)

    assert coordinator._pairing_link is first
    assert first.closed is False
    assert second.closed is True
    assert coordinator._link is None


def test_an_incoming_operational_link_is_deduplicated_once_connected(tmp_path, qapp):
    """Тот же дедуп, но уже для рабочей связи после связывания."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id="2" * 32)
    first = _FakeLink(peer_fingerprint="f" * 64)
    second = _FakeLink(peer_fingerprint="f" * 64)

    coordinator._on_incoming_link(first)
    coordinator._on_incoming_link(second)

    assert coordinator._link is first
    assert first.closed is False
    assert second.closed is True
    coordinator.stop()


# ---------------------------------------------------------------------- свойство 3 и 4: подтверждение
# нужно с обеих сторон


def test_local_confirmation_alone_does_not_enable_the_exchange(tmp_path):
    coordinator, trust = _make_coordinator(tmp_path)
    link, candidate = _reach_pairing_candidate(coordinator)

    coordinator.confirm_pairing(candidate)

    assert any(message.type is MessageType.PAIR_CONFIRM for message in link.sent)
    assert trust.peer() is None
    assert coordinator._link is None
    assert coordinator.state is LinkState.SEARCHING


def test_local_rejection_sends_a_negative_confirmation_before_closing(tmp_path):
    coordinator, trust = _make_coordinator(tmp_path)
    link, candidate = _reach_pairing_candidate(coordinator)
    events: list[str] = []
    original_send = link.send
    original_close = link.close

    def record_send(message):
        events.append(f"send:{message.type.name}:{message.header.get('agree')}")
        original_send(message)

    def record_close():
        events.append("close")
        original_close()

    link.send = record_send
    link.close = record_close

    coordinator.reject_pairing(candidate)

    assert events == ["send:PAIR_CONFIRM:False", "close"]
    assert trust.peer() is None
    assert coordinator.state is LinkState.UNPAIRED
    assert coordinator._pairing is False
    assert coordinator._pairing_link is None
    assert coordinator._pairing_candidate is None


def test_a_stale_dialog_cannot_confirm_a_new_link_with_the_same_candidate(tmp_path):
    coordinator, trust = _make_coordinator(tmp_path)
    old_link, old_candidate = _reach_pairing_candidate(coordinator)

    coordinator.begin_pairing()
    new_link, new_candidate = _reach_pairing_candidate(
        coordinator,
        origin_id=old_candidate.origin_id,
        machine_name=old_candidate.machine_name,
        fingerprint=old_candidate.fingerprint,
        address=old_candidate.address,
        begin_pairing=False,
    )

    coordinator.confirm_pairing(old_candidate)

    assert old_link.closed is True
    assert coordinator._pairing_link is new_link
    assert coordinator._pairing_candidate is new_candidate
    assert not any(message.type is MessageType.PAIR_CONFIRM for message in new_link.sent)
    assert coordinator._local_agreed is False
    assert trust.peer() is None


def test_a_stale_dialog_cannot_reject_a_new_link_with_the_same_candidate(tmp_path):
    coordinator, trust = _make_coordinator(tmp_path)
    old_link, old_candidate = _reach_pairing_candidate(coordinator)

    coordinator.begin_pairing()
    new_link, new_candidate = _reach_pairing_candidate(
        coordinator,
        origin_id=old_candidate.origin_id,
        machine_name=old_candidate.machine_name,
        fingerprint=old_candidate.fingerprint,
        address=old_candidate.address,
        begin_pairing=False,
    )

    coordinator.reject_pairing(old_candidate)

    assert old_link.closed is True
    assert coordinator._pairing is True
    assert coordinator._pairing_link is new_link
    assert coordinator._pairing_candidate is new_candidate
    assert not any(message.type is MessageType.PAIR_CONFIRM for message in new_link.sent)
    assert new_link.closed is False
    assert trust.peer() is None


def test_a_stale_link_cannot_confirm_a_new_pairing_after_pairing_is_restarted(tmp_path):
    """Согласие принадлежит конкретной TLS-связи, а не общему состоянию.

    Даже если закрытый fake поздно испустит оба своих сигнала, они не могут
    подтвердить или сбросить кандидата, появившегося после нового begin_pairing().
    """
    coordinator, trust = _make_coordinator(tmp_path)
    old_link = _LateSignalLink(peer_fingerprint="a" * 64)
    old_link, _old_candidate = _reach_pairing_candidate(
        coordinator,
        origin_id="2" * 32,
        fingerprint="a" * 64,
        link=old_link,
    )

    coordinator.begin_pairing()
    new_link, new_candidate = _reach_pairing_candidate(
        coordinator,
        origin_id="3" * 32,
        fingerprint="b" * 64,
        begin_pairing=False,
    )
    coordinator.confirm_pairing(new_candidate)

    old_link.message_received.emit(Message(MessageType.PAIR_CONFIRM, {"agree": True}, b""))
    old_link.disconnected.emit("поздний сигнал закрытой связи")

    assert trust.peer() is None
    assert coordinator._link is None
    assert coordinator.service._link is None
    assert old_link.closed is True
    assert coordinator._pairing is True
    assert coordinator._pairing_link is new_link
    assert coordinator._pairing_candidate is new_candidate
    assert coordinator._local_agreed is True
    assert coordinator._remote_agreed is False
    assert new_link.closed is False
    assert coordinator.state is LinkState.SEARCHING


def test_stop_fully_ends_pairing_and_stale_signals_are_harmless(tmp_path):
    coordinator, trust = _make_coordinator(tmp_path)
    link, candidate = _reach_pairing_candidate(coordinator)
    coordinator.confirm_pairing(candidate)

    coordinator.stop()
    link.message_received.emit(Message(MessageType.PAIR_CONFIRM, {"agree": True}, b""))
    link.disconnected.emit("поздний сигнал после stop")

    assert link.closed is True
    assert coordinator._pairing is False
    assert coordinator._pairing_link is None
    assert coordinator._pairing_candidate is None
    assert coordinator._local_agreed is False
    assert coordinator._remote_agreed is False
    assert coordinator._pairing_window.isActive() is False
    assert trust.peer() is None
    assert coordinator._link is None
    assert coordinator.service._link is None
    assert coordinator.state is LinkState.UNPAIRED


def test_confirmation_from_both_sides_enables_the_exchange(tmp_path):
    coordinator, trust = _make_coordinator(tmp_path)
    link, candidate = _reach_pairing_candidate(coordinator)
    coordinator.confirm_pairing(candidate)

    link.message_received.emit(Message(MessageType.PAIR_CONFIRM, {"agree": True}, b""))

    assert trust.peer() is not None
    assert trust.peer().origin_id == "2" * 32
    assert coordinator.peer.machine_name == "LAPTOP-TWO"
    assert coordinator._link is link
    assert coordinator.state is LinkState.CONNECTED
    assert coordinator.service._link is link
    coordinator.stop()


def test_remote_confirmation_before_local_also_waits_for_both(tmp_path):
    """Порядок подтверждений не важен - важно, что оба произошли."""
    coordinator, trust = _make_coordinator(tmp_path)
    link, candidate = _reach_pairing_candidate(coordinator)

    link.message_received.emit(Message(MessageType.PAIR_CONFIRM, {"agree": True}, b""))
    assert trust.peer() is None
    assert coordinator._link is None

    coordinator.confirm_pairing(candidate)
    assert trust.peer() is not None
    assert coordinator._link is link
    coordinator.stop()


def test_the_peer_declining_confirmation_returns_to_unpaired(tmp_path):
    coordinator, trust = _make_coordinator(tmp_path)
    link, candidate = _reach_pairing_candidate(coordinator)
    coordinator.confirm_pairing(candidate)

    link.message_received.emit(Message(MessageType.PAIR_CONFIRM, {"agree": False}, b""))

    assert trust.peer() is None
    assert coordinator.state is LinkState.UNPAIRED
    assert link.closed is True
    assert coordinator._pairing is False
    assert coordinator._pairing_link is None
    assert coordinator._pairing_candidate is None
    assert coordinator._local_agreed is False
    assert coordinator._remote_agreed is False


def test_forgetting_a_peer_returns_to_unpaired(tmp_path):
    coordinator, trust = _make_coordinator(tmp_path)
    link, candidate = _reach_pairing_candidate(coordinator)
    coordinator.confirm_pairing(candidate)
    link.message_received.emit(Message(MessageType.PAIR_CONFIRM, {"agree": True}, b""))

    coordinator.forget_peer()

    assert trust.peer() is None
    assert coordinator.state is LinkState.UNPAIRED


# ---------------------------------------------------------------------- I2: забыть компьютер
# останавливает всё, что могло бы само себя перезавести


def test_forgetting_a_peer_stops_the_silence_watchdog_and_retry_timer(tmp_path, qapp):
    """До исправления forget_peer() не трогал таймеры: сторож молчания по уже
    закрытой связи срабатывал спустя SILENCE_LIMIT_MS, _drop() запускал
    _retry, а _try_connect() (пира уже нет) включал маячок навсегда."""
    coordinator, trust = _make_coordinator(tmp_path)
    link, candidate = _reach_pairing_candidate(coordinator)
    coordinator.confirm_pairing(candidate)
    link.message_received.emit(Message(MessageType.PAIR_CONFIRM, {"agree": True}, b""))
    assert coordinator._silence.isActive() is True

    coordinator.forget_peer()

    assert coordinator._silence.isActive() is False
    assert coordinator._retry.isActive() is False


def test_forgetting_a_peer_stops_the_beacon_from_announcing_forever(tmp_path, qtbot):
    """Проверено исполнением в ревью: через 30 секунд узел начинал каждые 2
    секунды объявлять всей сети свой идентификатор, имя машины и отпечаток -
    бессрочно, вне режима связывания. Сокращаем сторож молчания, чтобы не
    ждать настоящие 30 секунд, и убеждаемся, что после forget_peer() он не
    воскрешает маячок."""
    coordinator, trust = _make_coordinator(tmp_path)
    link, candidate = _reach_pairing_candidate(coordinator)
    coordinator.confirm_pairing(candidate)
    link.message_received.emit(Message(MessageType.PAIR_CONFIRM, {"agree": True}, b""))

    coordinator.forget_peer()

    # Даже если бы сторож молчания и таймер повторов остались взведены (то,
    # что чинит именно forget_peer), поиск не должен включаться заново: без
    # доверенного пира _try_connect() обязан отказаться работать.
    coordinator._try_connect()

    assert coordinator._discovery._timer.isActive() is False
    assert coordinator.state is LinkState.UNPAIRED


def test_try_connect_refuses_to_search_without_a_trusted_peer(tmp_path, qapp):
    """I2: попытки соединения отказываются работать, когда доверенного пира нет."""
    coordinator, _ = _make_coordinator(tmp_path)
    assert coordinator.peer is None

    coordinator._try_connect()

    assert coordinator._discovery._timer.isActive() is False
    assert coordinator.state is LinkState.UNPAIRED


# ---------------------------------------------------------------------- I3: ручной адрес
# закрывает прежнюю связь, а не открывает вторую


def test_manual_address_bootstraps_pairing_without_a_discovery_beacon(
    tmp_path, monkeypatch
):
    """An isolated unpaired client must dial the address entered by the user."""
    coordinator, _ = _make_coordinator(tmp_path)
    calls: list[tuple[str, int, str | None]] = []

    class _RecordingLink(QObject):
        connected = Signal(str)

        def __init__(self, identity, parent=None) -> None:
            super().__init__(parent)

        def connect_to(self, address, port, expected_fingerprint) -> None:
            calls.append((address, port, expected_fingerprint))

        def close(self) -> None:
            pass

    monkeypatch.setattr(coordinator_module, "PeerLink", _RecordingLink)

    coordinator.set_manual_address(" 192.168.1.42 ")
    coordinator.begin_pairing()

    try:
        assert calls == [("192.168.1.42", TCP_PORT, None)]
    finally:
        coordinator.stop()


def test_setting_a_manual_address_while_connected_closes_the_previous_link(tmp_path, monkeypatch):
    """До исправления set_manual_address() соединялся, не закрыв прежнюю
    связь: старая оставалась живой и продолжала доставлять сообщения, а
    повторный attach_link() у сервиса подключал offer_ready второй раз к тому
    же слоту - каждое копирование уходило бы на второй компьютер дважды."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    first_link = _FakeLink()
    coordinator._on_connected(first_link)
    assert coordinator._link is first_link
    assert coordinator.service._link is first_link

    class _RecordingLink(QObject):
        connected = Signal(str)
        disconnected = Signal(str)
        message_received = Signal(object)

        def __init__(self, identity, parent=None) -> None:
            super().__init__(parent)
            self.sent: list[object] = []

        def connect_to(self, address, port, expected_fingerprint) -> None:
            # Симулирует немедленное успешное рукопожатие - тесту нужен уже
            # установленный self._link, чтобы проверить, что он единственный.
            self.connected.emit(expected_fingerprint or "")

        def send(self, message) -> None:
            self.sent.append(message)

        def close(self) -> None:
            pass

    monkeypatch.setattr(coordinator_module, "PeerLink", _RecordingLink)

    coordinator.set_manual_address("192.168.1.9")

    assert first_link.closed is True
    second_link = coordinator._link
    assert second_link is not first_link
    assert isinstance(second_link, _RecordingLink)

    # offer_ready подключается к _send_offer заново в attach_link(); без
    # detach_link() перед новой попыткой он остался бы подключён и от первого
    # раза тоже, и один offer_ready.emit() ушёл бы на провод дважды.
    second_link.sent.clear()
    from duo_input.clipboard.offer import ClipboardOffer as _Offer

    coordinator.service.offer_ready.emit(_Offer(origin_id="x", seq=1, descriptors=()))
    assert len(second_link.sent) == 1
    coordinator.stop()


# ---------------------------------------------------------------------- согласование возможностей (HELLO)


@pytest.fixture
def coordinator_with_link(tmp_path, qapp):
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id="b" * 32)
    link = _FakeLink()
    coordinator._on_connected(link)
    yield coordinator, link
    coordinator.stop()


def test_the_minor_version_moved_and_the_major_version_did_not():
    # Подъём major разорвал бы связь со всеми существующими сборками. Новые
    # типы сообщений в этом не нуждаются: их закрывает capability.
    assert (PROTOCOL_MAJOR, PROTOCOL_MINOR) == (1, 1)


def test_hello_announces_both_capabilities(coordinator_with_link):
    coordinator, link = coordinator_with_link

    hello = next(m for m in link.sent if m.type is MessageType.HELLO)

    assert hello.header["capabilities"] == list(CAPABILITIES)


def test_a_peer_that_announces_files_is_recorded_as_supporting_them(coordinator_with_link):
    coordinator, link = coordinator_with_link

    link.deliver(
        Message(
            MessageType.HELLO,
            {
                "protocol_major": PROTOCOL_MAJOR,
                "protocol_minor": PROTOCOL_MINOR,
                "origin_id": "b" * 32,
                "machine_name": "PC2",
                "capabilities": [CAPABILITY_CLIPBOARD, CAPABILITY_FILES],
            },
            b"",
        )
    )

    assert coordinator.peer_supports(CAPABILITY_FILES)


def test_a_peer_with_no_capabilities_key_is_treated_as_clipboard_only(coordinator_with_link):
    # Это и есть совместимость со старым клиентом: он не объявляет ничего,
    # мы не посылаем ему FILE_*, и его буфер обмена продолжает работать.
    coordinator, link = coordinator_with_link

    link.deliver(
        Message(
            MessageType.HELLO,
            {
                "protocol_major": PROTOCOL_MAJOR,
                "protocol_minor": 0,
                "origin_id": "b" * 32,
                "machine_name": "OldPC",
            },
            b"",
        )
    )

    assert coordinator.peer_supports(CAPABILITY_CLIPBOARD)
    assert not coordinator.peer_supports(CAPABILITY_FILES)


def test_a_capabilities_value_that_is_not_a_list_is_ignored_rather_than_trusted(
    coordinator_with_link,
):
    coordinator, link = coordinator_with_link

    link.deliver(
        Message(
            MessageType.HELLO,
            {
                "protocol_major": PROTOCOL_MAJOR,
                "protocol_minor": PROTOCOL_MINOR,
                "origin_id": "b" * 32,
                "machine_name": "PC2",
                "capabilities": CAPABILITY_FILES,
            },
            b"",
        )
    )

    assert not coordinator.peer_supports(CAPABILITY_FILES), (
        "строка возможности содержит саму себя как подстроку - проверка через "
        "`in` по строке приняла бы её, и мы послали бы FILE_* туда, где их не ждут"
    )


def test_capabilities_are_announced_before_they_are_known(coordinator_with_link):
    coordinator, _link = coordinator_with_link

    assert coordinator.peer_capabilities == frozenset(), (
        "до HELLO мы не знаем ничего, и это не то же самое, что знать про clipboard"
    )


def _hello(header_extra: dict | None = None, *, protocol_major: int = PROTOCOL_MAJOR) -> Message:
    """HELLO от пира, с подменой любого поля заголовка."""
    header = {
        "protocol_major": protocol_major,
        "protocol_minor": PROTOCOL_MINOR,
        "origin_id": "b" * 32,
        "machine_name": "PC2",
    }
    header.update(header_extra or {})
    return Message(MessageType.HELLO, header, b"")


def test_a_capabilities_value_of_null_is_treated_as_a_legacy_peer(coordinator_with_link):
    # json.loads возвращает None для `"capabilities": null`. По смыслу это
    # отсутствие объявления, а не объявление пустоты, и путь должен совпадать
    # с путём отсутствующего ключа.
    coordinator, link = coordinator_with_link

    link.deliver(_hello({"capabilities": None}))

    assert coordinator.peer_capabilities == frozenset({CAPABILITY_CLIPBOARD})
    assert not coordinator.peer_supports(CAPABILITY_FILES)


def test_a_capabilities_list_of_non_strings_announces_nothing(coordinator_with_link):
    # Список - правильного типа, а его содержимое - нет. Ни один элемент не
    # должен превратиться в объявленную возможность, и ни один не должен
    # уронить разбор.
    coordinator, link = coordinator_with_link

    link.deliver(
        _hello(
            {
                "capabilities": [
                    7,
                    None,
                    [CAPABILITY_FILES],
                    {CAPABILITY_FILES: True},
                    CAPABILITY_FILES.encode("ascii"),
                ]
            }
        )
    )

    assert coordinator.peer_capabilities == frozenset()
    assert not coordinator.peer_supports(CAPABILITY_FILES)


def test_a_capabilities_mapping_is_ignored_rather_than_iterated_into_keys(coordinator_with_link):
    # Проверка `isinstance(announced, list)` держится именно на этом случае.
    # Для строки "files/1" её снятие безвредно по случайности: перебор строки
    # даёт символы, и "files/1" целиком в набор не попадает. А перебор словаря
    # даёт ключи - и пир, приславший {"files/1": true}, был бы засчитан как
    # умеющий файлы. Тест на строке проходит и без проверки; этот - нет.
    coordinator, link = coordinator_with_link

    link.deliver(_hello({"capabilities": {CAPABILITY_CLIPBOARD: True, CAPABILITY_FILES: True}}))

    assert not coordinator.peer_supports(CAPABILITY_FILES)
    assert coordinator.peer_capabilities == frozenset({CAPABILITY_CLIPBOARD}), (
        "объявление неправильной формы - это не объявление; пир считается устаревшим"
    )


def test_a_non_string_does_not_take_the_strings_beside_it_down(coordinator_with_link):
    coordinator, link = coordinator_with_link

    link.deliver(
        _hello({"capabilities": [CAPABILITY_CLIPBOARD, 7, CAPABILITY_FILES]})
    )

    assert coordinator.peer_capabilities == frozenset(
        {CAPABILITY_CLIPBOARD, CAPABILITY_FILES}
    )


def test_an_empty_capabilities_list_is_not_the_same_as_a_legacy_peer(coordinator_with_link):
    # Пустой список - это пир, который умеет объявлять и объявил, что не умеет
    # ничего. Дописывать ему clipboard/1 значило бы объявить за него.
    coordinator, link = coordinator_with_link

    link.deliver(_hello({"capabilities": []}))

    assert coordinator.peer_capabilities == frozenset()
    assert not coordinator.peer_supports(CAPABILITY_CLIPBOARD)
    assert not coordinator.peer_supports(CAPABILITY_FILES)


def test_capabilities_known_carries_the_frozenset_the_peer_announced(coordinator_with_link):
    coordinator, link = coordinator_with_link
    # PySide6 держит на получателя слабую ссылку: обработчик, которого никто
    # не держит, молча собирается сборщиком мусора, и сигнал перестаёт
    # работать без единой ошибки. Владелец здесь - само имя `heard`, живущее
    # до конца теста.
    heard: list = []
    coordinator.capabilities_known.connect(heard.append)

    link.deliver(_hello({"capabilities": [CAPABILITY_CLIPBOARD, CAPABILITY_FILES]}))

    assert heard == [frozenset({CAPABILITY_CLIPBOARD, CAPABILITY_FILES})]
    assert isinstance(heard[0], frozenset), "получатель не должен уметь править объявление пира"


def test_hello_and_capability_boundaries_are_logged(coordinator_with_link, caplog):
    coordinator, link = coordinator_with_link
    caplog.set_level("INFO", logger="duo_input.clipboard.coordinator")

    link.deliver(_hello({"capabilities": [CAPABILITY_CLIPBOARD, CAPABILITY_FILES]}))

    messages = [record.getMessage() for record in caplog.records]
    assert any("peer_hello_received" in message for message in messages)
    assert any(
        "peer_capabilities_known" in message
        and "clipboard/1" in message
        and "files/2" in message
        for message in messages
    )


def test_a_hello_on_the_wrong_major_version_never_reaches_the_parser(coordinator_with_link):
    coordinator, link = coordinator_with_link
    heard: list = []
    coordinator.capabilities_known.connect(heard.append)

    link.deliver(_hello({"capabilities": [CAPABILITY_CLIPBOARD]}))
    assert heard == [frozenset({CAPABILITY_CLIPBOARD})], (
        "сигнал обязан быть живым до основной проверки, иначе она пройдёт впустую"
    )

    link.deliver(
        _hello(
            {"capabilities": [CAPABILITY_CLIPBOARD, CAPABILITY_FILES]},
            protocol_major=PROTOCOL_MAJOR + 1,
        )
    )

    assert heard == [frozenset({CAPABILITY_CLIPBOARD})], (
        "связь уже разорвана по расхождению major - объявлениям из того же "
        "кадра верить нельзя"
    )
    assert not coordinator.peer_supports(CAPABILITY_FILES)


def test_reconnecting_forgets_what_the_previous_build_of_the_peer_could_do(
    coordinator_with_link,
):
    # Переподключение может прийтись на другую сборку пира. Унести на неё
    # возможности прежней - это ровно тот способ послать FILE_* туда, где их
    # не ждут, который вся эта задача и закрывает.
    coordinator, link = coordinator_with_link
    link.deliver(_hello({"capabilities": [CAPABILITY_CLIPBOARD, CAPABILITY_FILES]}))
    assert coordinator.peer_supports(CAPABILITY_FILES)

    second_link = _FakeLink()
    coordinator._on_connected(second_link)

    assert coordinator.peer_capabilities == frozenset(), (
        "до HELLO от новой связи мы снова не знаем ничего"
    )


def test_the_live_link_is_reachable_without_reaching_into_a_private_attribute(
    coordinator_with_link,
):
    # Task 4.2 нужна живая связь из другого модуля, а частный атрибут,
    # пересекающий границу модуля, гниёт первым.
    coordinator, link = coordinator_with_link

    assert coordinator.link is link

    coordinator._on_disconnected("кабель выдернули")

    assert coordinator.link is None


def test_a_peer_speaking_the_first_file_protocol_is_not_file_capable(coordinator_with_link):
    # files/1 нёс манифест в двухбайтовом заголовке и не знал read_id. Сборка
    # с files/2 не может ни прочесть его объявление, ни получить ответ на своё
    # чтение, поэтому такой пир для файлов - устаревший, а буфер обмена с ним
    # остаётся.
    coordinator, link = coordinator_with_link

    link.deliver(_hello({"capabilities": [CAPABILITY_CLIPBOARD, "files/1"]}))

    assert CAPABILITY_FILES != "files/1"
    assert not coordinator.peer_supports(CAPABILITY_FILES)
    assert coordinator.peer_supports(CAPABILITY_CLIPBOARD)


def test_the_pairing_button_does_not_disrupt_a_live_link(tmp_path, qapp):
    # Регресс #2: у уже связанных нажатие «Связать компьютеры» не должно рвать
    # рабочую связь ради нового мультикаст-поиска.
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id="2" * 32)
    link = _FakeLink(peer_fingerprint="f" * 64, peer_address="192.168.1.5")
    coordinator._on_connected(link)
    assert coordinator._link is link

    coordinator.begin_pairing()

    assert coordinator._link is link
    assert not link.closed
    assert not coordinator._pairing


def test_the_pairing_button_reconnects_a_trusted_peer_without_searching(tmp_path):
    # Регресс #2: у доверенного, но пока не подключённого пира кнопка должна
    # воссоединять по сохранённому адресу (unicast), а не запускать discovery.
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id="2" * 32)
    calls = []
    coordinator._try_connect = lambda: calls.append(True)

    coordinator.begin_pairing()

    assert calls == [True]
    assert not coordinator._pairing


def test_the_pairing_button_still_searches_for_a_first_time_peer(tmp_path, qapp):
    # Без доверенного пира кнопка по-прежнему запускает связывание/поиск.
    coordinator, _ = _make_coordinator(tmp_path)

    coordinator.begin_pairing()

    assert coordinator._pairing


# ---------------------------------------------------------------------- адреса от платы


def test_the_last_good_address_is_tried_first_then_the_boards(tmp_path, dial):
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    coordinator._retry = _FakeTimer()
    # 192.168.1.5 - сохранённый last_address; в списке платы он повторяется.
    # Список изменился, а набора не было - coordinator звонит сам, первым
    # кандидатом (Task 15, п.4), поэтому первая попытка видна уже здесь, без
    # явного _try_connect().
    coordinator.set_board_addresses(["10.0.0.2", "192.168.1.5", "10.0.0.3"])

    for _ in range(2):
        dial[-1].disconnected.emit("refused")
        coordinator._try_connect()  # то, что сделал бы сработавший _retry

    assert [link.address for link in dial] == ["192.168.1.5", "10.0.0.2", "10.0.0.3"]


def test_the_next_candidate_is_tried_without_the_backoff(tmp_path, dial):
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    timer = _FakeTimer()
    coordinator._retry = timer
    coordinator.set_board_addresses(["10.0.0.2"])

    coordinator._try_connect()
    dial[-1].disconnected.emit("refused")

    assert timer.starts == [0]
    assert coordinator._attempt == 0


def test_the_backoff_starts_only_after_the_whole_list_failed(tmp_path, dial):
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    timer = _FakeTimer()
    coordinator._retry = timer
    coordinator.set_board_addresses(["10.0.0.2"])

    coordinator._try_connect()
    dial[-1].disconnected.emit("refused")
    coordinator._try_connect()
    dial[-1].disconnected.emit("refused")

    assert timer.starts == [0, reconnect_delay_ms(0)]
    assert coordinator._candidate_index == 0


def test_a_manual_address_is_the_only_candidate(tmp_path, dial):
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    # set_board_addresses само звонит первым кандидатом (Task 15, п.4) - это
    # не то, что здесь проверяется, поэтому считаем набор ДО restore.
    coordinator.set_board_addresses(["10.0.0.2"])
    dialed_before_restore = len(dial)

    coordinator.restore_manual_address("192.168.7.7")

    assert coordinator._candidates() == ["192.168.7.7"]
    assert len(dial) == dialed_before_restore  # restore только запоминает, не звонит


def test_a_connection_remembers_and_announces_the_address_that_worked(tmp_path, dial):
    coordinator, trust = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    coordinator.set_board_addresses(["10.0.0.2"])
    announced = []
    coordinator.address_in_use.connect(announced.append)
    coordinator._candidate_index = 1

    coordinator._try_connect()
    dial[-1].connected.emit("f" * 64)

    try:
        assert announced == ["10.0.0.2"]
        assert trust.peer().last_address == "10.0.0.2"
        assert coordinator._candidate_index == 0
    finally:
        coordinator.stop()


def test_an_incoming_ipv4_mapped_address_is_stored_plain(tmp_path, qapp):
    coordinator, trust = _make_coordinator(tmp_path, peer_origin_id=SMALLEST_ORIGIN_ID)
    announced = []
    coordinator.address_in_use.connect(announced.append)

    coordinator._on_incoming_link(_FakeLink("f" * 64, peer_address="::ffff:192.168.1.44"))

    try:
        assert announced == ["192.168.1.44"]
        assert trust.peer().last_address == "192.168.1.44"
    finally:
        coordinator.stop()


def test_no_candidates_at_all_falls_back_to_searching(tmp_path, dial, monkeypatch):
    # TrustStore не хранит пира без адреса, поэтому «кандидатов нет» задаётся
    # напрямую: так выглядит пир, чей адрес неизвестен, а плата молчит.
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    monkeypatch.setattr(coordinator, "_candidates", lambda: [])
    try:
        coordinator._try_connect()

        assert coordinator.state is LinkState.SEARCHING
        assert dial == []
    finally:
        coordinator.stop()


def test_an_empty_board_address_is_not_a_candidate(tmp_path, dial):
    # set_board_addresses теперь может звонить само (Task 15, п.4) - `dial`
    # держит это на замене PeerLink, а не на настоящем сокете.
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    coordinator.set_board_addresses(["", "10.0.0.2", ""])

    assert coordinator._candidates() == ["192.168.1.5", "10.0.0.2"]


def test_a_candidate_that_vanished_mid_dial_does_not_make_the_round_skip_the_next(tmp_path, dial, qapp):
    """Пока шёл набор 10.0.0.2, плата прислала список уже без него. Следующим
    должен быть 10.0.0.3 - тот, кто занял его место, - а не конец круга с
    паузой, как было бы при сдвиге индекса по старому списку."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    timer = _FakeTimer()
    coordinator._retry = timer
    coordinator._board_addresses = ["10.0.0.2", "10.0.0.3"]
    coordinator._candidate_index = 1
    coordinator._try_connect()
    assert dial[-1].address == "10.0.0.2"

    coordinator.set_board_addresses(["10.0.0.3"])  # набор идёт - только запомнить
    dial[-1].disconnected.emit("refused")

    try:
        assert timer.starts == [0]  # следующий кандидат сразу, без паузы
        coordinator._try_connect()
        assert dial[-1].address == "10.0.0.3"
    finally:
        coordinator.stop()


def test_a_dialled_address_pushed_deeper_by_growth_does_not_get_dialled_twice(tmp_path, dial):
    """Пока шёл набор 10.0.0.2, плата прислала список, где перед прежними
    кандидатами появился новый адрес - 10.0.0.2 сдвинулся на другое место в
    списке. Следующим должен быть тот, кто стоит за НИМ на НОВОМ месте
    (10.0.0.3), а не тот, кто просто оказался на старом индексе набранного
    адреса плюс один - иначе набор впустую повторил бы уже испытанный адрес."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    timer = _FakeTimer()
    coordinator._retry = timer
    coordinator._board_addresses = ["10.0.0.2", "10.0.0.3"]
    coordinator._candidate_index = 1
    coordinator._try_connect()
    assert dial[-1].address == "10.0.0.2"

    coordinator.set_board_addresses(["10.0.0.9", "10.0.0.2", "10.0.0.3"])  # набор идёт - только запомнить
    dial[-1].disconnected.emit("refused")

    try:
        assert timer.starts == [0]  # следующий кандидат сразу, без паузы
        coordinator._try_connect()
        assert dial[-1].address == "10.0.0.3"  # не повтор 10.0.0.2
    finally:
        coordinator.stop()


def test_a_wrapped_index_still_advances_from_the_dialled_address(tmp_path, dial):
    """Индекс уже был перенесён через край списка (не первый круг по
    кандидатам), а затем набранный адрес не ответил. Следующий кандидат
    ищется от НАБРАННОГО адреса, а не слепым сдвигом уже перенесённого
    индекса - здесь оба пути совпадают, но это и есть проверка, что найденная
    ветка не портит уже проверенный перенос через край."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    timer = _FakeTimer()
    coordinator._retry = timer
    coordinator._board_addresses = ["10.0.0.2"]
    coordinator._candidate_index = 2  # за пределами списка ["192.168.1.5", "10.0.0.2"]
    coordinator._try_connect()
    assert dial[-1].address == "192.168.1.5"

    dial[-1].disconnected.emit("refused")

    try:
        assert timer.starts == [0]
        coordinator._try_connect()
        assert dial[-1].address == "10.0.0.2"
    finally:
        coordinator.stop()


def test_an_unnormalized_index_still_finds_slot_zero_when_the_dialled_address_vanishes(tmp_path, dial):
    """``_candidate_index`` переносится через край СРАЗУ при наборе (см.
    ``_try_connect``), а не только при чтении: если набранный адрес пропал из
    списка, а его место в слоте 0 занял новый (плата обновила last_address,
    пока шёл старый набор), ``_drop`` обязан попасть именно на этот новый
    адрес, а не решить, что круг уже кончился из-за индекса, оставшегося не
    перенесённым через край с прошлого набора."""
    coordinator, trust = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    timer = _FakeTimer()
    coordinator._retry = timer
    coordinator._board_addresses = ["10.0.0.2"]
    coordinator._candidate_index = 2  # за пределами списка ["192.168.1.5", "10.0.0.2"]
    coordinator._try_connect()
    assert dial[-1].address == "192.168.1.5"

    # Адрес пира сменился, пока шёл старый набор (например, входящее
    # соединение по новому адресу обновило last_address) - слот 0 теперь
    # занимает не тот, кого набирали.
    trust.update_address("192.168.1.77")
    dial[-1].disconnected.emit("refused")

    try:
        assert timer.starts == [0]
        coordinator._try_connect()
        assert dial[-1].address == "192.168.1.77"
    finally:
        coordinator.stop()


# ------------------------------------------------------- автонабор при новом списке адресов платы


def test_a_new_board_address_list_dials_immediately_from_idle(tmp_path, dial, qapp):
    """(a) Звонящая сторона, простой: новый список от платы обязан
    набираться сразу, а не ждать следующего такта обмена (до пяти секунд).
    Первый кандидат - сохранённый last_address (192.168.1.5, см.
    _make_coordinator), как и при любом другом наборе - _candidates() ставит
    его первым; новый адрес от платы пополняет список тем же порядком, каким
    его увидел бы обычный _try_connect()."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)

    coordinator.set_board_addresses(["10.0.0.2"])

    assert len(dial) == 1
    assert dial[-1].address == "192.168.1.5"
    assert coordinator._candidates() == ["192.168.1.5", "10.0.0.2"]
    assert coordinator._dialing is True
    assert coordinator._candidate_index == 0


def test_the_same_board_address_list_again_does_not_redial(tmp_path, dial, qapp):
    """(b) Список не изменился - "изменился" проверяется по значению, а
    не звонить второй раз просто потому что обмен снова прислал тот же
    список. `_dialing` сброшен руками, как будто первая попытка уже как-то
    разрешилась - иначе одного только "набор уже идёт" хватило бы, чтобы
    скрыть отсутствие проверки на "список не менялся"."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    coordinator.set_board_addresses(["10.0.0.2"])
    assert len(dial) == 1
    coordinator._dialing = False

    coordinator.set_board_addresses(["10.0.0.2"])

    assert len(dial) == 1


def test_a_live_link_is_not_disturbed_by_a_new_board_address_list(tmp_path, dial, qapp):
    """(c) Живая связь уже есть - новый список от платы её не трогает."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    coordinator._link = _FakeLink()

    coordinator.set_board_addresses(["10.0.0.2"])

    try:
        assert dial == []
    finally:
        coordinator._link = None
        coordinator.stop()


def test_the_waiting_side_does_not_dial_on_a_new_board_address_list(tmp_path, dial, qapp):
    """(d) Ждущая сторона (наш origin_id больше) никогда не звонит сама -
    новый список от платы не должен это менять. `_try_connect()` сам по себе
    уже не звонит за ждущую сторону (внутренняя проверка того же условия),
    поэтому отсутствие звонка в `dial` не отличило бы удалённую здесь
    проверку от рабочей - в обоих случаях `dial` остаётся пустым. Наблюдаемая
    разница - таймер молчания: без проверки в set_board_addresses он
    перезапускался бы на каждый новый список от платы, хотя связь и так уже
    не идёт."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=SMALLEST_ORIGIN_ID)

    coordinator.set_board_addresses(["10.0.0.2"])

    try:
        assert dial == []
        assert coordinator._silence.isActive() is False
    finally:
        coordinator.stop()


def test_an_active_retry_timer_blocks_a_new_board_address_dial(tmp_path, dial, qapp):
    """(e) Таймер повтора уже тикает - новый список от платы ждёт его,
    а не набирает поверх него."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    fake_retry = _FakeTimer()
    coordinator._retry = fake_retry
    fake_retry.start(reconnect_delay_ms(0))

    coordinator.set_board_addresses(["10.0.0.2"])

    assert dial == []


def test_a_dial_already_in_flight_is_not_joined_by_a_second_one(tmp_path, dial, qapp):
    """Набор уже идёт (первый кандидат ещё не ответил ни успехом, ни
    отказом) - второй список от платы не должен запускать второй набор
    поверх первого."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    coordinator.set_board_addresses(["10.0.0.2"])
    assert len(dial) == 1
    assert coordinator._dialing is True

    coordinator.set_board_addresses(["10.0.0.3"])

    assert len(dial) == 1


def test_a_new_board_address_list_does_not_dial_while_blocked(tmp_path, dial, monkeypatch, qapp):
    """BLOCKED должен оставаться BLOCKED: занятый порт слушателя - это не
    что-то, что чинится звонком, а новый список адресов от платы не должен
    выглядеть так, будто он его чинит."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    monkeypatch.setattr(coordinator._listener, "listen", lambda port: False)
    coordinator.start()
    assert coordinator.state is LinkState.BLOCKED

    coordinator.set_board_addresses(["10.0.0.2"])

    try:
        assert dial == []
        assert coordinator.state is LinkState.BLOCKED
    finally:
        coordinator.stop()


def test_a_new_board_address_list_does_not_redial_after_protocol_mismatch(tmp_path, dial, qapp):
    """После PROTOCOL_MISMATCH пир уже признан несовместимым - новый список
    адресов от платы не должен запускать повторный набор именно этого пира:
    расхождение версии протокола не лечится переподключением (§11)."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    link = _FakeLink()
    coordinator._on_connected(link)
    coordinator._on_message(
        Message(MessageType.HELLO, {"protocol_major": PROTOCOL_MAJOR + 1}, b"")
    )
    assert coordinator.state is LinkState.PROTOCOL_MISMATCH

    coordinator.set_board_addresses(["10.0.0.2"])

    try:
        assert dial == []
        assert coordinator.state is LinkState.PROTOCOL_MISMATCH
    finally:
        coordinator.stop()


def test_a_new_board_address_list_dials_immediately_from_searching(tmp_path, dial, monkeypatch, qapp):
    """SEARCHING - доверенный пир есть, но кандидатов для набора пока нет
    (см. test_no_candidates_at_all_falls_back_to_searching - TrustStore не
    хранит пира без адреса, поэтому «кандидатов нет» задаётся тем же
    монкипатчем напрямую). Список от платы - единственное, что может
    запустить набор в этом состоянии: таймер повтора здесь не тикает вовсе
    (в отличие от DISCONNECTED после обычного разрыва), так что без ветки
    SEARCHING в guard'е немедленного набора соединение никогда бы не
    началось само."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    monkeypatch.setattr(coordinator, "_candidates", lambda: list(coordinator._board_addresses))
    coordinator._try_connect()
    assert coordinator.state is LinkState.SEARCHING
    assert dial == []
    assert coordinator._retry.isActive() is False

    coordinator.set_board_addresses(["10.0.0.2"])

    try:
        assert [link.address for link in dial] == ["10.0.0.2"]
    finally:
        coordinator.stop()


def test_pairing_blocks_a_new_board_address_dial(tmp_path, dial, qapp):
    """Идёт связывание - новый список от платы не должен запускать
    параллельный набор поверх него."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    coordinator._pairing = True

    coordinator.set_board_addresses(["10.0.0.2"])

    try:
        assert dial == []
    finally:
        coordinator._pairing = False
        coordinator.stop()


# ---------------------------------------------------------------------- маячок доверенного пира


def _peer_beacon(origin_id: str = LARGEST_ORIGIN_ID, fingerprint: str = "f" * 64):
    return coordinator_module.Beacon(
        origin_id=origin_id,
        machine_name="LAPTOP-TWO",
        fingerprint=fingerprint,
        port=TCP_PORT,
        protocol_major=PROTOCOL_MAJOR,
    )


def test_a_beacon_with_a_foreign_fingerprint_is_ignored(tmp_path, dial, qapp):
    """Совпал origin_id, но не отпечаток - это не наш пир (или его прежняя
    установка): ни звонка, ни адреса."""
    coordinator, trust = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)

    coordinator._on_peer_seen(_peer_beacon(fingerprint="e" * 64), "192.168.1.66")

    try:
        assert dial == []
        assert trust.peer().last_address == "192.168.1.5"
    finally:
        coordinator.stop()


def test_a_beacon_during_a_dial_in_flight_does_not_start_a_second_link(tmp_path, dial, qapp):
    """Маячок приходит каждые 2 с, а набор длится до 10 с: без проверки
    `_dialing` каждый маячок запускал бы ещё одну PeerLink поверх идущей."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    coordinator._try_connect()
    assert len(dial) == 1
    assert coordinator._dialing is True

    coordinator._on_peer_seen(_peer_beacon(), "192.168.1.99")

    try:
        assert len(dial) == 1
        assert coordinator._dialing is True
    finally:
        coordinator.stop()


def test_a_stream_of_beacons_creates_at_most_one_link(tmp_path, dial, qapp):
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)

    for _ in range(5):
        coordinator._on_peer_seen(_peer_beacon(), "192.168.1.99")

    try:
        assert len(dial) == 1
    finally:
        coordinator.stop()


def test_a_beacon_does_not_dial_while_connected(tmp_path, dial, qapp):
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    coordinator._on_connected(_FakeLink("f" * 64, peer_address="192.168.1.5"))
    assert coordinator.state is LinkState.CONNECTED

    coordinator._on_peer_seen(_peer_beacon(), "192.168.1.99")

    try:
        assert dial == []
        assert coordinator.state is LinkState.CONNECTED
    finally:
        coordinator.stop()


def test_a_beacon_does_not_dial_while_blocked(tmp_path, dial, monkeypatch, qapp):
    """Занятый порт слушателя маячок не чинит - BLOCKED остаётся BLOCKED."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    monkeypatch.setattr(coordinator._listener, "listen", lambda port: False)
    coordinator.start()
    assert coordinator.state is LinkState.BLOCKED

    coordinator._on_peer_seen(_peer_beacon(), "192.168.1.99")

    try:
        assert dial == []
        assert coordinator.state is LinkState.BLOCKED
    finally:
        coordinator.stop()


def test_a_beacon_does_not_redial_after_protocol_mismatch(tmp_path, dial, qapp):
    """Несовместимая версия не лечится переподключением (§11)."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    coordinator._on_connected(_FakeLink())
    coordinator._on_message(
        Message(MessageType.HELLO, {"protocol_major": PROTOCOL_MAJOR + 1}, b"")
    )
    assert coordinator.state is LinkState.PROTOCOL_MISMATCH

    coordinator._on_peer_seen(_peer_beacon(), "192.168.1.99")

    try:
        assert dial == []
        assert coordinator.state is LinkState.PROTOCOL_MISMATCH
    finally:
        coordinator.stop()


def test_the_waiting_side_does_not_react_to_a_beacon(tmp_path, dial, qapp):
    """Ждущая сторона не звонит и не перезапускает сторож молчания на каждый маячок."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=SMALLEST_ORIGIN_ID)

    coordinator._on_peer_seen(_peer_beacon(origin_id=SMALLEST_ORIGIN_ID), "192.168.1.99")

    try:
        assert dial == []
        assert coordinator._silence.isActive() is False
    finally:
        coordinator.stop()


def test_an_active_retry_timer_defers_a_beacon_dial(tmp_path, dial, qapp):
    """То же правило, что у платы: тикающий повтор не обгоняется."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    fake_retry = _FakeTimer()
    coordinator._retry = fake_retry
    fake_retry.start(reconnect_delay_ms(0))

    coordinator._on_peer_seen(_peer_beacon(), "192.168.1.99")

    assert dial == []


def test_stop_resets_dialing_and_candidate_index(tmp_path, dial):
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    coordinator._retry = _FakeTimer()
    coordinator.set_board_addresses(["10.0.0.2", "10.0.0.3"])
    coordinator._try_connect()
    dial[-1].disconnected.emit("refused")
    assert coordinator._candidate_index == 1

    coordinator.stop()

    assert coordinator._candidate_index == 0
    assert coordinator._dialing is False


def test_forget_peer_resets_dialing_and_candidate_index(tmp_path, dial):
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    coordinator._retry = _FakeTimer()
    coordinator.set_board_addresses(["10.0.0.2", "10.0.0.3"])
    coordinator._try_connect()
    dial[-1].disconnected.emit("refused")
    assert coordinator._candidate_index == 1

    coordinator.forget_peer()

    assert coordinator._candidate_index == 0
    assert coordinator._dialing is False


def test_update_address_is_skipped_when_the_address_did_not_change(tmp_path):
    """update_address вызывается только когда адрес действительно сменился -
    иначе штамп времени последнего изменения дёргался бы на каждое обычное
    переподключение по тому же, уже сохранённому адресу."""
    coordinator, trust = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    calls: list[str] = []
    original = trust.update_address

    def recording(address):
        calls.append(address)
        return original(address)

    trust.update_address = recording
    link = _FakeLink(peer_fingerprint="f" * 64, peer_address="192.168.1.5")

    coordinator._on_connected(link)

    try:
        assert calls == []
        assert trust.peer().last_address == "192.168.1.5"
    finally:
        coordinator.stop()


def test_an_incoming_link_with_no_peer_address_does_not_touch_trust(tmp_path):
    """Пустой peer_address (заглушка без настоящего сокета) не должен ни
    что-то сохранять в доверие, ни оповещать несуществующим адресом."""
    coordinator, trust = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    announced = []
    coordinator.address_in_use.connect(announced.append)
    link = _FakeLink(peer_fingerprint="f" * 64, peer_address="")

    coordinator._on_incoming_link(link)

    try:
        assert announced == []
        assert trust.peer().last_address == "192.168.1.5"
    finally:
        coordinator.stop()


def test_the_candidate_index_wraps_when_the_list_shrinks(tmp_path, dial):
    """Индекс мог указывать в глубь более длинного списка кандидатов - набор
    ещё шёл по адресам от платы, - а затем плата прислала список короче.
    Без взятия по модулю следующий набор либо упал бы с IndexError, либо
    навсегда застрял на последнем элементе старой длины: оба хуже, чем откат
    к первому кандидату (последнему удачному адресу)."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    coordinator.set_board_addresses(["10.0.0.2", "10.0.0.3", "10.0.0.4"])
    coordinator._candidate_index = 2
    coordinator.set_board_addresses(["10.0.0.9"])  # кандидатов теперь только 2

    coordinator._try_connect()

    try:
        assert dial[-1].address == "192.168.1.5"
    finally:
        coordinator.stop()


def test_on_peer_seen_restarts_the_dial_from_the_fresh_address(tmp_path, dial, qapp):
    """Маячок от доверенного пира в простое - набор начинается с его адреса,
    а не с середины прежнего списка, где мог застрять неудачный набор."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    coordinator._board_addresses = ["10.0.0.2", "10.0.0.3"]  # список без автонабора
    coordinator._candidate_index = 2

    coordinator._on_peer_seen(_peer_beacon(), "192.168.1.99")

    try:
        assert dial[-1].address == "192.168.1.99"
    finally:
        coordinator.stop()


def test_begin_pairing_restarts_the_dial_from_the_last_good_address(tmp_path, dial):
    """«Связать» для уже доверенного, но не подключённого пира должно
    начинать набор с последнего удачного адреса, а не продолжаться с
    середины списка, в которой застрял предыдущий неудачный набор."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    coordinator.set_board_addresses(["10.0.0.2", "10.0.0.3"])
    coordinator._candidate_index = 2

    coordinator.begin_pairing()

    try:
        assert dial[-1].address == "192.168.1.5"
    finally:
        coordinator.stop()
