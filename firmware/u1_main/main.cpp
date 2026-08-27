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

#include <cstring>

#include "hardware/watchdog.h"

#include "buttons.hpp"
#include "ch375_probe.hpp"
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
    // Bring-up only: before anything else claims these pins, find out whether
    // the two CH375s can be reached at all. Every later layer assumes they can.
    // The line is left alone. Once a CH375 is in serial mode its TXD is an
    // output, and holding that line from this end would be two drivers on one
    // wire - which is what the mode jumper on the module is for instead.
    duo_input::u1::Ch375ProbeResult keyboard_probe;
    duo_input::u1::Ch375ProbeResult mouse_probe;
    std::uint32_t last_probe_ms = 0;
    bool probed_once = false;
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
            const std::uint32_t probe_now = to_ms_since_boot(get_absolute_time());
            if (!probed_once || probe_now - last_probe_ms >= 10000) {
                last_probe_ms = probe_now;
                probed_once = true;
                keyboard_probe = duo_input::u1::probe_ch375(duo_input::u1::kPinKeyboardTx,
                                                            duo_input::u1::kPinKeyboardRx,
                                                            duo_input::u1::kPinKeyboardInt);
                mouse_probe = duo_input::u1::probe_ch375(duo_input::u1::kPinMouseTx,
                                                         duo_input::u1::kPinMouseRx,
                                                         duo_input::u1::kPinMouseInt);
            }

            std::uint8_t report[100];
            report[0] = 0;
            const duo_input::u1::Ch375ProbeResult* probes[2] = {&keyboard_probe, &mouse_probe};
            for (int index = 0; index < 2; ++index) {
                const duo_input::u1::Ch375ProbeResult& probe = *probes[index];
                std::uint8_t* at = report + 1 + index * 12;
                at[0] = static_cast<std::uint8_t>((probe.rx_idle_high ? 1 : 0) |
                                                  (probe.rx_floating ? 2 : 0) |
                                                  (probe.int_high ? 4 : 0) |
                                                  (probe.tx_idle_high ? 8 : 0) |
                                                  (probe.tx_floating ? 16 : 0) |
                                                  (probe.rx_settles_high ? 32 : 0));
                at[1] = static_cast<std::uint8_t>((probe.check_exist_answered ? 1 : 0) |
                                                  (probe.tx_wins_when_pushed ? 2 : 0) |
                                                  (probe.rx_wins_when_pushed ? 4 : 0));
                at[2] = probe.check_exist_reply;
                at[3] = probe.ic_version_answered ? 1 : 0;
                at[4] = probe.ic_version;
                at[5] = probe.host_mode_reply;
                at[6] = probe.connect_answered ? 1 : 0;
                at[7] = probe.connect_reply;
                at[8] = static_cast<std::uint8_t>(probe.shortest_pulse_us & 0xFF);
                at[9] = static_cast<std::uint8_t>(probe.shortest_pulse_us >> 8);
                at[10] = static_cast<std::uint8_t>(probe.edges_seen & 0xFF);
                at[11] = static_cast<std::uint8_t>(probe.edges_seen >> 8);

                std::uint8_t* sweep = report + 25 + index * 10;
                for (std::size_t rate = 0; rate < 5; ++rate) {
                    sweep[rate * 2] = probe.sweep_reply[rate];
                    sweep[rate * 2 + 1] = probe.sweep_edges[rate];
                }

                std::uint8_t* trace = report + 45 + index * 26;
                trace[0] = probe.edge_count;
                trace[1] = probe.level_before_first_edge ? 1 : 0;
                for (std::size_t edge = 0; edge < 12; ++edge) {
                    trace[2 + edge * 2] = static_cast<std::uint8_t>(probe.edge_us[edge] & 0xFF);
                    trace[3 + edge * 2] = static_cast<std::uint8_t>(probe.edge_us[edge] >> 8);
                }
            }
            config.set_link_debug(report, sizeof(report));
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
