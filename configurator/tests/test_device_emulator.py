from __future__ import annotations

import hashlib
import struct
from dataclasses import replace
from pathlib import Path

import pytest

from duo_input.device.emulator import ErrorCode, U1Emulator
from duo_input.device.transport import AbstractByteTransport
from duo_input.domain.config_binary import compile_device_config, decode_device_config
from duo_input.domain.models import Macro, TargetMode
from duo_input.generated.protocol import (
    PROTOCOL_VERSION_MAJOR,
    PROTOCOL_VERSION_MINOR,
    Capability,
    CdcMessageType,
)
from duo_input.protocol.cobs import cobs_decode, cobs_encode
from duo_input.protocol.crc import crc32_ieee
from duo_input.protocol.frame import CdcFrame, FrameError, decode_cdc_frame, encode_cdc_frame


class _MemoryTransport(AbstractByteTransport):
    def write(self, data: bytes) -> bytes:
        if not self.is_open:
            raise RuntimeError("transport is closed")
        return bytes(data)


def test_abstract_transport_open_and_close_are_idempotent():
    transport = _MemoryTransport()

    assert not transport.is_open
    transport.open()
    transport.open()
    assert transport.is_open
    assert transport.write(b"abc") == b"abc"
    transport.close()
    transport.close()
    assert not transport.is_open


def _error(frame: CdcFrame) -> ErrorCode:
    return ErrorCode(frame.payload[0])


def _request(
    emulator: U1Emulator,
    message_type: CdcMessageType,
    payload: bytes = b"",
    sequence: int | None = None,
) -> CdcFrame:
    if sequence is None:
        sequence = 100 if emulator.last_sequence is None else (emulator.last_sequence + 1) & 0xFFFF
    return emulator.exchange(CdcFrame(message_type, sequence, payload))


def _hello(
    emulator: U1Emulator,
    capabilities: int | None = None,
    sequence: int | None = None,
) -> CdcFrame:
    if capabilities is None:
        capabilities = sum(int(capability) for capability in Capability)
    return _request(emulator, CdcMessageType.HELLO, struct.pack("<I", capabilities), sequence)


def _decode_stream(data: bytes) -> list[CdcFrame]:
    assert not data or data.endswith(b"\0")
    return [decode_cdc_frame(part + b"\0") for part in data[:-1].split(b"\0")] if data else []


def _wire_with_major(frame: CdcFrame, major: int) -> bytes:
    raw = bytearray(cobs_decode(encode_cdc_frame(frame)[:-1]))
    raw[2] = major
    raw[-4:] = crc32_ieee(raw[:-4]).to_bytes(4, "little")
    return cobs_encode(raw) + b"\0"


@pytest.fixture
def config_a() -> bytes:
    return Path("tests/vectors/config_vectors/valid_minimal.bin").read_bytes()


@pytest.fixture
def config_b(config_a: bytes) -> bytes:
    return compile_device_config(replace(decode_device_config(config_a), active_profile_id=2))


def _write_all(emulator: U1Emulator, package: bytes, *, verify: bool = True) -> None:
    digest = hashlib.sha256(package).digest()
    assert _error(_request(emulator, CdcMessageType.WRITE_BEGIN, struct.pack("<I", len(package)) + digest)) is ErrorCode.OK
    for offset in range(0, len(package), 512):
        reply = _request(
            emulator,
            CdcMessageType.WRITE_CHUNK,
            struct.pack("<I", offset) + package[offset : offset + 512],
        )
        assert _error(reply) is ErrorCode.OK
        assert struct.unpack_from("<I", reply.payload, 1)[0] == min(offset + 512, len(package))
    if verify:
        assert _error(_request(emulator, CdcMessageType.WRITE_VERIFY)) is ErrorCode.OK


