// U1 entry point.
//
// The two cores divide the work along one line and never cross it. Core 0 owns
// USB, the SPI link to U2, flash and the whole output state; Core 1 owns the
// peripherals, the bindings, the capture and the macros. Everything Core 1
// decides becomes a command in a queue that Core 0 drains, which is why there
// is no lock anywhere between a keypress and a HID report.
//
// What crosses the other way is small and deliberate: a capture the host asked
// for, a profile it asked for, and the answers to both. Those go through the
// config service's take_* accessors rather than either side reaching into the
// other, because the alternative is a USB callback writing state that another
// core is in the middle of reading.

#include "pico/multicore.h"
#include "pico/stdlib.h"

#include "tusb.h"

#include <cstdio>
#include <cstring>

#include "hardware/watchdog.h"

#include "buttons.hpp"
#include "ch375/descriptor_setup.hpp"
#include "ch375_probe.hpp"
#include "config_profiles.hpp"
#include "config_service.hpp"
#include "core1_runtime.hpp"
#include "diagnostics/ch375_baud_scan.hpp"
#include "diagnostics_service.hpp"
#include "hid/state_manager.hpp"
#include "input/pipeline.hpp"
#include "output_runtime.hpp"
#include "pico_flash.hpp"
#include "spi_master.hpp"
#include "storage/ab_store.hpp"
#include "usb_service.hpp"

