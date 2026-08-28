#pragma once

// The CH375 command set, transcribed from WCH's own datasheets.
//
// Every value here is cited in docs/hardware/ch375-command-table.md against
// the section it came from. None of it was taken from a library or a forum:
// those disagree with one another about several of these bytes, and a wrong
// command to a USB host controller does not fail loudly - it answers with a
// number that looks reasonable.
//
// DS1 is "CH375 Datasheet (I)"; DS2 is "CH375 Datasheet (II)", version 4.

#include <cstddef>
#include <cstdint>

namespace duo_input::u1::ch375 {

/// A data block is length-prefixed and the length is 0 to 64. DS1 5.13, 5.14.
inline constexpr std::size_t kMaxBlockSize = 64;

enum class Ch375Command : std::uint8_t {
    // --- general, DS1 -------------------------------------------------------
    /// Chip and firmware version. Bit 7 is set; bits 5-0 are the version. 5.1
    GetIcVersion = 0x01,
    /// Serial rate: division coefficient then constant. Reply comes at the new
    /// rate, so the host must switch immediately after. 5.2
    SetBaudRate = 0x02,
    /// Low-power suspend. 5.3
    EnterSleep = 0x03,
    /// Hardware reset. Takes about 40 ms, during which the chip says nothing. 5.4
    ResetAll = 0x05,
    /// Send any byte, get its bitwise inverse. The one command that proves the
    /// port works at all. 5.5
    CheckExist = 0x06,
    /// Takes a UsbMode. Completes within about 20 µs and answers a status. 5.9
    SetUsbMode = 0x15,
    /// Ask whether a device is attached; answers a connection status. 5.10
    TestConnect = 0x16,
    /// Give up the retry in progress. 5.11
    AbortNak = 0x17,
    /// Read the interrupt status and clear the request. 5.12
    GetStatus = 0x22,
    /// Read a block: length first, then that many bytes. 5.13
    ReadUsbData = 0x28,
    /// Write a block: length first, then that many bytes. 5.14
    WriteUsbData7 = 0x2B,