def test_feed_handles_fragmented_and_coalesced_frames_and_negotiates_capabilities():
    emulator = U1Emulator()
    offered = int(Capability.CONFIG_READ | Capability.CAPTURE)
    hello = encode_cdc_frame(CdcFrame(CdcMessageType.HELLO, 77, struct.pack("<I", offered)))

    assert emulator.feed(hello[:3]) == b""
    reply = _decode_stream(emulator.feed(hello[3:]))[0]
    assert reply.type is CdcMessageType.DEVICE_INFO
    assert reply.sequence == 77
    assert reply.payload == struct.pack(
        "<BBBIIB32s",
        ErrorCode.OK,
        PROTOCOL_VERSION_MAJOR,
        PROTOCOL_VERSION_MINOR,
        offered,
        0,
        1,
        b"\0" * 32,
    )

    status = encode_cdc_frame(CdcFrame(CdcMessageType.GET_STATUS, 78, b""))
    ping = encode_cdc_frame(CdcFrame(CdcMessageType.PING, 79, b"echo"))
    replies = _decode_stream(emulator.feed(status + ping))
    assert [reply.type for reply in replies] == [CdcMessageType.GET_STATUS, CdcMessageType.PING]
    assert replies[0].payload == struct.pack("<BBBBI", ErrorCode.OK, 1, 0, 0, 0)
    assert replies[1].payload == bytes((ErrorCode.OK,)) + b"echo"


def test_minor_capability_intersection_rejects_unnegotiated_operation(config_a: bytes):
    emulator = U1Emulator()
    _hello(emulator, int(Capability.CONFIG_READ))

    reply = _request(
        emulator,
        CdcMessageType.WRITE_BEGIN,
        struct.pack("<I", len(config_a)) + hashlib.sha256(config_a).digest(),
    )
    assert _error(reply) is ErrorCode.UNSUPPORTED_CAPABILITY
    assert not emulator.staging_active


@pytest.mark.parametrize(
    ("message_type", "payload"),
    [
        (CdcMessageType.WRITE_BEGIN, b""),
        (CdcMessageType.READ_CONFIG_CHUNK, b""),
        (CdcMessageType.SET_ACTIVE_PROFILE, b""),
        (CdcMessageType.TEST_MACRO, b"\x01"),
    ],
)
def test_malformed_fixed_payload_precedes_session_and_capability_gates(message_type, payload):
    without_session = U1Emulator()
    baseline = (
        without_session.active_profile,
        without_session.capture_active,
        without_session.staging_active,
        without_session.release_all_count,
    )
    assert _error(_request(without_session, message_type, payload)) is ErrorCode.INVALID_REQUEST
    assert (
        without_session.active_profile,
        without_session.capture_active,
        without_session.staging_active,
        without_session.release_all_count,
    ) == baseline

    without_capability = U1Emulator()
    _hello(without_capability, 0)
    assert _error(_request(without_capability, message_type, payload)) is ErrorCode.INVALID_REQUEST
    assert not without_capability.staging_active


def test_major_mismatch_is_reported_and_blocks_state_changes(config_a: bytes):
    emulator = U1Emulator()
    request = CdcFrame(CdcMessageType.HELLO, 9, struct.pack("<I", 0xFFFFFFFF))

    reply = _decode_stream(emulator.feed(_wire_with_major(request, 99)))[0]
    assert reply.type is CdcMessageType.DEVICE_INFO
    assert _error(reply) is ErrorCode.INCOMPATIBLE_MAJOR

    begin = _request(
        emulator,
        CdcMessageType.WRITE_BEGIN,
        struct.pack("<I", len(config_a)) + hashlib.sha256(config_a).digest(),
        10,
    )
    assert _error(begin) is ErrorCode.INCOMPATIBLE_MAJOR
    assert not emulator.staging_active


def test_malformed_hello_does_not_replace_valid_negotiation(config_a: bytes):
    emulator = U1Emulator()
    _hello(emulator, sequence=200)

    malformed = _request(emulator, CdcMessageType.HELLO, b"\0", 201)
    assert _error(malformed) is ErrorCode.INVALID_REQUEST
    assert not emulator.staging_active

    begin = _request(
        emulator,
        CdcMessageType.WRITE_BEGIN,
        struct.pack("<I", len(config_a)) + hashlib.sha256(config_a).digest(),
        202,
    )
    assert _error(begin) is ErrorCode.OK
    assert emulator.staging_active


