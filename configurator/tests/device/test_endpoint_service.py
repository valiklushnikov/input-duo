"""ПК2 разговаривает со своей U2 ровно об одном - об адресах."""

from __future__ import annotations

from dataclasses import replace

from duo_input.device.emulator import ErrorCode, U1Emulator
from duo_input.device.endpoint_service import EndpointService
from duo_input.device.qt_transport import SynchronousTransportLink
from duo_input.device.transport import AbstractByteTransport
from duo_input.generated.protocol import CdcMessageType
from duo_input.protocol.frame import decode_cdc_frame, encode_cdc_frame


def _service_with(emulator, qtbot):
    links = []

    def factory():
        link = SynchronousTransportLink(emulator)
        links.append(link)
        return link

    service = EndpointService(link_factory=factory, timeout_ms=500)
    return service, links


def test_the_first_call_opens_the_board_and_the_answer_follows(qtbot):
    # U1Emulator говорит тем же кадрированием и отвечает на HELLO и
    # EXCHANGE_ADDRESSES так же, как U2 - для этого сервиса разницы нет.
    emulator = U1Emulator()
    emulator.set_peer_addresses(["192.168.1.7"])
    service, links = _service_with(emulator, qtbot)
    received = []
    service.peer_addresses_received.connect(received.append)

    service.exchange_addresses(["10.0.0.2"])

    qtbot.waitUntil(lambda: received == [["192.168.1.7"]])
    assert emulator.local_addresses == ["10.0.0.2"]
    assert len(links) == 1


def test_no_board_means_no_attempt_and_no_error(qtbot):
    service = EndpointService(link_factory=lambda: None)
    assert service.exchange_addresses(["10.0.0.2"]) is False


def test_a_board_without_the_capability_is_left_alone(qtbot, monkeypatch):
    import duo_input.device.emulator as emulator_module
    from duo_input.generated.protocol import Capability

    monkeypatch.setattr(
        emulator_module,
        "DEVICE_CAPABILITIES",
        emulator_module.DEVICE_CAPABILITIES & ~int(Capability.ADDRESS_EXCHANGE),
    )
    emulator = U1Emulator()
    service, links = _service_with(emulator, qtbot)

    service.exchange_addresses(["10.0.0.2"])
    qtbot.waitUntil(lambda: service.unsupported)

    assert service.exchange_addresses(["10.0.0.2"]) is False
    assert len(links) == 1


def test_a_lost_board_is_reopened_on_the_next_call(qtbot):
    emulator = U1Emulator()
    service, links = _service_with(emulator, qtbot)
    received = []
    service.peer_addresses_received.connect(received.append)
    service.exchange_addresses(["10.0.0.2"])
    qtbot.waitUntil(lambda: len(received) == 1)

    links[0].link_lost.emit("unplugged")
    service.exchange_addresses(["10.0.0.2"])

    qtbot.waitUntil(lambda: len(received) == 2)
    assert len(links) == 2


# --------------------------------------------------------------- extra guards


class _MutatingTransport(AbstractByteTransport):
    """Rewrites device replies on the wire without touching the emulator.

    Mirrors ``test_device_service.py``'s helper of the same name: the mutate
    callback sees each decoded reply frame and may replace it (return a new
    frame) or leave it alone (return ``None``).
    """

    def __init__(self, emulator: U1Emulator, mutate) -> None:
        super().__init__()
        self._emulator = emulator
        self._mutate = mutate

    @property
    def is_open(self) -> bool:
        return self._emulator.is_open

    def open(self) -> None:
        self._emulator.open()

    def close(self) -> None:
        self._emulator.close()

    def write(self, data: bytes) -> bytes:
        raw = self._emulator.write(data)
        if not raw:
            return raw
        out = bytearray()
        for part in raw[:-1].split(b"\0"):
            frame = decode_cdc_frame(part + b"\0")
            out.extend(encode_cdc_frame(self._mutate(frame) or frame))
        return bytes(out)


def test_a_non_ok_error_reply_emits_nothing_and_keeps_the_link_usable(qtbot):
    # The first EXCHANGE_ADDRESSES reply is turned into a device error; the
    # second one is left alone, so the same link must still work afterwards.
    corrupt = [True]

    def mutate(frame):
        if frame.type is not CdcMessageType.EXCHANGE_ADDRESSES or not corrupt[0]:
            return None
        corrupt[0] = False
        payload = bytearray(frame.payload)
        payload[0] = ErrorCode.INVALID_REQUEST
        return replace(frame, payload=bytes(payload))

    emulator = U1Emulator()
    emulator.set_peer_addresses(["192.168.1.7"])
    links = []

    def factory():
        link = SynchronousTransportLink(_MutatingTransport(emulator, mutate))
        links.append(link)
        return link

    service = EndpointService(link_factory=factory, timeout_ms=500)
    received = []
    service.peer_addresses_received.connect(received.append)

    assert service.exchange_addresses(["10.0.0.2"]) is True
    qtbot.waitUntil(lambda: service._pending is None)
    assert received == []
    assert links[0].is_open is True

    assert service.exchange_addresses(["10.0.0.2"]) is True
    qtbot.waitUntil(lambda: received == [["192.168.1.7"]])
    assert len(links) == 1, "a working reply must not have forced a reopen"


def test_a_timeout_closes_the_link_so_the_next_call_reopens(qtbot):
    emulator = U1Emulator()
    emulator.inject_timeout()  # drops the very next reply - HELLO, here
    service, links = _service_with(emulator, qtbot)

    assert service.exchange_addresses(["10.0.0.2"]) is True
    qtbot.waitUntil(lambda: links[0].is_open is False, timeout=2000)

    received = []
    service.peer_addresses_received.connect(received.append)
    service.exchange_addresses(["10.0.0.2"])

    qtbot.waitUntil(lambda: received == [[]])
    assert len(links) == 2, "the timed-out link must have been reopened, not reused"


def test_a_second_call_while_a_request_is_pending_returns_false(qtbot):
    emulator = U1Emulator()
    emulator.set_peer_addresses(["192.168.1.7"])
    service, links = _service_with(emulator, qtbot)
    received = []
    service.peer_addresses_received.connect(received.append)

    service.exchange_addresses(["10.0.0.2"])
    qtbot.waitUntil(lambda: received == [["192.168.1.7"]])

    # Second request in flight: the reply has not been delivered yet because
    # SynchronousTransportLink answers through the event loop.
    assert service.exchange_addresses(["10.0.0.2"]) is True
    assert service.exchange_addresses(["10.0.0.2"]) is False

    qtbot.waitUntil(lambda: len(received) == 2)
    assert len(links) == 1


def test_a_payload_error_in_the_reply_closes_the_link(qtbot):
    # Truncate DEVICE_INFO so parse_device_info's size check fails - a
    # PayloadError raised while handling a reply, not a device-reported error.
    def mutate(frame):
        if frame.type is not CdcMessageType.DEVICE_INFO:
            return None
        return replace(frame, payload=bytes(frame.payload[:10]))

    emulator = U1Emulator()
    links = []

    def factory():
        link = SynchronousTransportLink(_MutatingTransport(emulator, mutate))
        links.append(link)
        return link

    service = EndpointService(link_factory=factory, timeout_ms=500)

    assert service.exchange_addresses(["10.0.0.2"]) is True
    qtbot.waitUntil(lambda: links[0].is_open is False)

    # The link was torn down, not merely left pending: the next call reopens.
    assert service.exchange_addresses(["10.0.0.2"]) is True
    qtbot.waitUntil(lambda: len(links) == 2)