namespace {

// Core 0 owns this. Core 1 only ever submits commands to it, so there is
// exactly one writer and no locking between a keypress and a USB report.
duo_input::u1::OutputRuntime g_outputs;

/// Core 1's only reach into the output: the queue, and nothing else.
///
/// Not the HID state, not the link, not the reports. A full queue is refused
/// rather than waited on - Core 1 cannot block on Core 0, which is busy being
/// blocked on the host.
class QueuedCommands final : public duo_input::u1::ICommandSink {
public:
    bool submit(const duo_input::runtime::OutputCommand& command) override {
        return g_outputs.submit(command);
    }
};

QueuedCommands g_commands;

/// The stored configuration, pointed at where it lies in flash.
duo_input::u1::StoredProfiles g_profiles;

/// Everything between a peripheral report and a queued command.
duo_input::u1::Core1Runtime g_runtime(g_commands, g_profiles);

#if DUO_CH375_PROBE
/// Where a normalized event goes.
class RuntimeInput final : public duo_input::u1::input::IInputHandler {
public:
    void on_input(const duo_input::u1::input::InputEvent& event,
                  std::uint32_t now_ms) override {
        g_runtime.handle_input(event, now_ms);
    }
};

RuntimeInput g_input;
duo_input::u1::input::InputPipeline g_keyboard_pipeline(g_input);
duo_input::u1::input::InputPipeline g_mouse_pipeline(g_input);

// The controllers, the ports beneath them and the enumeration above them.
//
// At namespace scope rather than inside main, because Core 1 is what ticks
// them now and it cannot see main's locals. They were already static: each
// carries an event queue of eight 64-byte reports, and main's frame has to fit
// in a two-kilobyte stack.
duo_input::u1::ch375::PioCh375Transport g_keyboard_port;
duo_input::u1::ch375::PioCh375Transport g_mouse_port;
duo_input::u1::ch375::Ch375Transport g_keyboard_commands(g_keyboard_port);
duo_input::u1::ch375::Ch375Transport g_mouse_commands(g_mouse_port);
// Enumerated by hand rather than with AUTO_SETUP, which assigns an address
// without saying which and never reports the endpoint - see
// descriptor_setup.hpp.
duo_input::u1::ch375::DescriptorSetup g_keyboard_setup(g_keyboard_commands);
duo_input::u1::ch375::DescriptorSetup g_mouse_setup(g_mouse_commands);
duo_input::u1::ch375::Ch375Device g_keyboard_device(g_keyboard_commands, g_keyboard_setup);
duo_input::u1::ch375::Ch375Device g_mouse_device(g_mouse_commands, g_mouse_setup);

struct DeviceTally {
    std::uint8_t attached = 0;
    std::uint8_t detached = 0;
    std::uint8_t ready = 0;
    std::uint8_t reports = 0;
    std::uint8_t last_size = 0;
    std::uint8_t last[8] = {};
};

DeviceTally g_keyboard_tally;
DeviceTally g_mouse_tally;

/// How long a pass round Core 1 takes.
///
/// The state machine polls an endpoint every 8 ms and gives a configured
/// device a second before declaring it lost. Both are meaningless if a pass
/// takes longer than they do - and a device that came up was once declared
/// gone without a single poll being issued, which is what that looks like.
/// Measured on the core that ticks the controllers, because that is the loop
/// those deadlines are written against.
std::uint32_t g_last_pass_us = 0;
std::uint32_t g_worst_pass_us = 0;
#endif

/// Install a profile's macros, indexed by the slot a binding names.
///
/// The definitions point into the step pool inside StoredProfiles, which this
/// rewrites. Safe only because the scheduler was stopped and drained before
/// the profile changed, so nothing is mid-macro reading what this replaces.
void install_macros(std::uint8_t profile) {
    // Static: Core 1 has a two-kilobyte stack and the binding table sits below
    // this on the same path.
    static duo_input::u1::macros::MacroDefinition
        definitions[duo_input::u1::kMaxProfileMacros];
    g_profiles.macros_for(profile, definitions, duo_input::u1::kMaxProfileMacros);
    for (std::size_t slot = 0; slot < duo_input::u1::kMaxProfileMacros; ++slot) {
        g_runtime.define_macro(static_cast<std::uint8_t>(slot), definitions[slot]);
    }
}

/// Core 1: peripherals in, commands out, and nothing else.
///
/// It never touches the output state, the link or USB. It also never blocks,
/// which is what lets Core 0 keep feeding a two-second watchdog while a macro
/// with a two-second pause in it is running.
void core1_entry() {
    // Core 0 erases and programs flash, and it cannot do that while this core
    // might be fetching instructions from the chip being erased. This is what
    // lets it stop us; without it the request would wait forever.
    //
    // Announced from here rather than from Core 0, and only after arming:
    // between launching a core and that core arming itself there is a window
    // where it is running from flash and cannot yet be stopped, and a write
    // landing in it would be a request that never returns.
    multicore_lockout_victim_init();
    duo_input::u1::set_core1_running(true);

    std::uint8_t installed = g_profiles.active_profile_id();
    g_runtime.set_profile_now(installed);
    install_macros(installed);

    while (true) {
        const std::uint32_t now_ms = to_ms_since_boot(get_absolute_time());

#if DUO_CH375_PROBE
        const std::uint32_t now_us = time_us_32();
        if (g_last_pass_us != 0) {
            const std::uint32_t elapsed = now_us - g_last_pass_us;
            if (elapsed > g_worst_pass_us) {
                g_worst_pass_us = elapsed;
            }
        }
        g_last_pass_us = now_us;

        duo_input::u1::ch375::Ch375Device* devices[2] = {&g_keyboard_device, &g_mouse_device};
        duo_input::u1::ch375::DescriptorSetup* setups[2] = {&g_keyboard_setup, &g_mouse_setup};
        duo_input::u1::input::InputPipeline* pipelines[2] = {&g_keyboard_pipeline,
                                                             &g_mouse_pipeline};
        DeviceTally* tallies[2] = {&g_keyboard_tally, &g_mouse_tally};
        // Static: one of these is seventy-odd bytes of report buffer, and this
        // core has two kilobytes for everything below it.
        static duo_input::u1::ch375::Ch375Event event;
        for (int index = 0; index < 2; ++index) {
            devices[index]->tick(now_us);
            while (devices[index]->take_event(event)) {
                DeviceTally& tally = *tallies[index];
                switch (event.kind) {
                    case duo_input::u1::ch375::Ch375EventKind::Attached:
                        ++tally.attached;
                        break;
                    case duo_input::u1::ch375::Ch375EventKind::Detached:
                        ++tally.detached;
                        break;
                    case duo_input::u1::ch375::Ch375EventKind::Ready:
                        ++tally.ready;
                        break;
                    case duo_input::u1::ch375::Ch375EventKind::Report: {
                        ++tally.reports;
                        const std::size_t keep = event.report_size > sizeof(tally.last)
                                                     ? sizeof(tally.last)
                                                     : event.report_size;
                        tally.last_size = static_cast<std::uint8_t>(keep);
                        std::memcpy(tally.last, event.report, keep);
                        break;
                    }
                    default:
                        break;
                }
                // A detach synthesises the releases the peripheral never sent,
                // which is the only thing standing between a yanked cable and
                // a computer that types until it is rebooted.
                pipelines[index]->on_event(event, setups[index]->kind(), now_ms);
            }
        }
#endif

        g_runtime.tick(now_ms);

        // A swap happened - a binding, a macro, or the host asked for one. The
        // bindings moved with it and the macros have to follow.
        if (g_runtime.active_profile() != installed) {
            installed = g_runtime.active_profile();
            install_macros(installed);
        }
    }
}

// Both switches are read as active-low with an internal pull-up, from the
// first instant, so a board that stops early still has defined input pins
// rather than floating ones.
using duo_input::u1::kPinSw1;
using duo_input::u1::kPinSw2;

/// How long the loop may stall before the watchdog restarts the board.
///
/// Comfortably longer than the slowest thing the loop does - a flash sector
/// erase, a few tens of milliseconds - and short enough that a device which
/// has stopped responding recovers before the operator gives up on it.
constexpr std::uint32_t kWatchdogMs = 2000;

void configure_button(uint pin) {
    gpio_init(pin);
    gpio_set_dir(pin, GPIO_IN);
    gpio_pull_up(pin);
}

#if DUO_CH375_PROBE
duo_input::u1::PinActivity watch_existing_pin(unsigned pin, std::uint32_t for_us) {
    duo_input::u1::PinActivity activity;
    const std::uint32_t started = time_us_32();
    std::uint32_t samples = 0;
    std::uint32_t low = 0;
    bool level = gpio_get(pin);
    while (time_us_32() - started < for_us) {
        const bool now = gpio_get(pin);
        ++samples;
        if (!now) {
            ++low;
        }
        if (now != level) {
            level = now;
            if (activity.transitions < 0xFFFF) {
                ++activity.transitions;
            }
        }
    }
    if (samples != 0) {
        activity.low_percent = static_cast<std::uint8_t>((low * 100u) / samples);
    }
    return activity;
}
#endif

/// Sends the configurator's replies back down the CDC pipe.
class CdcWriter : public duo_input::u1::CdcSink {
public:
    void write(const std::uint8_t* data, std::size_t size) override {
        // Written whether or not the host has raised DTR. Gating on it meant a
        // host that opened the port without setting the line got every request
        // accepted and no answer at all, which is indistinguishable from a
        // device that is not there - and QSerialPort does not raise DTR on
        // open, so that host was the configurator.
        //
        // A host that stopped reading cannot wedge this loop either: TinyUSB's
        // FIFO discards rather than blocks, and the configurator retries.
        tud_cdc_write(data, static_cast<std::uint32_t>(size));
        tud_cdc_write_flush();
    }
};

/// Where SW1 has decided the mouse should go.
///
/// A toggle rather than a fixed destination, because the button exists for the
/// case where the operator cannot see which computer currently has the mouse.
/// Nothing generates mouse input yet - the CH375B arrives in the next plan -
/// so this state has nothing to route today, and it is kept rather than faked
/// into a command that would do nothing.
bool g_mouse_on_pc2 = false;

/// The LED reports whether U2 is answering.
///
/// That is the most useful thing this one lamp can say right now: it is the
/// only outward sign of a four-wire link that someone can knock loose, and it
/// goes dark within the same 100 ms in which U2 releases everything it holds.
void show_link(bool healthy) {
#ifdef PICO_DEFAULT_LED_PIN
    gpio_put(PICO_DEFAULT_LED_PIN, healthy ? 1 : 0);
#endif
}

duo_input::runtime::OutputCommand release_pc1() {
    duo_input::runtime::OutputCommand command;
    command.kind = duo_input::runtime::CommandKind::ReleaseRoute;
    command.route = duo_input::runtime::Route::Pc1;
    return command;
}

void configure_indicator() {
#ifdef PICO_DEFAULT_LED_PIN
    gpio_init(PICO_DEFAULT_LED_PIN);
    gpio_set_dir(PICO_DEFAULT_LED_PIN, GPIO_OUT);
    // Off, not on: an LED lit before the firmware can do anything tells the
    // operator the device is ready when it is not.
    gpio_put(PICO_DEFAULT_LED_PIN, 0);
#endif
}

}  // namespace


