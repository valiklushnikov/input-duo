"""Deterministic, dependency-free U1 CDC protocol emulator."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from duo_input.domain.config_binary import ConfigError, decode_device_config
from duo_input.generated.protocol import (
    BINARY_CONFIG_MAX_BYTES,
    CDC_MAX_PAYLOAD,
    CONFIG_CHUNK_MAX_BYTES,
    PROTOCOL_VERSION_MAJOR,
    PROTOCOL_VERSION_MINOR,
    Capability,
    CdcMessageType,
    ErrorCode,
)
from duo_input.protocol.cobs import cobs_decode, cobs_encode
from duo_input.protocol.crc import crc32_ieee
from duo_input.protocol.frame import CdcFrame, FrameError, decode_cdc_frame, encode_cdc_frame

from .transport import AbstractByteTransport


DEVICE_CAPABILITIES = sum(int(capability) for capability in Capability)
_ZERO_HASH = b"\0" * 32
_FIXED_REQUEST_SIZES = {
    CdcMessageType.HELLO: 4,
    CdcMessageType.GET_STATUS: 0,
    CdcMessageType.GET_ACTIVE_CONFIG_INFO: 0,
    CdcMessageType.READ_CONFIG_BEGIN: 0,
    CdcMessageType.READ_CONFIG_CHUNK: 6,
    CdcMessageType.WRITE_BEGIN: 36,
    CdcMessageType.WRITE_VERIFY: 0,
    CdcMessageType.WRITE_COMMIT: 0,
    CdcMessageType.WRITE_ABORT: 0,
    CdcMessageType.SET_ACTIVE_PROFILE: 1,
    CdcMessageType.CAPTURE_BEGIN: 0,
    CdcMessageType.CAPTURE_END: 0,
    CdcMessageType.TEST_MACRO: 2,
    CdcMessageType.STOP_AND_RELEASE_ALL: 0,
    CdcMessageType.GET_DIAGNOSTICS: 0,
    CdcMessageType.FACTORY_RESET_ARM: 0,
    CdcMessageType.FACTORY_RESET_COMMIT: 0,
}
_REQUIRED_CAPABILITY = {
    CdcMessageType.GET_ACTIVE_CONFIG_INFO: Capability.CONFIG_READ,
    CdcMessageType.READ_CONFIG_BEGIN: Capability.CONFIG_READ,
    CdcMessageType.READ_CONFIG_CHUNK: Capability.CONFIG_READ,
    CdcMessageType.WRITE_BEGIN: Capability.CONFIG_WRITE,
    CdcMessageType.WRITE_CHUNK: Capability.CONFIG_WRITE,
    CdcMessageType.WRITE_VERIFY: Capability.CONFIG_WRITE,
    CdcMessageType.WRITE_COMMIT: Capability.CONFIG_WRITE,
    CdcMessageType.WRITE_ABORT: Capability.CONFIG_WRITE,
    CdcMessageType.SET_ACTIVE_PROFILE: Capability.ROUTE_CONTROL,
    CdcMessageType.CAPTURE_BEGIN: Capability.CAPTURE,
    CdcMessageType.CAPTURE_END: Capability.CAPTURE,
    CdcMessageType.TEST_MACRO: Capability.TEST_MACRO,
    CdcMessageType.GET_DIAGNOSTICS: Capability.DIAGNOSTICS,
    CdcMessageType.FACTORY_RESET_ARM: Capability.FACTORY_RESET,
    CdcMessageType.FACTORY_RESET_COMMIT: Capability.FACTORY_RESET,
}


@dataclass(frozen=True)
class _Slot:
    package: bytes = b""
    digest: bytes = _ZERO_HASH
    valid: bool = False
    generation: int = 0


@dataclass
class _Staging:
    expected_size: int
    expected_hash: bytes
    data: bytearray
    next_offset: int = 0
    verified: bool = False


@dataclass
class _Diagnostics:
    bad_crc: int = 0
    disconnect: int = 0
    timeout: int = 0
    bad_sequence: int = 0
    aborted_staging: int = 0


#: Every host-block shape write_host_observation() has shipped, keyed by how
#: many values it carries. The block's leading length byte is what makes an
#: older shape a valid payload rather than a broken one, so the emulator has to
#: be able to speak all of them: 7 values is the original 20-byte block, 16 is
#: the 38-byte one, 22 is the prior 62-byte one, and 24 is the current
#: 70-byte one.
_HOST_OBSERVATION_LAYOUTS = {
    7: "<BIIIBHI",
    16: "<BIIIBHIHHHBBIHHH",
    22: "<BIIIBHIHHHBBIHHHIIIIII",
    24: "<BIIIBHIHHHBBIHHHIIIIIIII",
}


class U1Emulator(AbstractByteTransport):
    """In-memory U1 endpoint using the production CDC codec and config decoder."""

    def __init__(self) -> None:
        super().__init__()
        self._slots = {"A": _Slot(), "B": _Slot()}
        self._active_slot: str | None = None
        self._staging: _Staging | None = None
        self._receive = bytearray()
        self._last_sequence: int | None = None
        self._last_request_wire: bytes | None = None
        self._last_response_wire: bytes | None = None
        self._negotiated_capabilities: int | None = None
        self._negotiation_error = ErrorCode.BAD_STATE
        self._capture_active = False
        self._capture_event: bytes | None = None
        self._active_profile = 1
        self._release_all_count = 0
        self._factory_reset_armed = False
        self.physical_confirmation = False
        self._diagnostics = _Diagnostics()
        # What the emulated U1 says about its link to U2. A real device reports
        # these, so the emulator has to as well, or the configurator cannot be
        # exercised against the state that matters most to an operator.
        self.endpoint_answering = True
        self.endpoint_mounted = True
        self.link_frames_sent = 0
        self.link_crc_errors = 0
        self.link_echoed_frames = 0
        self.endpoint_drops = 0
        self.endpoint_release_ms = 0
        # Commands the real U1's Core 1 could not hand to Core 0. Nonzero means
        # what a computer is holding no longer matches what the operator did.
        self.dropped_commands = 0
        # What the real U1's output runtime is doing about its queue right now:
        # 0 no fault, 1 a queue that is refusing commands. It clears itself
        # once a pass goes by with nothing refused, which is why it is separate
        # from the cumulative count above.
        self.runtime_fault = 0
        # Which backend read U1's own USB ports, by the protocol's own number
        # (1 CH375, 2 PIO_USB), and its counters. ``None`` is the default and
        # it is not a gap: it makes this emulator answer exactly the way
        # firmware predating the appended blocks answers, which is the payload
        # the configurator's backward-compatibility path has to keep reading.
        # Setting it makes the reply carry the latency, peripheral and backend
        # blocks a current U1 sends.
        self.input_backend: int | None = None
        #: The twelve u32 counters, in the order config_service.cpp writes them.
        self.backend_counters: tuple[int, ...] = (0,) * 12
        #: Two ports, each (attached, ready, kind, vid, pid, buttons,
        #: descriptor bytes, 32-byte hash) - the shape write_peripheral() puts
        #: on the wire.
        self.peripheral_ports: tuple[tuple, ...] = (
            (0, 0, 0, 0, 0, 0, 0, bytes(32)),
            (0, 0, 0, 0, 0, 0, 0, bytes(32)),
        )
        #: What the host stack and its root port are doing, in the shape
        #: write_host_observation() puts on the wire. Any of the lengths in
        #: _HOST_OBSERVATION_LAYOUTS is accepted, so a test can build the
        #: payload an older firmware sent as well as the current one: (init flags, clk_sys when
        #: Core 1 began, clk_sys now, SOF frames, packed root-port state,
        #: attach count, input-core passes, device mount/unmount/HID mount
        #: counts, endpoint-slot/failure high-waters, maximum pass/SOF gaps,
        #: root-port resets and configured-hub mount edges, then the endpoint
        #: slot map, the packed host event counts, the enumeration progress
        #: mask, the blocked-pass count and total, and the deepest input-core
        #: stack pointer, live endpoint-transfer flags and the completion
        #: total captured at the latest attach). ``None`` means this image has no
        #: host stack to observe and sends a length of zero - which is what the
        #: real CH375 image does, and a different fact from an older firmware
        #: that sends no host block at all.
        self.host_observation: tuple[int, ...] | None = None
        self._timeout_once = False
        self._disconnect_once = False
        self._bad_crc_response_once = False

    @property
    def last_sequence(self) -> int | None:
        return self._last_sequence

    @property
    def active_generation(self) -> int:
        slot = self._active()
        return slot.generation if slot else 0

    @property
    def active_hash(self) -> bytes:
        slot = self._active()
        return slot.digest if slot else _ZERO_HASH

    @property
    def active_profile(self) -> int:
        return self._active_profile

    @property
    def capture_active(self) -> bool:
        return self._capture_active

    @property
    def staging_active(self) -> bool:
        return self._staging is not None

    @property
    def release_all_count(self) -> int:
        return self._release_all_count

    def write(self, data: bytes) -> bytes:
        if not self.is_open:
            raise RuntimeError("transport is closed")
        return self.feed(data)

    def feed(self, data: bytes) -> bytes:
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        if self._disconnect_once:
            self._disconnect_once = False
            self._diagnostics.disconnect += 1
            self._is_open = False
            self._receive.clear()
            self._abort_staging()
            self._reset_session()
            return b""

        self._receive.extend(data)
        responses = bytearray()
        while True:
            try:
                end = self._receive.index(0)
            except ValueError:
                break
            request_wire = bytes(self._receive[: end + 1])
            del self._receive[: end + 1]
            try:
                frame, major = self._decode_request(request_wire)
            except FrameError as error:
                if "CRC" in str(error):
                    self._diagnostics.bad_crc += 1
                continue
            response = self._process_request(request_wire, frame, major)
            if response is None:
                continue
            if self._timeout_once:
                self._timeout_once = False
                self._diagnostics.timeout += 1
                continue
            if self._bad_crc_response_once:
                self._bad_crc_response_once = False
                response = self._corrupt_response_crc(response)
            responses.extend(response)

        if data == b"" and self._capture_active and self._capture_event is not None:
            sequence = 0 if self._last_sequence is None else (self._last_sequence + 1) & 0xFFFF
            responses.extend(
                encode_cdc_frame(CdcFrame(CdcMessageType.CAPTURE_EVENT, sequence, self._capture_event))
            )
            self._last_sequence = sequence
            self._last_request_wire = None
            self._last_response_wire = None
            self._capture_event = None
            self._capture_active = False
        return bytes(responses)

    def exchange(self, frame: CdcFrame) -> CdcFrame:
        response = self.feed(encode_cdc_frame(frame))
        if not response:
            raise TimeoutError("emulated request produced no response")
        delimiter = response.find(b"\0")
        return decode_cdc_frame(response[: delimiter + 1])

    def install_active(self, package: bytes) -> None:
        package = bytes(package)
        decoded = decode_device_config(package)
        generation = max(slot.generation for slot in self._slots.values()) + 1
        target = "A" if self._active_slot != "A" else "B"
        self._slots[target] = _Slot(package, hashlib.sha256(package).digest(), True, generation)
        self._active_slot = target
        self._active_profile = decoded.active_profile_id

    def simulate_power_cycle(self) -> None:
        self._receive.clear()
        self._abort_staging()
        self._capture_active = False
        self._capture_event = None
        self._factory_reset_armed = False
        self._reset_session()
        valid = [(name, slot) for name, slot in self._slots.items() if slot.valid]
        self._active_slot = max(valid, key=lambda item: item[1].generation)[0] if valid else None
        active = self._active()
        self._active_profile = decode_device_config(active.package).active_profile_id if active else 1

    def queue_capture_event(self, payload: bytes) -> bool:
        if not isinstance(payload, bytes):
            raise TypeError("payload must be bytes")
        if len(payload) > CDC_MAX_PAYLOAD:
            raise ValueError("capture event exceeds CDC payload limit")
        if not self._capture_active or self._capture_event is not None:
            return False
        self._capture_event = payload
        return True

    def inject_timeout(self) -> None:
        self._timeout_once = True

    def inject_disconnect(self) -> None:
        self._disconnect_once = True

    def inject_bad_crc_response(self) -> None:
        self._bad_crc_response_once = True

    def _active(self) -> _Slot | None:
        return self._slots[self._active_slot] if self._active_slot is not None else None

    def _reset_session(self) -> None:
        self._last_sequence = None
        self._last_request_wire = None
        self._last_response_wire = None
        self._negotiated_capabilities = None
        self._negotiation_error = ErrorCode.BAD_STATE

    def _abort_staging(self) -> None:
        if self._staging is not None:
            self._diagnostics.aborted_staging += 1
            self._staging = None

    @staticmethod
    def _decode_request(request_wire: bytes) -> tuple[CdcFrame, int]:
        try:
            return decode_cdc_frame(request_wire), PROTOCOL_VERSION_MAJOR
        except FrameError as error:
            if str(error) != "incompatible CDC major":
                raise
        try:
            raw = bytearray(cobs_decode(request_wire[:-1]))
        except ValueError as error:
            raise FrameError("malformed COBS frame") from error
        major = raw[2]
        if int.from_bytes(raw[-4:], "little") != crc32_ieee(raw[:-4]):
            raise FrameError("invalid CDC CRC")
        raw[2] = PROTOCOL_VERSION_MAJOR
        raw[-4:] = crc32_ieee(raw[:-4]).to_bytes(4, "little")
        normalized = cobs_encode(raw) + b"\0"
        return decode_cdc_frame(normalized), major

    def _process_request(self, request_wire: bytes, frame: CdcFrame, major: int) -> bytes | None:
        if request_wire == self._last_request_wire:
            return self._last_response_wire
        if self._last_sequence is not None and frame.sequence != (self._last_sequence + 1) & 0xFFFF:
            self._diagnostics.bad_sequence += 1
            response_type = CdcMessageType.DEVICE_INFO if frame.type is CdcMessageType.HELLO else frame.type
            return encode_cdc_frame(
                CdcFrame(response_type, frame.sequence, self._error_payload(frame, ErrorCode.BAD_SEQUENCE))
            )

        response_type, payload = self._dispatch(frame, major)
        response = encode_cdc_frame(CdcFrame(response_type, frame.sequence, payload))
        self._last_sequence = frame.sequence
        self._last_request_wire = request_wire
        self._last_response_wire = response
        return response

    def _dispatch(self, frame: CdcFrame, major: int) -> tuple[CdcMessageType, bytes]:
        if frame.type is CdcMessageType.HELLO:
            # A handshake starts a new owner of the session, and a question the
            # previous owner left running would go on swallowing this one's
            # keys. The firmware ends it here; so does this.
            self._capture_active = False
            self._capture_event = None
            # And a write the previous owner abandoned. Closing a serial port
            # does not unmount USB, so a configurator that quit mid-write
            # leaves the transaction open; a new session cannot continue
            # someone else's write, and leaving it standing means answering
            # BUSY to every later WRITE_BEGIN until the device is unplugged.
            # The firmware has always done this, and it is the recovery path a
            # crashed configurator takes - so an emulator that answered BUSY
            # here could never have caught a regression in it.
            self._abort_staging()
            if not self._payload_shape_is_valid(frame):
                return CdcMessageType.DEVICE_INFO, self._device_info_payload(
                    ErrorCode.INVALID_REQUEST, 0
                )
            return CdcMessageType.DEVICE_INFO, self._hello(frame.payload, major)
        if major != PROTOCOL_VERSION_MAJOR:
            return frame.type, self._error_payload(frame, ErrorCode.INCOMPATIBLE_MAJOR)
        if frame.type in (CdcMessageType.DEVICE_INFO, CdcMessageType.CAPTURE_EVENT):
            return frame.type, bytes((ErrorCode.INVALID_REQUEST,))
        if not self._payload_shape_is_valid(frame):
            return frame.type, self._error_payload(frame, ErrorCode.INVALID_REQUEST)

        if frame.type not in (CdcMessageType.PING, CdcMessageType.STOP_AND_RELEASE_ALL):
            if self._negotiation_error is not ErrorCode.OK:
                return frame.type, self._error_payload(frame, self._negotiation_error)
            required = _REQUIRED_CAPABILITY.get(frame.type)
            if required is not None and not self._negotiated_capabilities & int(required):
                return frame.type, self._error_payload(frame, ErrorCode.UNSUPPORTED_CAPABILITY)

        handler = getattr(self, f"_handle_{frame.type.name.lower()}")
        return frame.type, handler(frame.payload)

    @staticmethod
    def _payload_shape_is_valid(frame: CdcFrame) -> bool:
        if frame.type is CdcMessageType.WRITE_CHUNK:
            return 5 <= len(frame.payload) <= 4 + CONFIG_CHUNK_MAX_BYTES
        expected = _FIXED_REQUEST_SIZES.get(frame.type)
        return expected is None or len(frame.payload) == expected

    def _error_payload(self, frame: CdcFrame, error: ErrorCode) -> bytes:
        if frame.type is CdcMessageType.HELLO:
            return self._device_info_payload(error, 0)
        if frame.type is CdcMessageType.GET_STATUS:
            return bytes((error,)) + self._handle_get_status(b"")[1:]
        if frame.type in (CdcMessageType.GET_ACTIVE_CONFIG_INFO, CdcMessageType.READ_CONFIG_BEGIN):
            return bytes((error,)) + self._config_info()[1:]
        if frame.type is CdcMessageType.WRITE_CHUNK:
            return bytes((error,)) + struct.pack("<I", self._staging.next_offset if self._staging else 0)
        if frame.type is CdcMessageType.READ_CONFIG_CHUNK:
            return bytes((error,)) + (frame.payload[:4] if len(frame.payload) >= 4 else b"\0" * 4)
        if frame.type is CdcMessageType.GET_DIAGNOSTICS:
            return bytes((error,)) + self._handle_get_diagnostics(b"")[1:]
        if frame.type is CdcMessageType.PING:
            return bytes((error,)) + (frame.payload if len(frame.payload) < CDC_MAX_PAYLOAD else b"")
        return bytes((error,))

    def _hello(self, payload: bytes, major: int) -> bytes:
        if major != PROTOCOL_VERSION_MAJOR:
            self._negotiated_capabilities = None
            self._negotiation_error = ErrorCode.INCOMPATIBLE_MAJOR
            return self._device_info_payload(ErrorCode.INCOMPATIBLE_MAJOR, 0)
        requested = struct.unpack("<I", payload)[0]
        self._negotiated_capabilities = requested & DEVICE_CAPABILITIES
        self._negotiation_error = ErrorCode.OK
        return self._device_info_payload(ErrorCode.OK, self._negotiated_capabilities)

    def _device_info_payload(self, error: ErrorCode, capabilities: int) -> bytes:
        return struct.pack(
            "<BBBIIB32s",
            error,
            PROTOCOL_VERSION_MAJOR,
            PROTOCOL_VERSION_MINOR,
            capabilities,
            self.active_generation,
            self._active_profile,
            self.active_hash,
        )

    def _handle_get_status(self, payload: bytes) -> bytes:
        return struct.pack(
            "<BBBBI",
            ErrorCode.OK,
            self._active_profile,
            self._capture_active,
            self._staging is not None,
            self._release_all_count,
        )

    def _config_info(self) -> bytes:
        active = self._active()
        return (
            bytes((ErrorCode.OK,))
            + struct.pack("<II", self.active_generation, len(active.package) if active else 0)
            + self.active_hash
        )

    def _handle_get_active_config_info(self, payload: bytes) -> bytes:
        return self._config_info()

    def _handle_read_config_begin(self, payload: bytes) -> bytes:
        return self._config_info()

    def _handle_read_config_chunk(self, payload: bytes) -> bytes:
        if len(payload) != 6:
            return bytes((ErrorCode.INVALID_REQUEST,)) + (payload[:4] if len(payload) >= 4 else b"\0" * 4)
        offset, requested = struct.unpack("<IH", payload)
        prefix = struct.pack("<I", offset)
        if requested > CONFIG_CHUNK_MAX_BYTES:
            return bytes((ErrorCode.BAD_SIZE,)) + prefix
        active = self._active()
        if active is None:
            return bytes((ErrorCode.BAD_STATE,)) + prefix
        if offset > len(active.package):
            return bytes((ErrorCode.BAD_CHUNK,)) + prefix
        return bytes((ErrorCode.OK,)) + prefix + active.package[offset : offset + requested]

    def _handle_write_begin(self, payload: bytes) -> bytes:
        if len(payload) != 36:
            return bytes((ErrorCode.INVALID_REQUEST,))
        if self._staging is not None:
            return bytes((ErrorCode.BUSY,))
        size = struct.unpack_from("<I", payload)[0]
        if size == 0 or size > BINARY_CONFIG_MAX_BYTES:
            return bytes((ErrorCode.BAD_SIZE,))
        self._staging = _Staging(size, payload[4:], bytearray())
        return bytes((ErrorCode.OK,))

    def _handle_write_chunk(self, payload: bytes) -> bytes:
        if len(payload) < 5 or len(payload) > 4 + CONFIG_CHUNK_MAX_BYTES:
            return self._write_chunk_reply(ErrorCode.INVALID_REQUEST)
        if self._staging is None:
            return self._write_chunk_reply(ErrorCode.BAD_STATE)
        offset = struct.unpack_from("<I", payload)[0]
        chunk = payload[4:]
        if offset != self._staging.next_offset or len(chunk) > self._staging.expected_size - offset:
            return self._write_chunk_reply(ErrorCode.BAD_CHUNK)
        self._staging.data.extend(chunk)
        self._staging.next_offset += len(chunk)
        self._staging.verified = False
        return self._write_chunk_reply(ErrorCode.OK)

    def _write_chunk_reply(self, error: ErrorCode) -> bytes:
        return bytes((error,)) + struct.pack("<I", self._staging.next_offset if self._staging else 0)

    def _handle_write_verify(self, payload: bytes) -> bytes:
        if self._staging is None:
            return bytes((ErrorCode.BAD_STATE,))
        if self._staging.next_offset != self._staging.expected_size:
            return bytes((ErrorCode.BAD_SIZE,))
        package = bytes(self._staging.data)
        if hashlib.sha256(package).digest() != self._staging.expected_hash:
            self._abort_staging()
            return bytes((ErrorCode.BAD_HASH,))
        try:
            decode_device_config(package)
        except ConfigError:
            self._abort_staging()
            return bytes((ErrorCode.INVALID_CONFIG,))
        self._staging.verified = True
        return bytes((ErrorCode.OK,))

    def _handle_write_commit(self, payload: bytes) -> bytes:
        if self._staging is None or not self._staging.verified:
            return bytes((ErrorCode.BAD_STATE,))
        package = bytes(self._staging.data)
        if len(package) != self._staging.expected_size:
            return bytes((ErrorCode.BAD_SIZE,))
        if hashlib.sha256(package).digest() != self._staging.expected_hash:
            self._abort_staging()
            return bytes((ErrorCode.BAD_HASH,))
        try:
            decoded = decode_device_config(package)
        except ConfigError:
            self._abort_staging()
            return bytes((ErrorCode.INVALID_CONFIG,))
        generation = max(slot.generation for slot in self._slots.values()) + 1
        target = "A" if self._active_slot != "A" else "B"
        completed_slot = _Slot(package, self._staging.expected_hash, True, generation)
        self._slots[target] = completed_slot
        self._active_slot = target
        self._active_profile = decoded.active_profile_id
        self._staging = None
        return bytes((ErrorCode.OK,))

    def _handle_write_abort(self, payload: bytes) -> bytes:
        if self._staging is None:
            return bytes((ErrorCode.BAD_STATE,))
        self._abort_staging()
        return bytes((ErrorCode.OK,))

    def _handle_set_active_profile(self, payload: bytes) -> bytes:
        if len(payload) != 1:
            return bytes((ErrorCode.INVALID_REQUEST,))
        active = self._active()
        if active is None:
            return bytes((ErrorCode.BAD_STATE,))
        config = decode_device_config(active.package)
        if payload[0] not in {profile.id for profile in config.profiles}:
            return bytes((ErrorCode.INVALID_REQUEST,))
        self._active_profile = payload[0]
        return bytes((ErrorCode.OK,))

    def _handle_capture_begin(self, payload: bytes) -> bytes:
        if self._capture_active:
            return bytes((ErrorCode.BUSY,))
        self._capture_active = True
        self._capture_event = None
        return bytes((ErrorCode.OK,))

    def _handle_capture_end(self, payload: bytes) -> bytes:
        if not self._capture_active:
            return bytes((ErrorCode.BAD_STATE,))
        self._capture_active = False
        self._capture_event = None
        return bytes((ErrorCode.OK,))

    def _handle_test_macro(self, payload: bytes) -> bytes:
        if len(payload) != 2:
            return bytes((ErrorCode.INVALID_REQUEST,))
        active = self._active()
        if active is None:
            return bytes((ErrorCode.BAD_STATE,))
        config = decode_device_config(active.package)
        profile = next((profile for profile in config.profiles if profile.id == payload[0]), None)
        if profile is None or payload[1] not in {macro.id for macro in profile.macros}:
            return bytes((ErrorCode.INVALID_REQUEST,))
        return bytes((ErrorCode.OK,))

    def _handle_stop_and_release_all(self, payload: bytes) -> bytes:
        self._release_all_count += 1
        self._capture_active = False
        self._capture_event = None
        self._abort_staging()
        return bytes((ErrorCode.OK,))

    def _handle_get_diagnostics(self, payload: bytes) -> bytes:
        return (
            bytes((ErrorCode.OK,))
            + struct.pack(
                "<IIIII",
                self._diagnostics.bad_crc,
                self._diagnostics.disconnect,
                self._diagnostics.timeout,
                self._diagnostics.bad_sequence,
                self._diagnostics.aborted_staging,
            )
            + struct.pack(
                "<BBIII",
                1 if self.endpoint_answering else 0,
                1 if self.endpoint_mounted else 0,
                self.link_frames_sent,
                self.link_crc_errors,
                self.link_echoed_frames,
            )
            + struct.pack("<BH", self.endpoint_drops, self.endpoint_release_ms)
            + struct.pack("<I", self.dropped_commands)
            + struct.pack("<B", self.runtime_fault)
            + self._appended_diagnostics()
        )

    def _appended_diagnostics(self) -> bytes:
        """The latency, peripheral, backend and host blocks a current U1 appends.

        Empty until ``input_backend`` is set, so by default this emulator
        answers byte for byte the way firmware predating these blocks answers.
        The blocks are positional, so none of them can be sent without the ones
        in front of it.
        """
        if self.input_backend is None:
            return b""
        buckets = 9
        edges = (250, 500, 1000, 2000, 5000, 10000, 20000, 50000)
        latency = bytes((buckets,)) + b"".join(struct.pack("<I", edge) for edge in edges)
        latency += 2 * (struct.pack("<II", 0, 0) + bytes(4 * buckets))
        ports = b"".join(
            struct.pack("<BBBHHBH32s", *port) for port in self.peripheral_ports
        )
        backend = bytes((self.input_backend, len(self.backend_counters)))
        backend += b"".join(struct.pack("<I", value) for value in self.backend_counters)
        # The host block's leading length carries the same meaning the backend
        # block's counter count does: zero says this image has the field and
        # nothing to put in it, rather than saying it measured zero.
        if self.host_observation is None:
            host = bytes((0,))
        else:
            # Keyed by how many values were handed over, so every append-only
            # shape this firmware line has shipped stays speakable here: the
            # emulator is the reference payload both the parser and the
            # firmware are written against, and one frozen at the previous
            # shape would let every test above pass while nothing carried the
            # newest fields.
            layout = _HOST_OBSERVATION_LAYOUTS[len(self.host_observation)]
            fields = struct.pack(layout, *self.host_observation)
            host = bytes((len(fields),)) + fields
        return latency + ports + backend + host

    def _handle_factory_reset_arm(self, payload: bytes) -> bytes:
        if not self.physical_confirmation:
            self._factory_reset_armed = False
            return bytes((ErrorCode.PHYSICAL_CONFIRMATION_REQUIRED,))
        self._factory_reset_armed = True
        return bytes((ErrorCode.OK,))

    def _handle_factory_reset_commit(self, payload: bytes) -> bytes:
        # Confirmation first, then the arm - the order the firmware checks them
        # in, and the order FACTORY_RESET_ARM already answers in. Somebody
        # being at the device is the precondition; whether a previous request
        # armed the reset is a detail of this session. Checked the other way
        # round, a host that had done neither was told BAD_STATE here and
        # PHYSICAL_CONFIRMATION_REQUIRED by hardware for the same request.
        if not self.physical_confirmation:
            self._factory_reset_armed = False
            return bytes((ErrorCode.PHYSICAL_CONFIRMATION_REQUIRED,))
        if not self._factory_reset_armed:
            return bytes((ErrorCode.BAD_STATE,))
        self._slots = {"A": _Slot(), "B": _Slot()}
        self._active_slot = None
        self._staging = None
        self._active_profile = 1
        self._capture_active = False
        self._capture_event = None
        self._factory_reset_armed = False
        return bytes((ErrorCode.OK,))

    def _handle_ping(self, payload: bytes) -> bytes:
        if len(payload) == CDC_MAX_PAYLOAD:
            return bytes((ErrorCode.BAD_SIZE,))
        return bytes((ErrorCode.OK,)) + payload

    @staticmethod
    def _corrupt_response_crc(response: bytes) -> bytes:
        raw = bytearray(cobs_decode(response[:-1]))
        raw[-1] ^= 1
        return cobs_encode(raw) + b"\0"


__all__ = ["DEVICE_CAPABILITIES", "ErrorCode", "U1Emulator"]