def test_major_mismatch_with_bad_crc_is_dropped_before_negotiation():
    emulator = U1Emulator()
    wire = bytearray(
        _wire_with_major(CdcFrame(CdcMessageType.HELLO, 9, struct.pack("<I", 0xFFFFFFFF)), 99)
    )
    wire[-2] = 2 if wire[-2] == 1 else wire[-2] ^ 1

    assert emulator.feed(bytes(wire)) == b""
    assert emulator.last_sequence is None


def test_non_v1_stateful_requests_are_rejected_before_dispatch(config_a: bytes):
    emulator = U1Emulator()
    _hello(emulator, sequence=50)

    write_begin = CdcFrame(
        CdcMessageType.WRITE_BEGIN,
        51,
        struct.pack("<I", len(config_a)) + hashlib.sha256(config_a).digest(),
    )
    write_reply = _decode_stream(emulator.feed(_wire_with_major(write_begin, 99)))[0]
    assert _error(write_reply) is ErrorCode.INCOMPATIBLE_MAJOR
    assert not emulator.staging_active

    stop_reply = _decode_stream(
        emulator.feed(_wire_with_major(CdcFrame(CdcMessageType.STOP_AND_RELEASE_ALL, 52, b""), 99))
    )[0]
    assert _error(stop_reply) is ErrorCode.INCOMPATIBLE_MAJOR
    assert emulator.release_all_count == 0


def test_exact_retry_is_byte_identical_and_bad_sequence_does_not_repeat_side_effects():
    emulator = U1Emulator()
    _hello(emulator, sequence=41)
    stop = CdcFrame(CdcMessageType.STOP_AND_RELEASE_ALL, 42, b"")
    encoded = encode_cdc_frame(stop)

    first = emulator.feed(encoded)
    retry = emulator.feed(encoded)
    assert retry == first
    assert emulator.release_all_count == 1

    stale = emulator.exchange(CdcFrame(CdcMessageType.PING, 42, b"different"))
    assert _error(stale) is ErrorCode.BAD_SEQUENCE
    assert stale.payload == bytes((ErrorCode.BAD_SEQUENCE,)) + b"different"
    assert emulator.release_all_count == 1
    assert _error(_request(emulator, CdcMessageType.PING, b"next", 43)) is ErrorCode.OK


def test_install_read_write_commit_and_readback_are_transactional(config_a: bytes, config_b: bytes):
    emulator = U1Emulator()
    emulator.install_active(config_a)
    hello = _hello(emulator)
    assert struct.unpack_from("<I", hello.payload, 7)[0] == 1
    assert hello.payload[-32:] == hashlib.sha256(config_a).digest()

    info = _request(emulator, CdcMessageType.GET_ACTIVE_CONFIG_INFO)
    assert info.payload == (
        bytes((ErrorCode.OK,))
        + struct.pack("<II", 1, len(config_a))
        + hashlib.sha256(config_a).digest()
    )
    chunk = _request(emulator, CdcMessageType.READ_CONFIG_CHUNK, struct.pack("<IH", 5, 17))
    assert chunk.payload == bytes((ErrorCode.OK,)) + struct.pack("<I", 5) + config_a[5:22]

    _write_all(emulator, config_b)
    assert emulator.active_hash == hashlib.sha256(config_a).digest()
    assert _error(_request(emulator, CdcMessageType.WRITE_COMMIT)) is ErrorCode.OK
    assert emulator.active_hash == hashlib.sha256(config_b).digest()
    assert emulator.active_generation == 2
    begin = _request(emulator, CdcMessageType.READ_CONFIG_BEGIN)
    assert begin.payload == bytes((ErrorCode.OK,)) + struct.pack("<II", 2, len(config_b)) + hashlib.sha256(config_b).digest()


