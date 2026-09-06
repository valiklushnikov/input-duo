/*
 * Derived from Pico-PIO-USB's host_hid_to_device_cdc example (MIT, 2019
 * Ha Thach / sekigon-gonnoc). The host lifecycle below is that example's,
 * unchanged and deliberately so; what has been added is everything between a
 * report arriving and a key reaching PC1.
 */

// U1 reference target: the upstream host lifecycle, with Duo Input's own input
// path on top of it.
//
// The order in main() and core1_main() is the whole point of this target and
// must not drift: the system clock is final before any peripheral exists, Core
// 1 is launched before the host stack, tuh_init runs *on* Core 1 so the SOF
// interrupt lives there, and only the device stack comes up on Core 0. Getting
// that wrong is what this migration has already paid for once - see
// UsbService::begin()'s comment about tusb_init() quietly starting both
// stacks.
//
// Core 1 does host work and input work. Core 0 does device work and output
// work. Nothing crosses between them except through the bounded queues: a
// callback record from the host callbacks, and an output command from the
// runtime. Neither core waits on the other.

#include <cstdint>

#include "hardware/clocks.h"
#include "pico/multicore.h"
#include "pico/stdlib.h"
#include "pico/time.h"

#include "pio_usb.h"
#include "tusb.h"

extern "C" void reference_print_one_trace();
extern "C" void reference_drain_one_callback();

#include "callback_queue.hpp"
#include "config_profiles.hpp"
#include "core1_runtime.hpp"
#include "input/pipeline.hpp"
#include "output_runtime.hpp"
#include "source_adapter.hpp"
#include "usb_service.hpp"

namespace {

using duo_input::u1::input::DeviceKind;

/// Core 0 owns this. Core 1 only ever submits to it, so there is exactly one
/// writer and no locking between a keypress and a USB report.
duo_input::u1::OutputRuntime g_outputs;

duo_input::u1::UsbService g_usb;

/// Core 1's only reach into the output: the queue, and nothing else. A full
/// queue is refused rather than waited on, because Core 1 cannot block on
/// Core 0.
class QueuedCommands final : public duo_input::u1::ICommandSink {
public:
    bool submit(const duo_input::runtime::OutputCommand& command) override {
        return g_outputs.submit(command);
    }
    std::size_t pending() const override { return g_outputs.pending(); }
};

QueuedCommands g_commands;

/// Deliberately never loaded on this target.
///
/// StoredProfiles reads bytes it is handed, not flash; leaving it unloaded is
/// how this slice gets the real profile source without the flash, config and
/// A/B store that Task 5 admits. An unloaded source answers "no such profile",
/// which leaves the runtime in its default routes - PC1, which is all this
/// slice routes to.
duo_input::u1::StoredProfiles g_profiles;

duo_input::u1::Core1Runtime g_runtime(g_commands, g_profiles);

/// Where a normalized event goes.
class RuntimeInput final : public duo_input::u1::input::IInputHandler {
public:
    void on_input(const duo_input::u1::input::InputEvent& event,
                  std::uint32_t now_ms) override {
        g_runtime.handle_input(event, now_ms);
    }
};

RuntimeInput g_input;

/// One pipeline per logical device, addressed by the adapter's source_id.
duo_input::u1::input::InputPipeline g_keyboard_pipeline(g_input);
duo_input::u1::input::InputPipeline g_mouse_pipeline(g_input);

using duo_input::u1::reference::ReferenceSourceAdapter;
ReferenceSourceAdapter g_adapter;

std::uint32_t now_ms() {
    return to_ms_since_boot(get_absolute_time());
}

/// Move one captured record into the adapter, and one of its events into the
/// pipeline it is addressed to.
///
/// At most one of each per pass: a burst must never turn a service loop into a
/// long one, and the host stack is what pays for a long one. The adapter is
/// drained before it is fed, so nothing it produced is ever dropped for want
/// of somewhere to put it.
void service_input(std::uint32_t millis) {
    duo_input::u1::input::SourceEvent event{};
    duo_input::u1::input::SourceIdentity identity{};
    if (g_adapter.take_event(event, identity)) {
        auto* pipeline =
            (event.source_id ==
             g_adapter.logical_port(DeviceKind::Keyboard))
                ? &g_keyboard_pipeline
                : &g_mouse_pipeline;
        pipeline->on_event(event, identity, millis);
        return;
    }

    // An interface whose layout came from its report descriptor has to be
    // moved into report protocol before that layout describes anything: TinyUSB
    // starts boot-capable interfaces in boot protocol, whose mouse report is
    // three bytes with no Report ID. This is the only place that call can be
    // made - the adapter is transport neutral and tested without TinyUSB.
    ReferenceSourceAdapter::ProtocolRequest protocol_request{};
    if (g_adapter.take_protocol_request(protocol_request)) {
        tuh_hid_set_protocol(protocol_request.dev_addr, protocol_request.instance,
                             protocol_request.protocol);
        return;
    }

    ReferenceCallbackRecord record{};
    if (reference_take(record)) {
        // The input path is the queue's only consumer now. The trace is a
        // by-product of that one take, never a second one: two consumers would
        // mean a report that reached the diagnostics instead of the keyboard.
        reference_trace_push(reference_trace_from(record));
        g_adapter.consume(record, time_us_32());
    }
}

}  // namespace

// core1: the USB host, and everything that reads what it produced
extern "C" void core1_main() {
    sleep_ms(10);

    // Use tuh_configure() to pass pio configuration to the host stack
    // Note: tuh_configure() must be called before
    pio_usb_configuration_t pio_cfg = PIO_USB_DEFAULT_CONFIG;
    tuh_configure(1, TUH_CFGID_RPI_PIO_USB_CONFIGURATION, &pio_cfg);

    // To run USB SOF interrupt in core1, init host stack for pio_usb (roothub
    // port1) on core1
    tuh_init(1);

    while (true) {
        tuh_task();  // tinyusb host task

        // Refused captures are input this firmware did not keep, and have to
        // reach the trace rather than only a counter.
        reference_drain_one_callback();

        const std::uint32_t millis = now_ms();
        service_input(millis);
        g_runtime.tick(millis);
    }
}

// core0: the USB device, and the output it publishes
int main() {
    // default 125MHz is not appropreate. Sysclock should be multiple of 12MHz.
    set_sys_clock_khz(120000, true);

    sleep_ms(10);

    multicore_reset_core1();
    // all USB host task run in core1
    multicore_launch_core1(core1_main);

    // init device stack on native usb (roothub port0)
    g_usb.begin();

    while (true) {
        g_usb.task();
        reference_print_one_trace();

        const std::uint32_t millis = now_ms();
        g_outputs.drain(millis, time_us_32());
        g_usb.publish(g_outputs);
    }

    return 0;
}
