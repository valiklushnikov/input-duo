// U1 entry point.
//
// At this stage U1 enumerates as its three HID interfaces plus CDC, drives the
// SPI link to U2, and holds nothing. There is no CH375B and no stored
// configuration yet, so nothing ever presses a key - but every later task adds
// input on top of this loop, and none of them get to revisit the invariant
// that a board with no input holds nothing.

#include "pico/multicore.h"
#include "pico/stdlib.h"

#include "tusb.h"

#include <cstdio>
#include <cstring>

#include "hardware/watchdog.h"

#include "buttons.hpp"
#include "ch375/enumerator.hpp"
#include "ch375_probe.hpp"
#include "diagnostics/ch375_baud_scan.hpp"
#include "config_service.hpp"
#include "diagnostics_service.hpp"
#include "hid/state_manager.hpp"
#include "output_runtime.hpp"
#include "pico_flash.hpp"
#include "spi_master.hpp"
#include "storage/ab_store.hpp"
#include "usb_service.hpp"

#if DUO_TEST_PATTERN
#include "test_pattern.hpp"
#endif

namespace {

// Core 0 owns this. Core 1 only ever submits commands to it, so there is
// exactly one writer and no locking between a keypress and a USB report.
duo_input::u1::OutputRuntime g_outputs;

#if DUO_TEST_PATTERN
// Core 1's stand-in until the CH375B exists. Compiled out of a release build:
// a device that can generate its own input must not ship by accident.
void core1_entry() {
    while (true) {
        duo_input::u1::advance_test_pattern(g_outputs,
                                            to_ms_since_boot(get_absolute_time()));
        sleep_ms(1);
    }
}
#endif

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

    duo_input::u1::UsbService usb;
    duo_input::u1::SpiMaster link;
    duo_input::u1::PicoFlash flash;
    duo_input::storage::AbStore store(flash);
    CdcWriter cdc_writer;
    duo_input::u1::ConfigService config(store, cdc_writer);

    // Whatever was stored last time is what the device runs now.
    const duo_input::storage::ScanResult stored = store.scan();
    if (stored.has_active) {
        config.set_active_profile(1);
    }

#if DUO_CH375_PROBE
    // Both ports come up before anything else touches these pins. The chips
    // are asked their four questions here and again every few seconds below,
    // so a controller powered up later is still found.
    static duo_input::u1::ch375::PioCh375Transport keyboard_port;
    static duo_input::u1::ch375::PioCh375Transport mouse_port;
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

    keyboard_port.begin(pio0, duo_input::u1::kPinKeyboardTx, duo_input::u1::kPinKeyboardRx,
                        duo_input::u1::kPinKeyboardInt);
    mouse_port.begin(pio0, duo_input::u1::kPinMouseTx, duo_input::u1::kPinMouseRx,
                     duo_input::u1::kPinMouseInt);
    // Before a single byte goes out, on either port.
    std::uint16_t keyboard_bad_at_boot = 0;
    std::uint16_t mouse_bad_at_boot = 0;
    const std::uint16_t keyboard_quiet_at_boot =
        duo_input::u1::listen_without_sending(keyboard_port, 1000, keyboard_bad_at_boot);
    const std::uint16_t mouse_quiet_at_boot =
        duo_input::u1::listen_without_sending(mouse_port, 1000, mouse_bad_at_boot);

    // The static level readings above say the wiring is sane. They cannot say
    // the two data lines are the right way round, or that a chip is in serial
    // mode - only asking it something can. CHECK_EXIST needs nothing to be
    // configured first: send a byte, get its inverse back (DS1 5.5).
    duo_input::u1::ch375::Ch375Transport keyboard_commands(keyboard_port);
    duo_input::u1::ch375::Ch375Transport mouse_commands(mouse_port);
    // Asked once, here, before the state machines below take the chips over.
    //
    // Running it periodically alongside them made two owners of one chip: the
    // probe sets the working mode and reads statuses, and reading a status is
    // what clears it - so each was consuming the interrupts the other was
    // waiting for. On the bench that looked like a device attaching and
    // detaching twenty-three times in a row.
    const duo_input::u1::Ch375ProbeResult keyboard_probe =
        duo_input::u1::probe_ch375(keyboard_port, keyboard_commands);
    const duo_input::u1::Ch375ProbeResult mouse_probe =
        duo_input::u1::probe_ch375(mouse_port, mouse_commands);

    // The lifecycle itself, on real hardware for the first time: one state
    // machine per controller, sharing nothing, each doing a bounded piece of
    // work per pass.
    duo_input::u1::ch375::AutoSetupEnumerator keyboard_setup(keyboard_commands);
    duo_input::u1::ch375::AutoSetupEnumerator mouse_setup(mouse_commands);
    duo_input::u1::ch375::Ch375Device keyboard_device(keyboard_commands, keyboard_setup);
    duo_input::u1::ch375::Ch375Device mouse_device(mouse_commands, mouse_setup);
    // The experiment that left the bus reset out changed nothing - the device
    // was lost at exactly the same rate without it - so the reset is not what
    // loses it, and the datasheet's sequence is back.

    struct DeviceTally {
        std::uint8_t attached = 0;
        std::uint8_t detached = 0;
        std::uint8_t ready = 0;
        std::uint8_t reports = 0;
        std::uint8_t last_size = 0;
        std::uint8_t last[8] = {};
    };
    DeviceTally keyboard_tally;
    DeviceTally mouse_tally;

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

#if DUO_TEST_PATTERN
    multicore_launch_core1(core1_entry);
#endif

    duo_input::u1::Buttons buttons;
    bool was_mounted = false;

    // Armed only now, with every service in place. It is fed at the end of the
    // loop, after USB, the link and the command queue have all been serviced,
    // so what it actually guarantees is that those keep happening - not merely
    // that some instruction somewhere is still executing.
    watchdog_enable(kWatchdogMs, true);

    while (true) {
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
            link.send_release_all(to_ms_since_boot(get_absolute_time()));
        }

        // Bounded, so a burst of input cannot starve the USB it is for.
        g_outputs.drain();

        usb.publish(g_outputs);

        // PC2's half of the state goes over the link. It sends on change and
        // otherwise heartbeats, so a quiet device does not saturate the bus
        // and does not look severed either.
        const std::uint32_t now_ms = to_ms_since_boot(get_absolute_time());
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
            // Ticked every pass, which is what the state machine is written
            // against. The report below is only a report.
            const std::uint32_t device_now_us = time_us_32();
            duo_input::u1::ch375::Ch375Device* devices[2] = {&keyboard_device, &mouse_device};
            duo_input::u1::ch375::AutoSetupEnumerator* setups[2] = {&keyboard_setup,
                                                                    &mouse_setup};
            DeviceTally* tallies[2] = {&keyboard_tally, &mouse_tally};
            for (int index = 0; index < 2; ++index) {
                devices[index]->tick(device_now_us);
                duo_input::u1::ch375::Ch375Event event;
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
                            const std::size_t keep =
                                event.report_size > sizeof(tally.last) ? sizeof(tally.last)
                                                                       : event.report_size;
                            tally.last_size = static_cast<std::uint8_t>(keep);
                            std::memcpy(tally.last, event.report, keep);
                            break;
                        }
                        default:
                            break;
                    }
                }
            }

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
            char text[480] = {};
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
                    "  setup attempts=%u last=0x%02X (%s)\n",
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
                    describe_setup_status(setups[index]->last_status()));
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