def test_power_loss_before_commit_keeps_old_config(config_a: bytes, config_b: bytes):
    emulator = U1Emulator()
    emulator.install_active(config_a)
    _hello(emulator)
    _write_all(emulator, config_b)

    emulator.simulate_power_cycle()

    assert emulator.active_hash == hashlib.sha256(config_a).digest()
    assert emulator.active_generation == 1
    assert not emulator.staging_active


def test_bad_chunk_hash_config_and_abort_never_change_active_slot(config_a: bytes, config_b: bytes):
    emulator = U1Emulator()
    emulator.install_active(config_a)
    _hello(emulator)
    old_hash = emulator.active_hash

    digest = hashlib.sha256(config_b).digest()
    assert _error(_request(emulator, CdcMessageType.WRITE_BEGIN, struct.pack("<I", len(config_b)) + digest)) is ErrorCode.OK
    bad_chunk = _request(emulator, CdcMessageType.WRITE_CHUNK, struct.pack("<I", 1) + config_b[:20])
    assert _error(bad_chunk) is ErrorCode.BAD_CHUNK
    assert emulator.active_hash == old_hash
    assert _error(_request(emulator, CdcMessageType.WRITE_ABORT)) is ErrorCode.OK

    wrong_hash = b"\xff" * 32
    assert _error(_request(emulator, CdcMessageType.WRITE_BEGIN, struct.pack("<I", len(config_b)) + wrong_hash)) is ErrorCode.OK
    for offset in range(0, len(config_b), 512):
        assert _error(_request(emulator, CdcMessageType.WRITE_CHUNK, struct.pack("<I", offset) + config_b[offset : offset + 512])) is ErrorCode.OK
    assert _error(_request(emulator, CdcMessageType.WRITE_VERIFY)) is ErrorCode.BAD_HASH
    assert emulator.active_hash == old_hash

    invalid = b"not a config"
    assert _error(_request(emulator, CdcMessageType.WRITE_BEGIN, struct.pack("<I", len(invalid)) + hashlib.sha256(invalid).digest())) is ErrorCode.OK
    assert _error(_request(emulator, CdcMessageType.WRITE_CHUNK, struct.pack("<I", 0) + invalid)) is ErrorCode.OK
    assert _error(_request(emulator, CdcMessageType.WRITE_VERIFY)) is ErrorCode.INVALID_CONFIG
    assert emulator.active_hash == old_hash


def test_disconnect_and_timeout_faults_preserve_active_and_timeout_retry_is_idempotent(config_a: bytes, config_b: bytes):
    emulator = U1Emulator()
    emulator.install_active(config_a)
    emulator.open()
    _hello(emulator)
    digest = hashlib.sha256(config_b).digest()
    assert _error(_request(emulator, CdcMessageType.WRITE_BEGIN, struct.pack("<I", len(config_b)) + digest)) is ErrorCode.OK

    emulator.inject_disconnect()
    assert emulator.write(encode_cdc_frame(CdcFrame(CdcMessageType.PING, emulator.last_sequence + 1, b""))) == b""
    assert not emulator.is_open
    assert not emulator.staging_active
    assert emulator.active_hash == hashlib.sha256(config_a).digest()

    emulator.open()
    _hello(emulator, sequence=500)
    stop = CdcFrame(CdcMessageType.STOP_AND_RELEASE_ALL, 501, b"")
    emulator.inject_timeout()
    with pytest.raises(TimeoutError):
        emulator.exchange(stop)
    assert emulator.release_all_count == 1
    assert _error(emulator.exchange(stop)) is ErrorCode.OK
    assert emulator.release_all_count == 1


