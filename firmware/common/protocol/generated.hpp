// generated; do not edit
#pragma once

#include <cstddef>
#include <cstdint>

namespace duo_input::protocol {

inline constexpr std::uint8_t SCHEMA_VERSION_MAJOR = 1;
inline constexpr std::uint8_t SCHEMA_VERSION_MINOR = 0;
inline constexpr std::uint8_t PROTOCOL_VERSION_MAJOR = 1;
inline constexpr std::uint8_t PROTOCOL_VERSION_MINOR = 0;

struct ProtocolLimits {
    static constexpr std::size_t BINARY_CONFIG_MAX_BYTES = 368640;
    static constexpr std::size_t BINDINGS_PER_PROFILE = 128;
    static constexpr std::size_t CDC_MAX_PAYLOAD = 1024;
    static constexpr std::size_t CONFIG_CHUNK_MAX_BYTES = 512;
    static constexpr std::size_t MACRO_STEPS_PER_MACRO = 64;
    static constexpr std::size_t MACROS_PER_PROFILE = 32;
    static constexpr std::size_t MAX_DELAY_MS = 60000;
    static constexpr std::size_t PROFILES = 8;
    static constexpr std::size_t QUEUED_MACRO_STARTS = 4;
    static constexpr std::size_t SPI_FRAME_SIZE = 64;
    static constexpr std::size_t TEXT_CHARACTERS_PER_STEP = 1024;
};

enum class CdcMessageType : std::uint8_t {
    CAPTURE_BEGIN = 0x0D,
    CAPTURE_END = 0x0F,
    CAPTURE_EVENT = 0x0E,
    DEVICE_INFO = 0x02,
    FACTORY_RESET_ARM = 0x13,
    FACTORY_RESET_COMMIT = 0x14,
    GET_ACTIVE_CONFIG_INFO = 0x04,
    GET_DIAGNOSTICS = 0x12,
    GET_STATUS = 0x03,
    HELLO = 0x01,
    PING = 0x15,
    READ_CONFIG_BEGIN = 0x05,
    READ_CONFIG_CHUNK = 0x06,
    SET_ACTIVE_PROFILE = 0x0C,
    STOP_AND_RELEASE_ALL = 0x11,
    TEST_MACRO = 0x10,
    WRITE_ABORT = 0x0B,
    WRITE_BEGIN = 0x07,
    WRITE_CHUNK = 0x08,
    WRITE_COMMIT = 0x0A,
    WRITE_VERIFY = 0x09,
};

enum class SpiMessageType : std::uint8_t {
    CONSUMER_STATE = 0x03,
    CONTROL_RELEASE_ALL = 0x05,
    ENDPOINT_STATUS = 0x07,
    HANDSHAKE = 0x01,
    HEARTBEAT = 0x06,
    KBD_STATE = 0x02,
    MOUSE_DELTA = 0x04,
};

enum class MacroStepType : std::uint8_t {
    CONSUMER_TAP = 0x04,
    DELAY = 0x06,
    KEY_DOWN = 0x02,
    KEY_TAP = 0x01,
    KEY_UP = 0x03,
    SET_KEYBOARD_ROUTE = 0x07,
    SET_MOUSE_ROUTE = 0x08,
    SET_PROFILE = 0x09,
    TEXT = 0x05,
};

enum class CdcError : std::uint8_t {
    BadChunk = 0x08,
    BadHash = 0x09,
    BadSequence = 0x04,
    BadSize = 0x07,
    BadState = 0x06,
    Busy = 0x05,
    IncompatibleMajor = 0x02,
    InvalidConfig = 0x0A,
    InvalidRequest = 0x01,
    Ok = 0x00,
    PhysicalConfirmationRequired = 0x0B,
    UnsupportedCapability = 0x03,
};

enum class KeyboardRoute : std::uint8_t {
    BOTH = 0x03,
    PC1 = 0x01,
    PC2 = 0x02,
};

enum class MouseRoute : std::uint8_t {
    PC1 = 0x01,
    PC2 = 0x02,
};

enum class TargetMode : std::uint8_t {
    BOTH = 0x03,
    INHERIT = 0x00,
    PC1 = 0x01,
    PC2 = 0x02,
};

enum class MouseRouteCommand : std::uint8_t {
    PC1 = 0x01,
    PC2 = 0x02,
    TOGGLE = 0x03,
};

enum class TextLayout : std::uint8_t {
    RU = 0x02,
    UA = 0x03,
    US = 0x01,
};

enum class TriggerKind : std::uint8_t {
    KEYBOARD_USAGE = 0x01,
    MOUSE_BUTTON = 0x02,
};

enum class BindingMode : std::uint8_t {
    ADD = 0x02,
    REPLACE = 0x01,
};

enum class ActionKind : std::uint8_t {
    RUN_MACRO = 0x01,
    SET_KEYBOARD_ROUTE = 0x03,
    SET_MOUSE_ROUTE = 0x05,
    SET_PROFILE = 0x06,
    TOGGLE_KEYBOARD_ROUTE = 0x02,
    TOGGLE_MOUSE_ROUTE = 0x04,
};

enum class InputBackend : std::uint8_t {
    CH375 = 0x01,
    PIO_USB = 0x02,
    UNKNOWN = 0x00,
};

enum class Capability : std::uint32_t {
    CAPTURE = 0x20,
    CONFIG_READ = 0x08,
    CONFIG_WRITE = 0x10,
    CONSUMER_HID = 0x04,
    DIAGNOSTICS = 0x80,
    FACTORY_RESET = 0x100,
    KEYBOARD_HID = 0x01,
    MOUSE_HID = 0x02,
    ROUTE_CONTROL = 0x200,
    SPI_ENDPOINT = 0x400,
    TEST_MACRO = 0x40,
};

}  // namespace duo_input::protocol