    // --- host mode, DS2 -----------------------------------------------------
    /// Bus speed. Resets to 12 Mbps full speed whenever SetUsbMode runs. 1.1
    SetUsbSpeed = 0x04,
    /// Takes 07H; bit 4 of the reply set means a 1.5 Mbps low-speed device.
    /// Only valid in host mode 5. 1.2
    GetDeviceRate = 0x0A,
    /// Takes 25H then a retry byte. See kRetry* below. 1.3
    SetRetry = 0x0B,
    /// Parallel port only - unusable over a serial connection. Listed so that
    /// nobody reaches for it. 1.4
    Delay100Us = 0x0F,
    /// Tell the CH375 which device address it is now talking to. Not the same
    /// as SetAddress. 1.5
    SetUsbAddress = 0x13,
    /// Receiver data toggle. 80H means expect DATA0. 1.6
    SetEndpoint6 = 0x1C,
    /// Transmitter data toggle. 80H means send DATA0, C0H means DATA1. 1.7
    SetEndpoint7 = 0x1D,
    /// Same as ReadUsbData, slightly quicker. 1.8
    ReadUsbData0 = 0x27,
    /// Clear a stalled endpoint. 01H-0FH are OUT, 81H-8FH are IN. 1.9
    ClearStall = 0x41,
    /// Ask the device to take a new address. Follow with SetUsbAddress. 1.10
    SetAddress = 0x45,
    /// Fetch a descriptor: 1 for DEVICE, 2 for CONFIGURATION. Answers
    /// USB_INT_BUF_OVER if it is longer than 64 bytes. 1.11
    GetDescriptor = 0x46,
    /// Select a configuration; 0 cancels it. 1.12
    SetConfiguration = 0x49,
    /// GET_DESCR, SET_ADDRESS and SET_CONFIGURATION in one step. 1.13
    AutoSetup = 0x4D,
    /// Sync flag then transaction attribute: sets the toggles and issues the
    /// token in one command. 1.14
    IssueTokenSynced = 0x4E,
    /// Transaction attribute: low nibble is the PID, high nibble the endpoint.
    /// 1.15
    IssueToken = 0x4F,
};

/// DS1 5.9. What "enabled" buys is the chip watching for a device by itself
/// and raising an interrupt when one arrives or leaves.
enum class UsbMode : std::uint8_t {
    DeviceDisabled = 0x00,
    DeviceExternalFirmware = 0x01,
    DeviceBuiltinFirmware = 0x02,
    HostDisabled = 0x04,
    /// Enabled, no SOF packets. Where to sit while waiting for a device.
    HostNoSof = 0x05,
    /// Enabled, SOF packets generated. Where to sit once one is attached.
    HostWithSof = 0x06,
    /// Holds the USB bus in reset, and keeps holding it until the mode
    /// changes. A step in plugging a device in, never a resting state.
    HostReset = 0x07,
};

/// DS1, the operation-status table. These two are the whole set: a third value
/// means the port has lost step with the chip.
enum class CommandStatus : std::uint8_t {
    Success = 0x51,
    Abort = 0x5F,
};

/// DS1 5.12. Carried as a byte rather than a closed enumeration, because the
/// failure statuses are a 32-value range that encodes *why* a transaction
/// failed - see is_failure() below.
enum class InterruptStatus : std::uint8_t {
    Success = 0x14,
    Connect = 0x15,
    Disconnect = 0x16,
    BufferOver = 0x17,
    DiskRead = 0x1D,
    DiskWrite = 0x1E,
    DiskError = 0x1F,
};

/// DS1 5.12: bit 5 set marks the byte as an operation failure.
inline constexpr bool is_failure(InterruptStatus status) {
    return (static_cast<std::uint8_t>(status) & 0x20) != 0;
}

/// DS1 5.12, bits 3-0 of a failure status: what the device answered.
inline constexpr std::uint8_t failure_response(InterruptStatus status) {
    return static_cast<std::uint8_t>(status) & 0x0F;
}

inline constexpr std::uint8_t kResponseNak = 0b1010;
inline constexpr std::uint8_t kResponseStall = 0b1110;

/// DS2 1.15. Low nibble of a transaction attribute.
enum class TokenPid : std::uint8_t {
    Setup = 0x0D,
    Out = 0x01,
    In = 0x09,
};

/// DS2 1.15: high nibble is the endpoint, low nibble the PID.
inline constexpr std::uint8_t transaction(std::uint8_t endpoint, TokenPid pid) {
    return static_cast<std::uint8_t>((endpoint << 4) | static_cast<std::uint8_t>(pid));
}

/// DS2 1.6, 1.7: the data toggle, written by hand.
inline constexpr std::uint8_t kToggleData0 = 0x80;
inline constexpr std::uint8_t kToggleData1 = 0xC0;

/// DS2 1.3. SetRetry takes this byte first, then the retry policy.
inline constexpr std::uint8_t kSetRetryPrefix = 0x25;
/// DS2 1.2. GetDeviceRate takes this byte.
inline constexpr std::uint8_t kGetDeviceRatePrefix = 0x07;

/// DS2 1.3. Bits 7-6 choose the NAK policy; bits 5-0 are timeout retries.
///
/// The chip's own default is 0x85 - retry a NAK forever. That is the wrong
/// default here: a keyboard that stops answering must not hold the firmware
/// inside a single command while the watchdog goes unfed and U2 loses the
/// link. Report the NAK instead and let the caller decide.
inline constexpr std::uint8_t kRetryReportNak = 0x0F;

/// DS2 1.1. What the bus runs at. Full speed is the default after any change
/// of working mode, which is why a low-speed device has to be told again.
enum class UsbSpeed : std::uint8_t {
    Full12Mbps = 0x00,
    Low1_5Mbps = 0x02,
};

/// DS2 1.11. The only two descriptor types GetDescriptor accepts.
enum class DescriptorType : std::uint8_t {
    Device = 1,
    Configuration = 2,
};

/// One rung of the speed ladder: the chip's two divisor bytes and the rate
/// they produce (DS1 5.2 - coefficient 02H gives 750000/(256-constant) and
/// 03H gives 6000000/(256-constant)).
struct BaudOption {
    std::uint8_t coefficient;
    std::uint8_t constant;
    unsigned baud;
};

/// How many filler bytes it takes to free a chip that has read noise.
///
/// Longer than any command's parameter list because the point is that nobody
/// knows what it is waiting for. Sixty-four is a whole endpoint buffer and
/// costs about seventy milliseconds at the rate the chip comes up at.
inline constexpr std::size_t kWedgeFlushBytes = 64;

/// Sampling rates to try when a channel answers nothing.
///
/// Spread either side of the rate the chip actually uses. A reply that reads
/// correctly at one of the neighbours means this side's timing is off - the
/// divider, the system clock, the cycles per bit - and not that the chip is
/// silent. A reply at none of them means it really is silent.
inline constexpr unsigned kRxSweepRates[] = {9600, 9200, 10000, 8800, 10400, 8400, 11000};
inline constexpr std::size_t kRxSweepCount = sizeof(kRxSweepRates) / sizeof(kRxSweepRates[0]);

/// How many refused mode commands before looking for the chip elsewhere.
///
/// Not every time: the search writes to rates the chip may not be using, which
/// is exactly what must not be done casually. But a channel that has refused
/// this many in a row is already unreachable, and looking cannot make it more
/// so.
inline constexpr std::uint16_t kLostChipSearchEvery = 8;

/// How many times a rate has to answer before it is believed.
inline constexpr int kPortProofRounds = 2;

/// The byte CHECK_EXIST is asked with when the port itself is in question.
///
/// Any value works - the chip answers the inverse of whatever it is given
/// (DS1 5.5) - but one with alternating bits fails loudly on a port that is
/// sampling at the wrong rate, where a byte of all ones or all zeroes can
/// survive the mistake and look like an answer.
inline constexpr std::uint8_t kPortProbeByte = 0xA5;

/// The rate a CH375 comes up at, and returns to after RESET_ALL (DS1 5.2).
inline constexpr unsigned kCh375DefaultBaud = 9600;

/// What the port is raised to once the chip is answering.
///
/// At 9600 a frame is eleven bits and so costs 1.15 ms, and one mouse report
/// takes fifteen bytes to collect: seventeen milliseconds for something a
/// moving mouse produces every eight. DS1 5.2 lists this rate with the same
/// 0.16% error as the default, so it is no less reliable - only twelve times
/// faster, which turns that seventeen milliseconds into 1.4.
inline constexpr unsigned kCh375FastBaud = 115200;

/// The rates to try, fastest first.
///
/// Both are exact or near-exact on the chip's own divisor, and both are far
/// enough above 9600 to make the difference that matters: at 9600 one mouse
/// report costs seventeen milliseconds and a moving hand produces one every
/// eight, so anything above about 20000 closes the gap.
///
/// A ladder rather than a single rate, because the two channels on this board
/// do not manage the same speed - one runs at 115200 and the other refuses it
/// every time. Whether that is wiring, length or the module, the firmware
/// cannot tell and does not need to.
inline constexpr BaudOption kBaudLadder[] = {
    {0x03, 0xCC, 115200},  // 6000000 / 52
    {0x02, 0xF4, 62500},   // 750000 / 12, exact
    {0x02, 0xEC, 37500},   // 750000 / 20, exact
};
inline constexpr std::size_t kBaudLadderSize = sizeof(kBaudLadder) / sizeof(kBaudLadder[0]);
inline constexpr std::uint8_t kFastBaudCoefficient = 0x03;
inline constexpr std::uint8_t kFastBaudConstant = 0xCC;

}  // namespace duo_input::u1::ch375