def test_capture_event_uses_next_sequence_queues_one_and_auto_ends():
    emulator = U1Emulator()
    _hello(emulator, int(Capability.CAPTURE), 0xFFFF)
    assert _error(_request(emulator, CdcMessageType.CAPTURE_BEGIN, sequence=0)) is ErrorCode.OK
    assert emulator.queue_capture_event(b"first")
    assert not emulator.queue_capture_event(b"second")

    event = _decode_stream(emulator.feed(b""))[0]
    assert event == CdcFrame(CdcMessageType.CAPTURE_EVENT, 1, b"first")
    assert not emulator.capture_active
    assert emulator.feed(b"") == b""


def test_stop_clears_capture_and_staging_and_increments_once(config_b: bytes):
    emulator = U1Emulator()
    _hello(emulator)
    assert _error(_request(emulator, CdcMessageType.CAPTURE_BEGIN)) is ErrorCode.OK
    assert _error(_request(emulator, CdcMessageType.WRITE_BEGIN, struct.pack("<I", len(config_b)) + hashlib.sha256(config_b).digest())) is ErrorCode.OK

    assert _error(_request(emulator, CdcMessageType.STOP_AND_RELEASE_ALL)) is ErrorCode.OK
    assert emulator.release_all_count == 1
    assert not emulator.capture_active
    assert not emulator.staging_active


def test_profile_and_macro_requests_validate_installed_references(config_a: bytes):
    base = decode_device_config(config_a)
    profile = replace(base.profiles[0], macros=(Macro(7, "test", TargetMode.PC1, ()),))
    package = compile_device_config(replace(base, profiles=(profile,) + base.profiles[1:]))
    emulator = U1Emulator()
    emulator.install_active(package)
    _hello(emulator)

    assert _error(_request(emulator, CdcMessageType.SET_ACTIVE_PROFILE, b"\x08")) is ErrorCode.OK
    assert emulator.active_profile == 8
    assert _error(_request(emulator, CdcMessageType.SET_ACTIVE_PROFILE, b"\x09")) is ErrorCode.INVALID_REQUEST
    assert _error(_request(emulator, CdcMessageType.TEST_MACRO, b"\x01\x07")) is ErrorCode.OK
    assert _error(_request(emulator, CdcMessageType.TEST_MACRO, b"\x01\x08")) is ErrorCode.INVALID_REQUEST


def test_factory_reset_requires_confirmation_and_clears_slots_safely(config_a: bytes):
    emulator = U1Emulator()
    emulator.install_active(config_a)
    _hello(emulator)

    assert _error(_request(emulator, CdcMessageType.FACTORY_RESET_ARM)) is ErrorCode.PHYSICAL_CONFIRMATION_REQUIRED
    emulator.physical_confirmation = True
    assert _error(_request(emulator, CdcMessageType.FACTORY_RESET_ARM)) is ErrorCode.OK
    emulator.physical_confirmation = False
    assert _error(_request(emulator, CdcMessageType.FACTORY_RESET_COMMIT)) is ErrorCode.PHYSICAL_CONFIRMATION_REQUIRED
    assert emulator.active_hash == hashlib.sha256(config_a).digest()

    emulator.physical_confirmation = True
    assert _error(_request(emulator, CdcMessageType.FACTORY_RESET_ARM)) is ErrorCode.OK
    assert _error(_request(emulator, CdcMessageType.FACTORY_RESET_COMMIT)) is ErrorCode.OK
    assert emulator.active_hash == b"\0" * 32
    assert emulator.active_generation == 0
    assert emulator.active_profile == 1