#if DUO_CH375_PROBE
/// Put a name to the byte a refused mode command came back with.
///
/// DS1 5.1 documents exactly two answers to a command that carries a
/// status: 51H success and 5FH abort. Anything else is not a refusal at
/// all - it is the port reading somebody else's byte, which is a different
/// fault with a different repair.
const char* describe_mode_reply(bool answered, std::uint8_t reply) {
    if (!answered) {
        return "no reply - the chip is not listening";
    }
    switch (reply) {
        case 0x51:
            return "success - refused for another reason";
        case 0x5F:
            return "abort - the chip refused the mode";
        default:
            return "undocumented - the port is out of step";
    }
}

/// Put a name to the byte AUTO_SETUP ended on.
///
/// DS1 5.12: bit 5 marks a failure and the low four bits carry what the device
/// itself answered. Those are different faults with the same appearance from
/// outside - a device that refuses, one that stalls, and one that is not
/// answering at all want three different repairs.
const char* describe_setup_status(std::uint8_t status) {
    switch (status) {
        case 0xFF:
            return "still running";
        case 0xFE:
            return "no reply to GET_STATUS";
        case 0xFD:
            return "no interrupt before the deadline";
        case 0x14:
            return "success";
        case 0x15:
            return "connect";
        case 0x16:
            return "disconnect";
        case 0x17:
            return "buffer overflow or bad transfer";
        default:
            break;
    }
    if ((status & 0x20) == 0) {
        return "not a host-mode status";
    }
    switch (status & 0x0F) {
        case 0b1010:
            return "device answered NAK";
        case 0b1110:
            return "device answered STALL";
        case 0b0000:
        case 0b0100:
        case 0b1000:
        case 0b1100:
            return "device did not answer - timeout";
        default:
            return "device answered with another PID";
    }
}
#endif

