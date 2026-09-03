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

import duo_input.clipboard.coordinator as coordinator_module
from PySide6.QtCore import QObject, Signal

from duo_input.clipboard.coordinator import (
    RECONNECT_DELAYS_MS,
    TCP_PORT,
    ClipboardCoordinator,
    LinkState,
    reconnect_delay_ms,
)
from duo_input.clipboard.identity import load_or_create
from duo_input.clipboard.pairing import PairingCandidate
from duo_input.clipboard.peer import PeerLink
from duo_input.clipboard.trust import TrustedPeer, TrustStore
from duo_input.clipboard.wire import PROTOCOL_MAJOR, Message, MessageType

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


class _FakeTimer:
    """Заменитель `QTimer`, который просто запоминает, чем его попросили."""

    def __init__(self) -> None:
        self.starts: list[int] = []
        self.stopped = 0

    def start(self, ms: int) -> None:
        self.starts.append(ms)

    def stop(self) -> None:
        self.stopped += 1


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
) -> tuple[_FakeLink, PairingCandidate]:
    """Довести парринг до момента, когда код уже показан и кандидат известен,
    но ни одна из сторон ещё не подтвердила.
    """
    coordinator.begin_pairing()
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
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    link = _FakeLink()

    coordinator._on_connected(link)
    assert coordinator.state is LinkState.CONNECTED

    coordinator._on_message(Message(MessageType.HELLO, {"protocol_major": PROTOCOL_MAJOR + 1}, b""))

    assert coordinator.state is LinkState.DISCONNECTED
    assert link.closed is True
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
    coordinator.begin_pairing()
    assert coordinator._listener._expected_fingerprint is None  # во время связывания - кто угодно

    coordinator._pairing_window.setInterval(50)
    coordinator._pairing_window.start()
    qtbot.waitUntil(lambda: not coordinator._pairing, timeout=2000)

    assert coordinator._listener._expected_fingerprint == ""


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


def test_forgetting_a_peer_returns_to_unpaired(tmp_path):
    coordinator, trust = _make_coordinator(tmp_path)
    link, candidate = _reach_pairing_candidate(coordinator)
    coordinator.confirm_pairing(candidate)
    link.message_received.emit(Message(MessageType.PAIR_CONFIRM, {"agree": True}, b""))

    coordinator.forget_peer()

    assert trust.peer() is None
    assert coordinator.state is LinkState.UNPAIRED