def test_diagnostics_count_crc_disconnect_timeout_bad_sequence_and_aborts(config_b: bytes):
    emulator = U1Emulator()
    emulator.open()
    _hello(emulator, sequence=10)
    bad_crc = bytearray(encode_cdc_frame(CdcFrame(CdcMessageType.PING, 11, b"crc")))
    bad_crc[-2] = (bad_crc[-2] + 1) or 1
    assert emulator.feed(bytes(bad_crc)) == b""
    assert _error(_request(emulator, CdcMessageType.PING, b"stale", 10)) is ErrorCode.BAD_SEQUENCE

    assert _error(_request(emulator, CdcMessageType.WRITE_BEGIN, struct.pack("<I", len(config_b)) + hashlib.sha256(config_b).digest(), 11)) is ErrorCode.OK
    assert _error(_request(emulator, CdcMessageType.WRITE_ABORT, sequence=12)) is ErrorCode.OK
    emulator.inject_timeout()
    timeout_request = CdcFrame(CdcMessageType.PING, 13, b"timeout")
    with pytest.raises(TimeoutError):
        emulator.exchange(timeout_request)
    assert _error(emulator.exchange(timeout_request)) is ErrorCode.OK
    emulator.inject_disconnect()
    assert emulator.write(encode_cdc_frame(CdcFrame(CdcMessageType.PING, 14, b"disconnect"))) == b""
    emulator.open()
    _hello(emulator, sequence=20)

    diagnostics = _request(emulator, CdcMessageType.GET_DIAGNOSTICS, sequence=21)
    assert diagnostics.payload == bytes((ErrorCode.OK,)) + struct.pack("<IIIII", 1, 1, 1, 1, 1)


def test_malformed_payloads_and_output_only_types_do_not_mutate_state(config_b: bytes):
    emulator = U1Emulator()
    _hello(emulator)
    baseline = (emulator.active_profile, emulator.capture_active, emulator.staging_active, emulator.release_all_count)

    cases = [
        (CdcMessageType.GET_STATUS, b"x"),
        (CdcMessageType.WRITE_BEGIN, b"short"),
        (CdcMessageType.WRITE_CHUNK, b"\0\0\0\0"),
        (CdcMessageType.WRITE_VERIFY, b"x"),
        (CdcMessageType.SET_ACTIVE_PROFILE, b""),
        (CdcMessageType.CAPTURE_BEGIN, b"x"),
        (CdcMessageType.TEST_MACRO, b"\x01"),
        (CdcMessageType.DEVICE_INFO, b""),
        (CdcMessageType.CAPTURE_EVENT, b"event"),
    ]
    for message_type, payload in cases:
        assert _error(_request(emulator, message_type, payload)) is ErrorCode.INVALID_REQUEST
        assert (emulator.active_profile, emulator.capture_active, emulator.staging_active, emulator.release_all_count) == baseline

    malformed_read = _request(emulator, CdcMessageType.READ_CONFIG_CHUNK, b"x")
    assert malformed_read.payload == bytes((ErrorCode.INVALID_REQUEST,)) + b"\0" * 4


def test_bad_crc_response_injection_corrupts_one_response_once():
    emulator = U1Emulator()
    emulator.inject_bad_crc_response()
    wire = emulator.feed(encode_cdc_frame(CdcFrame(CdcMessageType.HELLO, 3, struct.pack("<I", 0))))

    with pytest.raises(FrameError, match="CRC"):
        decode_cdc_frame(wire)
    retry = emulator.feed(encode_cdc_frame(CdcFrame(CdcMessageType.HELLO, 3, struct.pack("<I", 0))))
    assert _error(decode_cdc_frame(retry)) is ErrorCode.OK


def test_bad_crc_response_injection_preserves_cobs_for_long_zero_ping():
    emulator = U1Emulator()
    emulator.inject_bad_crc_response()
    request = encode_cdc_frame(CdcFrame(CdcMessageType.PING, 1, bytes(639)))

    wire = emulator.feed(request)

    cobs_decode(wire[:-1])
    with pytest.raises(FrameError, match="CRC"):
        decode_cdc_frame(wire)


def test_ping_rejects_payload_that_cannot_fit_error_prefixed_reply():
    emulator = U1Emulator()
    reply = _request(emulator, CdcMessageType.PING, b"x" * 1024)

    assert reply.payload == bytes((ErrorCode.BAD_SIZE,))
    stale = emulator.exchange(CdcFrame(CdcMessageType.PING, 100, b"y" * 1024))
    assert stale.payload == bytes((ErrorCode.BAD_SEQUENCE,))