int main() {
    configure_indicator();
    configure_button(kPinSw1);
    configure_button(kPinSw2);

    // Read before anything else can obscure it: once the hardware flags are
    // cleared, a watchdog reset is indistinguishable from a power cycle.
    const duo_input::diagnostics::ResetRecord reset = duo_input::u1::read_reset_record();
    (void)reset;  // Reported over CDC once protocol v1 carries a field for it.

    // Static, for the same reason as the controllers below: main's frame has
    // to fit in core 0's two-kilobyte stack, and ConfigService alone carries
    // three wire-frame buffers and the diagnostic payload - over four
    // kilobytes that outlive every call anyway.
    static duo_input::u1::UsbService usb;
    static duo_input::u1::SpiMaster link;
    static duo_input::u1::PicoFlash flash;
    static duo_input::storage::AbStore store(flash);
    static CdcWriter cdc_writer;
    static duo_input::u1::ConfigService config(store, cdc_writer);

    // Whatever was stored last time is what the device runs now.
    //
    // Read before Core 1 is launched, because Core 1 reads the bindings out of
    // it the moment it starts. The bytes are not copied - they are pointed at
    // where they lie in flash, which outlives everything that reads them.
    const duo_input::storage::ScanResult stored = store.scan();
    if (stored.has_active) {
        const duo_input::protocol::ByteView package =
            store.payload_view(stored.active, stored.active_slot().size);
        if (package.data != nullptr && g_profiles.load(package)) {
            config.set_active_profile(g_profiles.active_profile_id());
        }
    }

#if DUO_CH375_PROBE
    // Both ports come up before anything else touches these pins, and before
    // Core 1 - which owns the controllers above them - is launched.
    //
    // Before any state machine touches them, read both receive pads as plain
    // inputs. A pad with nothing on it and a pull-up should sit high and never
    // move; if one of them does move, the answer is about solder, not software.
    const duo_input::u1::PinActivity keyboard_pad =
        duo_input::u1::watch_bare_pin(duo_input::u1::kPinKeyboardRx, 1000);
    const duo_input::u1::PinActivity mouse_pad =
        duo_input::u1::watch_bare_pin(duo_input::u1::kPinMouseRx, 1000);

    // Watch both interrupt lines for a second rather than sampling them once.
    //
    // The single-shot version pulled the pin down and called it live only if
    // it stayed high - which is true of an idle INT# and false of one that is
    // asserting, because the signal is active low. So it reported "no wire" for
    // whichever chip happened to have an interrupt pending, which is exactly
    // the chip with a device on it. It measured the wrong thing confidently.
    const duo_input::u1::PinActivity keyboard_int =
        duo_input::u1::watch_bare_pin(duo_input::u1::kPinKeyboardInt, 1000);
    const duo_input::u1::PinActivity mouse_int =
        duo_input::u1::watch_bare_pin(duo_input::u1::kPinMouseInt, 1000);

    // Static-level loopback test, deliberately performed before PIO owns the
    // pins.  With S1 tied to S3 the level driven on GP0 must return on GP1.
    // This separates an electrical oscillator from a malformed UART program:
    // no UART state machine is running while these four readings are taken.
    gpio_init(duo_input::u1::kPinKeyboardTx);
    gpio_set_dir(duo_input::u1::kPinKeyboardTx, GPIO_OUT);
    gpio_put(duo_input::u1::kPinKeyboardTx, 1);
    sleep_us(100);
    const duo_input::u1::PinActivity tx_while_high =
        watch_existing_pin(duo_input::u1::kPinKeyboardTx, 3000);
    const duo_input::u1::PinActivity rx_while_high =
        watch_existing_pin(duo_input::u1::kPinKeyboardRx, 3000);

    gpio_put(duo_input::u1::kPinKeyboardTx, 0);
    sleep_us(100);
    const duo_input::u1::PinActivity tx_while_low =
        watch_existing_pin(duo_input::u1::kPinKeyboardTx, 3000);
    const duo_input::u1::PinActivity rx_while_low =
        watch_existing_pin(duo_input::u1::kPinKeyboardRx, 3000);
    gpio_put(duo_input::u1::kPinKeyboardTx, 1);

    // Repeat the same static test on the independently wired mouse channel.
    // Two channels failing alike implicate their shared translator/power
    // arrangement; one failing alone points back to that channel's wiring.
    gpio_init(duo_input::u1::kPinMouseTx);
    gpio_set_dir(duo_input::u1::kPinMouseTx, GPIO_OUT);
    gpio_put(duo_input::u1::kPinMouseTx, 1);
    sleep_us(100);
    const duo_input::u1::PinActivity mouse_tx_while_high =
        watch_existing_pin(duo_input::u1::kPinMouseTx, 3000);
    const duo_input::u1::PinActivity mouse_rx_while_high =
        watch_existing_pin(duo_input::u1::kPinMouseRx, 3000);
    gpio_put(duo_input::u1::kPinMouseTx, 0);
    sleep_us(100);
    const duo_input::u1::PinActivity mouse_tx_while_low =
        watch_existing_pin(duo_input::u1::kPinMouseTx, 3000);
    gpio_put(duo_input::u1::kPinMouseTx, 1);

    g_keyboard_port.begin(pio0, duo_input::u1::kPinKeyboardTx, duo_input::u1::kPinKeyboardRx,
                          duo_input::u1::kPinKeyboardInt);
    g_mouse_port.begin(pio0, duo_input::u1::kPinMouseTx, duo_input::u1::kPinMouseRx,
                       duo_input::u1::kPinMouseInt);
    // Before a single byte goes out, on either port.
    std::uint16_t keyboard_bad_at_boot = 0;
    std::uint16_t mouse_bad_at_boot = 0;
    const std::uint16_t keyboard_quiet_at_boot =
        duo_input::u1::listen_without_sending(g_keyboard_port, 1000, keyboard_bad_at_boot);
    const std::uint16_t mouse_quiet_at_boot =
        duo_input::u1::listen_without_sending(g_mouse_port, 1000, mouse_bad_at_boot);

    // The static level readings above say the wiring is sane. They cannot say
    // the two data lines are the right way round, or that a chip is in serial
    // mode - only asking it something can. CHECK_EXIST needs nothing to be
    // configured first: send a byte, get its inverse back (DS1 5.5).
    //
    // Asked once, here, before Core 1 takes the chips over.
    //
    // Running it periodically alongside them made two owners of one chip: the
    // probe sets the working mode and reads statuses, and reading a status is
    // what clears it - so each was consuming the interrupts the other was
    // waiting for. On the bench that looked like a device attaching and
    // detaching twenty-three times in a row. The controllers now live on the
    // other core, which makes that mistake harder to make by accident.
    const duo_input::u1::Ch375ProbeResult keyboard_probe =
        duo_input::u1::probe_ch375(g_keyboard_port, g_keyboard_commands);
    const duo_input::u1::Ch375ProbeResult mouse_probe =
        duo_input::u1::probe_ch375(g_mouse_port, g_mouse_commands);
    // The experiment that left the bus reset out changed nothing - the device
    // was lost at exactly the same rate without it - so the reset is not what
    // loses it, and the datasheet's sequence is back.

    duo_input::diagnostics::Ch375SingleProbeObservation single_probe;
    single_probe.pad_low_percent = tx_while_high.low_percent;
    single_probe.pad_transitions = tx_while_high.transitions;
    single_probe.quiet_frames = rx_while_high.low_percent;
    single_probe.quiet_bad_frames = rx_while_high.transitions;
    single_probe.probe_quiet_frames = tx_while_low.low_percent;
    single_probe.probe_quiet_first = rx_while_low.low_percent;
    single_probe.raw_count = static_cast<std::uint8_t>(tx_while_low.transitions & 0xFFu);
    single_probe.raw[0] = rx_while_low.transitions;
    single_probe.raw[1] = mouse_tx_while_high.transitions;
    single_probe.raw[2] = static_cast<std::uint16_t>(mouse_tx_while_high.low_percent) |
                          (static_cast<std::uint16_t>(mouse_rx_while_high.low_percent) << 8);
    single_probe.raw[3] = mouse_rx_while_high.transitions;
    single_probe.framing_errors = mouse_tx_while_low.low_percent;
#endif


#if DUO_SPI_DEBUG
    // Two independent answers, taken before the SPI block claims the pins.
    //
    // The first uses the peripheral's internal loop back and touches no pin at
    // all, so it speaks only about U1. The second drives every combination of
    // the outgoing lines and watches the incoming one, so it speaks only about
    // the wires and U2. Asked together they say which half is at fault; asked
    // as one number they say nothing, which is where the last two days went.
    static const std::uint8_t kSelfTestTx[6] = {0x00, 0x55, 0xAA, 0xFF, 0xA5, 0x5A};
    std::uint8_t self_test_rx[6] = {};
    duo_input::u1::SpiMaster::internal_loopback(kSelfTestTx, self_test_rx, sizeof(kSelfTestTx));
    const std::uint8_t wire_walk = duo_input::u1::SpiMaster::wire_walk();
#endif

    usb.begin();
    link.begin();

    // Everything Core 1 reads at start-up is in place, so it can go. It tells
    // the flash routines about itself once it can be stopped by them.
    multicore_launch_core1(core1_entry);

    duo_input::u1::Buttons buttons;
    bool was_mounted = false;

    // Armed only now, with every service in place. It is fed at the end of the
    // loop, after USB, the link and the command queue have all been serviced,
    // so what it actually guarantees is that those keep happening - not merely
    // that some instruction somewhere is still executing.
    watchdog_enable(kWatchdogMs, true);

    while (true) {
        const std::uint32_t now_ms = to_ms_since_boot(get_absolute_time());

        usb.task();

        const bool mounted = usb.mounted();
        if (mounted != was_mounted) {
            // A host that has just enumerated knows nothing about the reports
            // sent before, and anything held while unplugged was never
            // released as far as it is concerned. Start from nothing.
            g_outputs.process(release_pc1());
            usb.forget_sent_state();
            if (!mounted) {
                // The host went away. Anything it had staged is abandoned.
                config.on_disconnect();
            }
            was_mounted = mounted;
        }

        // The configurator's side of the conversation.
        if (tud_cdc_available()) {
            std::uint8_t incoming[64];
            const std::uint32_t read = tud_cdc_read(incoming, sizeof(incoming));
            config.on_cdc_bytes(incoming, read);
        }
        if (config.take_release_all_request()) {
            g_outputs.release_all();
            link.send_release_all(now_ms);
            // Asked for rather than done here: the command queue has exactly
            // one producer and this core is not it.
            g_runtime.request_release_all();
        }

        // What the host asked of Core 1, and what Core 1 has to say back.
        //
        // Both directions pass through here rather than either side calling
        // into the other, because one of those sides is a USB callback and the
        // other is halfway through reading a binding table.
        switch (config.take_capture_request()) {
            case duo_input::u1::CaptureRequest::Begin:
                g_runtime.begin_capture(now_ms);
                break;
            case duo_input::u1::CaptureRequest::Cancel:
                g_runtime.cancel_capture();
                break;
            case duo_input::u1::CaptureRequest::None:
                break;
        }
        {
            // Taken before the state below is published, so a capture that
            // ended by being answered is reported rather than swallowed.
            duo_input::u1::mapping::CapturedTrigger captured;
            if (g_runtime.take_capture_event(captured)) {
                config.emit_capture_event(captured);
            }
        }
        config.set_capture_active(g_runtime.capture_active());

        std::uint8_t profile = 0;
        if (config.take_profile_request(profile)) {
            g_runtime.request_profile(profile);
        }
        if (g_runtime.take_profile_ack(profile)) {
            // Only now is it true. Core 1 has stopped its macros and let go of
            // what was held under the old profile's meaning.
            config.set_active_profile(profile);
        }

        // Bounded, so a burst of input cannot starve the USB it is for.
        g_outputs.drain();

        usb.publish(g_outputs);

        // PC2's half of the state goes over the link. It sends on change and
        // otherwise heartbeats, so a quiet device does not saturate the bus
        // and does not look severed either.
        link.poll(now_ms, g_outputs.take_snapshot(duo_input::hid::Target::Pc2));

        // Published every pass, so the host can see the link rather than infer
        // it from an absence of errors.
        {
            duo_input::u1::LinkState state;
            state.answered = link.status().answered;
            state.mounted = link.status().mounted;
            state.frames_sent = link.frames_sent();
            state.crc_errors = link.status().crc_errors;
            state.echoed_frames = link.status().echoed_frames;
            state.endpoint_drops = link.status().endpoint_drops;
            state.endpoint_release_ms = link.status().endpoint_release_ms;
            config.set_link_state(state);
        }
        show_link(link.status().answered);


#if DUO_CH375_PROBE
        {
            // A report and nothing else. The controllers are ticked on Core 1,
            // which is where their event queues are drained and where their
            // 8 ms poll deadlines are actually measured; two cores ticking one
            // chip made each consume the interrupts the other was waiting for,
            // and on the bench that looked like a device attaching and
            // detaching twenty-three times in a row.
            duo_input::u1::ch375::Ch375Device* devices[2] = {&g_keyboard_device,
                                                             &g_mouse_device};
            duo_input::u1::ch375::DescriptorSetup* setups[2] = {&g_keyboard_setup,
                                                                &g_mouse_setup};
            DeviceTally* tallies[2] = {&g_keyboard_tally, &g_mouse_tally};
            const std::uint32_t worst_pass_us = g_worst_pass_us;

            // Written as text rather than packed into a struct.
            //
            // Every layout change to the packed version cost a reader that
            // silently drifted, and three separate wrong conclusions were
            // drawn from fields that had moved underneath it - including one
            // line that read "no interrupts were ever seen" beside "seventeen
            // devices attached". Text cannot come apart that way, and the
            // whole point of this build is to be believed.
            static const char* kStates[] = {"Absent",      "Resetting",   "HostMode",
                                            "Enumerating", "Ready",       "RecoverWait",
                                            "Fault"};
            // Zeroed, because what is sent is measured from what was
            // written - and anything past that in an uninitialised
            // buffer goes out as part of the message.
            static char text[900] = {};
            int used = 0;
            const char* names[2] = {"keyboard", "mouse"};
            for (int index = 0; index < 2 && used < static_cast<int>(sizeof(text)) - 1; ++index) {
                const duo_input::u1::ch375::Ch375Device& device = *devices[index];
                const DeviceTally& tally = *tallies[index];
                const unsigned state = static_cast<unsigned>(device.state());
                used += snprintf(
                    text + used, sizeof(text) - static_cast<std::size_t>(used),
                    "%s state=%s speed=%s attached=%u gone=%u ready=%u reports=%u\n"
                    "  check_exist=%s int_seen=%u status_read_failed=%u\n"
                    "  connect=%u disconnect=%u success=%u failure=%u impossible=%u\n"
                    "  detach_disconnect=%u detach_lost=%u enum_failed=%u mode_failed=%u\n"
                    "  setup attempts=%u last=0x%02X (%s) polls=%u\n"
                    "  found=%s endpoint=%u packet=%u boot=%s parse=%u\n"
                    "  last report (%u bytes): %02X %02X %02X %02X\n"
                    "  port=%u baud, refused_changes=%u\n"
                    "  mode_refused setup=%u recover=%u  found_elsewhere=%u alive_refusing=%u\n"
                    "  not_back_yet=%u recovered_raised=%u quiet_rearms=%u collapses=%u\n"
                    "  mode_reply=%s 0x%02X (%s)\n"
                    "  slowest pass round the loop=%u us\n",
                    names[index], state < 7 ? kStates[state] : "?",
                    device.device_is_low_speed() ? "low" : "full", tally.attached, tally.detached,
                    tally.ready, tally.reports,
                    (index == 0 ? keyboard_probe : mouse_probe).check_exist_ok ? "0xA8" : "WRONG",
                    device.interrupts_seen(), device.status_reads_failed(),
                    device.status_connect(), device.status_disconnect(), device.status_success(),
                    device.status_failure(), device.status_impossible(),
                    device.detach_from_disconnect(), device.detach_from_lost(),
                    device.enumerate_failures(), device.mode_failures(),
                    setups[index]->attempts(), setups[index]->last_status(),
                    describe_setup_status(setups[index]->last_status()),
                    device.polls_issued(),
                    setups[index]->kind() == duo_input::u1::ch375::DeviceKind::Keyboard
                        ? "keyboard"
                        : (setups[index]->kind() == duo_input::u1::ch375::DeviceKind::Mouse
                               ? "mouse"
                               : "nothing"),
                    setups[index]->interrupt_endpoint(), setups[index]->max_packet(),
                    setups[index]->boot_protocol() ? "yes" : "no",
                    static_cast<unsigned>(setups[index]->last_parse_error()),
                    tally.last_size, tally.last[0], tally.last[1], tally.last[2], tally.last[3],
                    (index == 0 ? g_keyboard_port : g_mouse_port).baud(),
                    device.baud_change_failures(), device.setup_mode_failures(),
                    device.recover_mode_failures(), device.chip_found_elsewhere(),
                    device.alive_but_refusing(),
                    device.chip_not_back_yet(), device.chip_recovered_from_raised(),
                    device.quiet_rearms(), device.collapses_while_raised(),
                    device.mode_answered() ? "answered" : "silent", device.mode_reply(),
                    describe_mode_reply(device.mode_answered(), device.mode_reply()),
                    worst_pass_us);
                // snprintf answers with how much it *would* have written. Left
                // unclamped, the next call is handed a negative amount of room
                // and the total runs past the end of the buffer.
                if (used < 0 || used > static_cast<int>(sizeof(text)) - 1) {
                    used = static_cast<int>(sizeof(text)) - 1;
                    break;
                }
            }
            // snprintf answers with how much it *would* have written, not
            // how much it did. Trusting that sends whatever lies past the end
            // of the buffer, which is how this report arrived unprintable.
            if (used < 0) {
                used = 0;
            }
            if (used > static_cast<int>(sizeof(text))) {
                used = static_cast<int>(sizeof(text));
            }
            config.set_link_debug(reinterpret_cast<const std::uint8_t*>(text),
                                  static_cast<std::size_t>(used));
        }
#endif

#if DUO_SPI_DEBUG
        {
            std::uint8_t report[48];
            report[0] = static_cast<std::uint8_t>(link.frames_sent());
            report[1] = static_cast<std::uint8_t>(link.frames_sent() >> 8);
            report[2] = link.status().answered ? 1 : 0;
            report[3] = wire_walk;
            std::memcpy(report + 4, link.last_reply(), 44);
            config.set_link_debug(report, 48);
        }
#endif

        // Active-low against internal pull-ups: a pin pulled to ground is a
        // press, whether that is a button or a wire.
        switch (buttons.update(now_ms, !gpio_get(kPinSw1), !gpio_get(kPinSw2))) {
            case duo_input::u1::ButtonEvent::EmergencyMouseToggle:
                // The one control that has to work when the configuration is
                // wrong, so it does not consult the configuration. The LED is
                // the whole visible effect until there is mouse input to route.
                g_mouse_on_pc2 = !g_mouse_on_pc2;
                break;
            case duo_input::u1::ButtonEvent::StopReleaseAll:
                g_outputs.release_all();
                link.send_release_all(now_ms);
                // The macro that is holding keys down is on the other core,
                // and a stop that leaves it typing is not a stop.
                g_runtime.request_release_all();
                break;
            case duo_input::u1::ButtonEvent::FactoryResetConfirmed:
                // Someone is at the device and held the button for five
                // seconds. Nothing is erased yet - the host still has to ask -
                // but the confirmation is now on record, and it authorises
                // exactly one reset.
                config.confirm_factory_reset();
                break;
            case duo_input::u1::ButtonEvent::None:
                break;
        }

        // Fed last, and only here: everything above has just been serviced.
        watchdog_update();
    }
}
