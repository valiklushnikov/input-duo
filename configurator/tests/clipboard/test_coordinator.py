"""Координатор: задержки переподключения, состояния и закрепление доверия."""

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

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[Message] = []
        self.closed = False

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


# ---------------------------------------------------------------------- задержки переповтора


def test_reconnect_delays_grow_and_then_stop_growing():
    delays = [reconnect_delay_ms(attempt) for attempt in range(7)]

    assert delays[: len(RECONNECT_DELAYS_MS)] == list(RECONNECT_DELAYS_MS)
    assert delays[-1] == RECONNECT_DELAYS_MS[-1]
    assert delays[-2] == RECONNECT_DELAYS_MS[-1]


def test_a_negative_attempt_still_gives_the_first_delay():
    assert reconnect_delay_ms(-1) == RECONNECT_DELAYS_MS[0]


def test_repeated_drops_grow_the_retry_delay_and_then_cap_it(tmp_path):
    """Не только чистая функция: сам координатор обязан растить и упирать паузу.

    `_drop` вызывает `reconnect_delay_ms(self._attempt)` и увеличивает счётчик
    попыток. Если бы счётчик не рос, каждая пауза была бы первой; если бы
    `reconnect_delay_ms` не упирался в потолок, шестая попытка подряд упала
    бы с IndexError.
    """
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


# ---------------------------------------------------------------------- состояния и доверие


def test_an_unpaired_coordinator_starts_unpaired(tmp_path):
    coordinator, _ = _make_coordinator(tmp_path)

    assert coordinator.state is LinkState.UNPAIRED
    assert coordinator.peer is None


def test_confirming_a_pairing_remembers_the_peer(tmp_path):
    coordinator, trust = _make_coordinator(tmp_path)
    candidate = PairingCandidate("2" * 32, "LAPTOP-TWO", "f" * 64, "192.168.1.5", 47654)

    coordinator.confirm_pairing(candidate)

    assert trust.peer().origin_id == "2" * 32
    assert coordinator.peer.machine_name == "LAPTOP-TWO"
    coordinator.stop()


def test_forgetting_a_peer_returns_to_unpaired(tmp_path):
    coordinator, trust = _make_coordinator(tmp_path)
    coordinator.confirm_pairing(
        PairingCandidate("2" * 32, "LAPTOP-TWO", "f" * 64, "192.168.1.5", 47654)
    )

    coordinator.forget_peer()

    assert trust.peer() is None
    assert coordinator.state is LinkState.UNPAIRED


def test_a_port_that_cannot_be_listened_on_is_reported_as_blocked(tmp_path, monkeypatch):
    coordinator, _ = _make_coordinator(tmp_path)
    coordinator.confirm_pairing(
        PairingCandidate("2" * 32, "LAPTOP-TWO", "f" * 64, "192.168.1.5", 47654)
    )
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
    # Предложение кода - это ещё не доверие: закрепляет только confirm_pairing.
    assert trust.peer() is None
    assert coordinator.state is LinkState.UNPAIRED


# ---------------------------------------------------------------------- кто звонит


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


# ---------------------------------------------------------------------- версия протокола


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


# ---------------------------------------------------------------------- сторож молчания


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


# ---------------------------------------------------------------------- входящие соединения


def test_a_second_incoming_link_is_closed_once_one_is_already_connected(tmp_path, qapp):
    """Во время парринга звонят оба - лишнее соединение закрывается."""
    coordinator, _ = _make_coordinator(tmp_path)
    first = _FakeLink()
    second = _FakeLink()

    coordinator._on_incoming_link(first)
    coordinator._on_incoming_link(second)

    assert coordinator._link is first
    assert first.closed is False
    assert second.closed is True
    coordinator.stop()


def test_a_real_incoming_connection_reaches_connected(tmp_path, qtbot):
    """Не заглушка: настоящее TLS-соединение через настоящий PeerListener координатора."""
    coordinator, _ = _make_coordinator(tmp_path)
    coordinator._listener.expect(None)  # режим парринга - примем кого угодно
    assert coordinator._listener.listen(0) is True

    remote_identity = load_or_create(tmp_path / "remote")
    remote = PeerLink(remote_identity)
    with qtbot.waitSignal(remote.connected, timeout=5000):
        remote.connect_to("127.0.0.1", coordinator._listener.port, coordinator._identity.fingerprint)

    qtbot.waitUntil(lambda: coordinator.state is LinkState.CONNECTED, timeout=5000)
    assert coordinator._link is not None

    coordinator.stop()
    remote.close()
