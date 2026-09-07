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
// work - PC1's USB device and PC2's SPI link both. Nothing crosses between
// them except through the bounded queues: a callback record from the host
// callbacks, and an output command from the runtime. Neither core waits on the
// other.
//
// The link is Core 0's alone. A 64-byte frame occupies 512 us of the bus, and
// Core 1 has a tuh_task() to run inside every millisecond; an SPI transfer
// there would be a host stall and a second writer on a peripheral nothing
// locks.

#include <array>
#include <atomic>
#include <cstdint>

#include "hardware/clocks.h"
#include "pico/multicore.h"
#include "pico/stdlib.h"
#include "pico/time.h"

#include "pio_usb.h"
#include "tusb.h"

extern "C" void reference_service_one_cdc();
extern "C" void reference_drain_one_callback();

#include "callback_queue.hpp"
#include "config_profiles.hpp"
#include "config_service.hpp"
#include "core1_runtime.hpp"
#include "core_bridge.hpp"
#include "host_control_state.hpp"
#include "input/pipeline.hpp"
#include "link_reconnect.hpp"
#include "output_runtime.hpp"
#include "pico_flash.hpp"
#include "source_adapter.hpp"
#include "spi_master.hpp"
#include "usb_service.hpp"

// Pico-PIO-USB's own root-port table. Declared here rather than reached
// through pio_usb_ll.h, for the same reason firmware/u1_main/pio_usb/backend.cpp
// does: that header drags in hardware/pio.h and both generated .pio.h
// programs for four volatile bools this target only reads. root_port_t and
// PIO_USB_ROOT_PORT_CNT come from pio_usb.h above, already included, so this
// is the library's own type and not a second copy of it that could drift. At
// file scope rather than in the anonymous namespace below: a C-linkage name
// cannot also have internal linkage.
extern "C" root_port_t pio_usb_root_port[PIO_USB_ROOT_PORT_CNT];

namespace {

using duo_input::u1::input::DeviceKind;

/// Core 0 owns this. Core 1 only ever submits to it, so there is exactly one
/// writer and no locking between a keypress and a USB report.
duo_input::u1::OutputRuntime g_outputs;

duo_input::u1::UsbService g_usb;

/// PC2, over four wires. Core 0 only - see the note at the top of this file.
duo_input::u1::SpiMaster g_link;

/// Whether U2 has just come back and has to be told to let go.
duo_input::u1::reference::LinkReconnect g_link_reconnect;

/// How often the link reading reaches the trace. Core 0 only.
constexpr std::uint32_t kLinkReportIntervalMs = 1000;
std::uint32_t g_last_link_report_ms = 0;

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

/// The stored configuration, pointed at where it lies in flash.
///
/// StoredProfiles reads bytes it is handed, not flash itself; main() hands it
/// the active slot's bytes once at boot, before Core 1 is launched, and a
/// configuration write hands it a new package through adopt_configuration
/// below. Task 5 is what makes that boot-time load real - see main()'s
/// startup scan.
duo_input::u1::StoredProfiles g_profiles;

duo_input::u1::Core1Runtime g_runtime(g_commands, g_profiles);

// ---------------------------------------------------------------------------
// Configuration, storage and diagnostics - Task 5.
//
// The same config, profile, flash and diagnostics services u1_main links,
// wired the same way and for the same reasons: Core 0 owns the CDC
// conversation and the flash it may write, and Core 1 adopts a configuration
// change through the handoff below rather than having it rewritten
// underneath whatever binding table or macro it is halfway through. See
// core_bridge.hpp's own comment for why that is a handshake and not a
// multicore_lockout.

/// The real flash behind the A/B store. Core 0 only - see PicoFlash's own
/// comment about a core executing from the chip currently being erased, which
/// is why core1_main below arms multicore_lockout_victim_init() before doing
/// anything else.
duo_input::u1::PicoFlash g_flash;
duo_input::storage::AbStore g_store(g_flash);

/// A host that has just enumerated (or just come back after being unplugged)
/// knows nothing about what PC1 was holding before. This is what tells it:
/// the same release_pc1() u1_main's main.cpp builds on the mounted-state
/// transition.
duo_input::runtime::OutputCommand release_pc1() {
    duo_input::runtime::OutputCommand command;
    command.kind = duo_input::runtime::CommandKind::ReleaseRoute;
    command.route = duo_input::runtime::Route::Pc1;
    return command;
}

/// Sends the configurator's replies back down the CDC pipe GET_DIAGNOSTICS and
/// friends travel over.
///
/// A second, independent wrapper around the same tud_cdc_write the reference
/// target's own trace writer already uses (host_callbacks.cpp's
/// TinyUsbCdcWriter, which serves reference_service_one_cdc's descriptor and
/// report trace lines) - two objects serving two different queues of bytes
/// out of the one physical CDC endpoint, exactly as u1_main's CdcWriter does
/// beside its own trace-free CDC use.
class CdcWriter final : public duo_input::u1::CdcSink {
public:
    void write(const std::uint8_t* data, std::size_t size) override {
        // Written whether or not the host has raised DTR - see u1_main's
        // CdcWriter for why: a host that never sets the line still has to be
        // answered, and QSerialPort does not raise it on open.
        tud_cdc_write(data, static_cast<std::uint32_t>(size));
        tud_cdc_write_flush();
    }
};

CdcWriter g_cdc_writer;

/// Where Core 0 leaves a configuration for Core 1 to pick up, and Core 1
/// leaves the answer.
duo_input::u1::ConfigHandoff g_config_handoff;

/// Install a profile's macros, indexed by the slot a binding names.
///
/// Runs on Core 1, mirroring u1_main's own install_macros exactly: the
/// definitions point into the step pool inside StoredProfiles, which this
/// rewrites, and nothing may be mid-macro reading what it replaces while this
/// runs - see adopt_configuration below, the only caller.
void install_macros(std::uint8_t profile) {
    // Static: Core 1's stack is small and the binding table sits below this
    // on the same path, the same reason u1_main's own copy is static.
    static duo_input::u1::macros::MacroDefinition
        definitions[duo_input::u1::kMaxProfileMacros];
    g_profiles.macros_for(profile, definitions, duo_input::u1::kMaxProfileMacros);
    for (std::size_t slot = 0; slot < duo_input::u1::kMaxProfileMacros; ++slot) {
        g_runtime.define_macro(static_cast<std::uint8_t>(slot), definitions[slot]);
    }
}

/// Switch every flash-backed runtime view. Runs on Core 1.
///
/// Let go of everything first, exactly as u1_main's adopt_configuration does
/// and for the same reason: what was held was held under the old
/// configuration's meaning, and release_all is what stops and drains the
/// scheduler before the step pool underneath it is safe to rewrite. This is
/// also this target's half of "a configuration write sends release-all
/// before the flash lockout": g_runtime.release_all() runs here, queuing the
/// release for Core 0 to apply, and Core 0's own loop sends
/// g_link.send_release_all() the same pass it drains that queue - the same
/// pair firmware/u1_main/main.cpp uses on its own release-all request path.
bool adopt_configuration(duo_input::protocol::ByteView package) {
    g_runtime.release_all();
    const bool loaded = g_profiles.load(package);
    // A package that is not a configuration leaves StoredProfiles empty,
    // which is also what a factory reset asks for. Profile zero is what an
    // empty configuration answers to.
    const std::uint8_t profile = loaded ? g_profiles.active_profile_id() : 0;
    g_runtime.set_profile_now(profile);
    install_macros(profile);
    return loaded;
}

/// How long Core 0 waits for Core 1 to adopt a configuration. Same value and
/// the same reasoning as u1_main's kConfigHandoffTimeoutMs: orders of
/// magnitude longer than a pass round Core 1, still far short of any
/// watchdog this target might grow later.
constexpr std::uint32_t kConfigHandoffTimeoutMs = 250;

/// Hand a configuration to Core 1 and wait to be told it was adopted.
///
/// Returns false only when Core 1 never answered. Whether the package was a
/// configuration comes back in ``loaded``.
bool hand_configuration_to_core1(duo_input::protocol::ByteView package, bool& loaded) {
    if (!duo_input::u1::core1_running()) {
        // Nothing else is executing yet, so there is nobody to hand it to and
        // nothing to race with - and nobody to answer a handshake either.
        loaded = adopt_configuration(package);
        return true;
    }

    const std::uint32_t ticket = g_config_handoff.post(package);
    const absolute_time_t deadline = make_timeout_time_ms(kConfigHandoffTimeoutMs);
    while (!g_config_handoff.finished(ticket)) {
        if (time_reached(deadline)) {
            return false;
        }
        tight_loop_contents();
    }
    loaded = g_config_handoff.succeeded();
    return true;
}

/// Switch every flash-backed runtime view, from Core 0's side. Mirrors
/// u1_main's own RuntimeConfig exactly - see its comment for why this asks
/// Core 1 and waits rather than stopping it and doing the work itself.
class RuntimeConfig final : public duo_input::u1::IRuntimeConfig {
public:
    bool activate(duo_input::protocol::ByteView package) override {
        bool loaded = false;
        if (!hand_configuration_to_core1(package, loaded)) {
            return false;
        }
        return loaded;
    }

    bool clear() override {
        // Nothing to load: the point is that Core 1 stops reading the slots
        // that are about to be erased.
        bool loaded = false;
        return hand_configuration_to_core1(duo_input::protocol::ByteView{nullptr, 0}, loaded);
    }
};

RuntimeConfig g_runtime_config;

duo_input::u1::ConfigService g_config(g_store, g_cdc_writer, g_runtime_config);

// ---------------------------------------------------------------------------
// The host stack's own base reading.
//
// GET_DIAGNOSTICS' host-observation block existed, before this target linked
// ConfigService, only for the shipping PIO USB backend - and that backend's
// DeviceRegistry is the one thing this rebuild does not import (see the
// CMakeLists.txt comment: "the current pio_usb/backend.cpp"). Publishing
// nothing here at all would make this the one image that demonstrably runs a
// live TinyUSB/PIO host on Core 1 and tells the configurator it has none -
// exactly the reading an operator needs when nothing enumerates. Publishing
// the shipping backend's own struct with everything past the base zeroed
// would be worse: an invented "0 mount events" beside a real "3554 SOF
// frames" is not a fact about this board.
//
// So this target reads only what pio_usb itself already exposes - its root
// port and its free-running frame counter, both read directly rather than
// through DeviceRegistry - and declares the base shape
// (kHostObservationBaseBytes) rather than the full one. A configurator that
// knows only the old shape reads exactly what it always read; one that knows
// the full shape reads the fields this build measured and nothing past them.

/// What core1_main's own tuh_configure()/tuh_init() calls answered, and
/// whether the host stack was already active before they ran - captured once,
/// at the moment those calls are made, because a call made later would be
/// reading TinyUSB's memory of its own history rather than watching it
/// happen. See firmware/u1_main/pio_usb/backend.cpp's PioUsbBackend::begin()
/// for the same reading, taken the same way, for the same reason.
///
/// Written by Core 1 once, before its service loop starts, and never again;
/// read by Core 0 every pass. No atomics: a write that happens once, long
/// before anything reads it, and never again has no later writer for a
/// fence to order against - unlike the two counters below, which really do
/// change under a concurrent reader.
struct HostInitReading {
    bool ready = false;
    std::uint8_t init_flags = 0;
    std::uint32_t clk_hz_at_begin = 0;
};
HostInitReading g_host_init;

/// Passes of Core 1's loop, and root-port connect edges seen while polling
/// it - the two readings that genuinely accumulate under a concurrent
/// reader, so unlike g_host_init above they are atomic. Incremented only by
/// Core 1 (core1_main's loop), read only by Core 0 (main's loop) - the same
/// single-writer/single-reader shape reference_overflows() already uses
/// across this same core boundary.
std::atomic<std::uint32_t> g_core1_passes{0};
std::atomic<std::uint32_t> g_root_port_connects{0};

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

using duo_input::u1::reference::poison_descriptor_buffer;
using duo_input::u1::reference::ReferenceSourceAdapter;
ReferenceSourceAdapter g_adapter;

// tuh_descriptor_get_hid_report is asynchronous. TinyUSB retains the buffer
// pointer until its completion callback, so stack storage would be a use after
// return. Only one targeted experiment can be active, hence one fixed buffer.
static std::array<std::uint8_t, kReferenceDescriptorCapacity>
    g_post_mount_descriptor{};

duo_input::u1::reference::DescriptorDiagnosticCoordinator
    g_descriptor_diagnostic(g_adapter);

void post_mount_descriptor_complete(tuh_xfer_t* xfer) {
    if (xfer == nullptr || !g_descriptor_diagnostic.active()) {
        return;
    }

    const std::uint8_t dev_addr = g_descriptor_diagnostic.dev_addr();
    const std::uint8_t instance = g_descriptor_diagnostic.instance();
    g_descriptor_diagnostic.complete(
        xfer->daddr, xfer->result == XFER_RESULT_SUCCESS, xfer->actual_len,
        tuh_hid_mounted(dev_addr, instance), g_post_mount_descriptor.data(),
        g_post_mount_descriptor.size(), xfer->user_data);
}

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
    if (g_descriptor_diagnostic.active()) {
        g_descriptor_diagnostic.abandon_if_unmounted(tuh_hid_mounted(
            g_descriptor_diagnostic.dev_addr(),
            g_descriptor_diagnostic.instance()));
    }

    // A measurement that gave up before reaching the wire has to say so. The
    // offer budget drains in about a second, and an experiment that ends in
    // silence cannot be told apart from a board that was never flashed.
    ReferenceDescriptorReason giveup_reason = ReferenceDescriptorReason::None;
    ReferenceSourceAdapter::DescriptorRequest giveup_request{};
    if (g_adapter.take_descriptor_giveup(giveup_reason, giveup_request)) {
        g_descriptor_diagnostic.skipped(giveup_reason, giveup_request.dev_addr,
                                        giveup_request.instance,
                                        giveup_request.length);
    }

    // The second measurement. One boot has to yield both the one-packet read
    // and the whole-document read, or the two answers describe two different
    // device states and neither explains the other. take_completed() is one
    // shot and the adapter arms the follow-up once, so this cannot turn into
    // a third experiment.
    if (g_descriptor_diagnostic.take_completed()) {
        g_adapter.schedule_descriptor_followup(time_us_32());
    }

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
    // TinyUSB has a single control transfer in flight at a time, so this call
    // is refused while the other interfaces of the same device are still being
    // set up. Refusing is not failing: the request is held and offered again
    // next pass rather than dropped, because dropping it leaves the interface
    // in boot protocol while the layout describes report protocol - which is
    // silently no input at all.
    static duo_input::u1::reference::ProtocolRequestHold protocol_request_held;
    if (!protocol_request_held.active()) {
        ReferenceSourceAdapter::ProtocolRequest next{};
        if (g_adapter.take_protocol_request(next)) {
            protocol_request_held.hold(next);
        }
    }
    if (protocol_request_held.active()) {
        const ReferenceSourceAdapter::ProtocolRequest protocol_request =
            protocol_request_held.request();
        const auto action = protocol_request_held.action(tuh_hid_mounted(
            protocol_request.dev_addr, protocol_request.instance));
        if (action == duo_input::u1::reference::ProtocolRequestHold::Action::Offer) {
            if (tuh_hid_set_protocol(protocol_request.dev_addr,
                                     protocol_request.instance,
                                     protocol_request.protocol)) {
                protocol_request_held.accepted();
            }
            return;
        }
        // Dropped means the interface vanished. Do not return: its queued
        // UMOUNT must be consumed below rather than hidden forever by stale
        // control work.
    }

    ReferenceSourceAdapter::DescriptorRequest descriptor_request{};
    if (!g_descriptor_diagnostic.active() &&
        g_adapter.take_descriptor_request(time_us_32(), descriptor_request)) {
        // Both give-ups below used to consume an offer and return with
        // nothing printed, which drained the budget in silence.
        tuh_itf_info_t info{};
        if (!tuh_hid_mounted(descriptor_request.dev_addr,
                             descriptor_request.instance)) {
            g_adapter.abandon_descriptor_request(
                ReferenceDescriptorReason::Unmounted, descriptor_request);
            return;
        }
        if (!tuh_hid_itf_get_info(descriptor_request.dev_addr,
                                  descriptor_request.instance, &info)) {
            g_adapter.abandon_descriptor_request(
                ReferenceDescriptorReason::NoInterface, descriptor_request);
            return;
        }

        g_descriptor_diagnostic.start(descriptor_request.dev_addr,
                                      descriptor_request.instance,
                                      descriptor_request.length);
        // The public descriptor API takes bInterfaceNumber. The callback gives
        // us TinyUSB's global HID instance/index; they are not interchangeable
        // (the Aula callbacks use instances 3/4 while its interface numbers
        // are 0/1).
        //
        // Poison immediately before the attempt, with nothing in between. The
        // buffer is static and reused, so a transfer that reports a length
        // while writing nothing would otherwise compare clean against the
        // previous attempt's bytes and print MATCH.
        poison_descriptor_buffer(g_post_mount_descriptor);
        const bool accepted = tuh_descriptor_get_hid_report(
            descriptor_request.dev_addr, info.desc.bInterfaceNumber,
            HID_DESC_TYPE_REPORT, 0, g_post_mount_descriptor.data(),
            descriptor_request.length, post_mount_descriptor_complete,
            g_descriptor_diagnostic.lifetime_token());
        if (accepted) {
            g_descriptor_diagnostic.request_accepted();
        } else {
            g_descriptor_diagnostic.refused();
        }
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

extern "C" bool reference_descriptor_unmounted(std::uint8_t dev_addr,
                                                std::uint8_t instance,
                                                std::uint32_t now_us) {
    return g_descriptor_diagnostic.capture_unmount(dev_addr, instance, now_us);
}

// core1: the USB host, and everything that reads what it produced
extern "C" void core1_main() {
    // Core 0 erases and programs flash, and it cannot do that while this core
    // might be fetching instructions from the chip being erased. This is what
    // lets it stop us; without it a flash write would wait forever.
    //
    // Announced from here rather than from Core 0, and only after arming -
    // the same reason u1_main's core1_entry announces it here: between
    // launching a core and that core arming itself there is a window where it
    // is running from flash and cannot yet be stopped, and a write landing in
    // it would be a request that never returns. Before anything else below
    // touches tuh_task or a clock this target does not own.
    multicore_lockout_victim_init();
    duo_input::u1::set_core1_running(true);

    // Before anything below changes a clock or touches the host stack:
    // whether it was already active, and the clock it is about to compute
    // its PIO dividers against. Read here and nowhere later, for the same
    // reason PioUsbBackend::begin() reads them first - a call made after
    // tuh_init cannot tell "this call started the host" from "tuh_init
    // returned true for an rhport something else already activated".
    std::uint8_t host_init_flags = tuh_rhport_is_active(1) ? (1u << 0) : 0;
    g_host_init.clk_hz_at_begin = clock_get_hz(clk_sys);

    sleep_ms(10);

    // Use tuh_configure() to pass pio configuration to the host stack
    // Note: tuh_configure() must be called before
    pio_usb_configuration_t pio_cfg = PIO_USB_DEFAULT_CONFIG;
    const bool pio_configured =
        tuh_configure(1, TUH_CFGID_RPI_PIO_USB_CONFIGURATION, &pio_cfg);

    // To run USB SOF interrupt in core1, init host stack for pio_usb (roothub
    // port1) on core1
    const bool tuh_initialized = tuh_init(1);

    // Recorded whatever they said, including "true" - see the comment above:
    // a true from tuh_init on an rhport somebody else already activated is
    // not evidence that this call did anything.
    if (pio_configured) {
        host_init_flags |= 1u << 1;
    }
    if (tuh_initialized) {
        host_init_flags |= 1u << 2;
    }
    if (tuh_inited()) {
        host_init_flags |= 1u << 3;
    }
    g_host_init.init_flags = host_init_flags;
    g_host_init.ready = true;

    // Whatever main()'s boot-time flash scan already loaded into g_profiles
    // is what this core starts running - the bindings and macros a
    // configuration write only ever changes from here on, through
    // adopt_configuration below.
    std::uint8_t installed_profile = g_profiles.active_profile_id();
    g_runtime.set_profile_now(installed_profile);
    install_macros(installed_profile);

    while (true) {
        // First thing in the pass, between one whole turn and the next: not
        // inside a binding table, not inside the macro definitions, not
        // holding an event half-processed. Mirrors u1_main's core1_entry -
        // see its comment for why that placement is the one that matters.
        {
            duo_input::protocol::ByteView package{nullptr, 0};
            if (g_config_handoff.take(package)) {
                g_config_handoff.complete(adopt_configuration(package));
                installed_profile = g_runtime.active_profile();
            }
        }

        tuh_task();  // tinyusb host task

        // Passes of this loop, and the root port's own connect edge - the
        // two host-observation readings that genuinely accumulate, so
        // unlike g_host_init they are counted here every pass rather than
        // captured once. Saturating, the same as every other counter on
        // this path: a u32 that wrapped back to a value it already showed
        // would read as a stopped Core 1 to the exact procedure that exists
        // to detect one.
        if (g_core1_passes.load(std::memory_order_relaxed) != 0xFFFFFFFFu) {
            g_core1_passes.fetch_add(1, std::memory_order_relaxed);
        }
        {
            static bool was_connected = false;
            const bool connected = pio_usb_root_port[0].connected;
            if (connected && !was_connected &&
                g_root_port_connects.load(std::memory_order_relaxed) != 0xFFFFFFFFu) {
                g_root_port_connects.fetch_add(1, std::memory_order_relaxed);
            }
            was_connected = connected;
        }

        // Refused captures are input this firmware did not keep, and have to
        // reach the trace rather than only a counter.
        reference_drain_one_callback();

        const std::uint32_t millis = now_ms();
        service_input(millis);
        g_runtime.tick(millis);

        // A swap happened - a binding, a macro, or the host asked for one.
        // The bindings moved with it and the macros have to follow, exactly
        // as u1_main's core1_entry keeps its own installed_profile current.
        if (g_runtime.active_profile() != installed_profile) {
            installed_profile = g_runtime.active_profile();
            install_macros(installed_profile);
        }
    }
}

// core0: the USB device, and the output it publishes
int main() {
    // default 125MHz is not appropreate. Sysclock should be multiple of 12MHz.
    set_sys_clock_khz(120000, true);

    sleep_ms(10);

    // Whatever was stored last time is what this target runs now.
    //
    // Read before Core 1 is launched, exactly as u1_main does: Core 1 reads
    // the bindings out of g_profiles the moment it starts. The bytes are not
    // copied - they are pointed at where they lie in flash, which outlives
    // everything that reads them.
    const duo_input::storage::ScanResult stored = g_store.scan();
    if (stored.has_active) {
        const duo_input::protocol::ByteView package =
            g_store.payload_view(stored.active, stored.active_slot().size);
        if (package.data != nullptr && g_profiles.load(package)) {
            g_config.set_initial_active_profile(g_profiles.active_profile_id());
        }
    }

    multicore_reset_core1();
    // all USB host task run in core1
    multicore_launch_core1(core1_main);

    // init device stack on native usb (roothub port0)
    g_usb.begin();

    // After the device stack, and after set_sys_clock_khz above: begin()
    // computes the PL022 prescalers against clk_peri as it is at that moment,
    // and nothing here moves the clock again. This target has no pio_usb
    // backend reparenting clk_peri underneath it, so one begin() is enough and
    // the old clock handoff barrier is deliberately not recreated.
    g_link.begin();

    // A host that has just enumerated (or just vanished) knows nothing about
    // what was held before, or is watching a device that stopped answering.
    // Tracked here, mirroring u1_main's own was_mounted, so the transition is
    // caught exactly once per change rather than every pass.
    bool was_mounted = false;

    while (true) {
        const std::uint32_t millis = now_ms();

        g_usb.task();

        const bool mounted = g_usb.mounted();
        if (mounted != was_mounted) {
            // A host that has just enumerated knows nothing about the reports
            // sent before, and anything held while unplugged was never
            // released as far as it is concerned. Start from nothing - the
            // same release_pc1/forget_sent_state pair u1_main's main loop
            // uses on this exact transition.
            g_outputs.process(release_pc1());
            g_usb.forget_sent_state();
            if (!mounted) {
                // The host went away. Anything it had staged is abandoned.
                g_config.on_disconnect();
            }
            was_mounted = mounted;
        }

        // The configurator's side of the conversation.
        if (tud_cdc_available()) {
            std::uint8_t incoming[64];
            const std::uint32_t read = tud_cdc_read(incoming, sizeof(incoming));
            g_config.on_cdc_bytes(incoming, read);
        }
        if (g_config.take_release_all_request()) {
            g_outputs.release_all();
            g_link.send_release_all(millis);
            // Asked for rather than done here: the command queue has exactly
            // one producer and this core is not it.
            g_runtime.request_release_all();
        }

        // What the host asked of Core 1, and what Core 1 has to say back -
        // the capture and profile handshakes and the dropped-command count.
        // In core_bridge.cpp rather than here, and tested there.
        duo_input::u1::pump_core_bridge(g_config, g_runtime);

        // The trace and the configurator's own replies share this one
        // physical CDC endpoint (see g_cdc_writer above and
        // host_callbacks.cpp's TinyUsbCdcWriter). A COBS decoder finds its
        // frame boundary at the next zero byte regardless of what sits
        // between two replies, so one trace line landing mid-session turns
        // the configurator's next frame into "malformed COBS frame" - proven
        // on real hardware, not by inference, the first time a configurator
        // actually talked to this target. So the trace stops the instant
        // on_cdc_bytes above has decoded one real frame
        // (conversation_active()) and only resumes once the host has
        // genuinely gone (on_disconnect(), called from the mount transition
        // above) - never merely because the configurator app went idle
        // between requests, which this device cannot reliably tell apart
        // from "still connected": QSerialPort does not raise DTR on open,
        // the same fact CdcWriter's own comment already relies on.
        if (!g_config.conversation_active()) {
            reference_service_one_cdc();
        }

        g_outputs.drain(millis, time_us_32());
        g_usb.publish(g_outputs);

        // U2 releases everything after 100 ms of silence; U1 keeps no such
        // clock and the link sends on change, so a link that has come back
        // disagrees with this side about what PC2 is holding and nothing
        // afterwards corrects it. Once per reconnection, before the poll that
        // then re-sends the state send_release_all just invalidated.
        if (g_link_reconnect.should_release(g_link.status().answered)) {
            g_link.send_release_all(millis);
        }

        // PC2's half of the state. It sends on change and otherwise
        // heartbeats, so a quiet device neither saturates the bus nor looks
        // severed.
        g_link.poll(millis, g_outputs);

        // Published every pass, so GET_DIAGNOSTICS can see the link rather
        // than infer it from an absence of errors - the same fields the LINK
        // trace line below is built from, fed to the CDC reply instead of the
        // trace queue.
        {
            duo_input::u1::LinkState state;
            state.answered = g_link.status().answered;
            state.mounted = g_link.status().mounted;
            state.frames_sent = g_link.frames_sent();
            state.crc_errors = g_link.status().crc_errors;
            state.echoed_frames = g_link.status().echoed_frames;
            state.endpoint_drops = g_link.status().endpoint_drops;
            state.endpoint_release_ms = g_link.status().endpoint_release_ms;
            g_config.set_link_state(state);
        }

        // Published every pass for the same reason: dropped_commands says
        // input was lost at some point, runtime_fault says the queue is
        // overflowing right now.
        g_config.set_dropped_commands(g_runtime.dropped_commands());
        g_config.set_runtime_fault(g_outputs.fault());
        g_config.set_input_latency(g_outputs.keyboard_latency(), g_outputs.mouse_latency());

        // Which host stack read the two roles, and this target's own
        // counters behind it. PIO_USB_REFERENCE names this target apart from
        // the shipping PIO_USB backend, because the two do not share a
        // DeviceRegistry and a diagnostic that named them the same would
        // claim counters this target never measures. The four fields behind
        // it are what closes the two gaps Task 3 and Task 4 left open: the
        // callback queue's overflow count was never read on hardware, and
        // whether a role is ready is what makes a route selected by a freshly
        // loaded profile something an operator can tell apart from one that
        // never took effect.
        g_config.set_backend(duo_input::protocol::InputBackend::PIO_USB_REFERENCE);
        {
            duo_input::u1::ReferenceCounters counters;
            counters.callback_overflows = reference_overflows();
            counters.ignored_interfaces = g_adapter.ignored_interface_count();
            counters.keyboard_ready = g_adapter.keyboard_ready();
            counters.mouse_ready = g_adapter.mouse_ready();
            g_config.set_reference_counters(counters);
        }

        // What the host stack itself is doing, below every counter above -
        // see the comment beside HostInitReading for why this target
        // publishes the base reading rather than nothing at all or the
        // shipping backend's full struct. g_host_init.ready is false only
        // before Core 1 has made its one capture, at the very start of
        // core1_main - a window measured in microseconds, not passes.
        if (g_host_init.ready) {
            duo_input::u1::HostObservation observation;
            observation.init_flags = g_host_init.init_flags;
            observation.clk_hz_at_begin = g_host_init.clk_hz_at_begin;
            observation.clk_hz_now = clock_get_hz(clk_sys);
            observation.sof_frame_count = pio_usb_host_get_frame_number();
            const root_port_t& root = pio_usb_root_port[0];
            std::uint8_t root_state = 0;
            if (root.initialized) {
                root_state |= 1u << 0;
            }
            if (root.connected) {
                root_state |= 1u << 1;
            }
            if (root.suspended) {
                root_state |= 1u << 2;
            }
            if (root.is_fullspeed) {
                root_state |= 1u << 3;
            }
            observation.root_port_state = root_state;
            observation.root_port_connects = static_cast<std::uint16_t>(
                g_root_port_connects.load(std::memory_order_relaxed));
            observation.core1_passes = g_core1_passes.load(std::memory_order_relaxed);
            g_config.set_host_observation_base(observation);
        }

        // And what the link had to say, once a second. Often enough to watch
        // U2 come and go while somebody pulls a cable, rare enough that it
        // cannot bury the report trace. Unsigned arithmetic, so the 49-day
        // wrap costs at most one late line.
        if (millis - g_last_link_report_ms >= kLinkReportIntervalMs) {
            g_last_link_report_ms = millis;
            ReferenceLinkStatus status;
            status.answered = g_link.status().answered;
            status.frames_sent = g_link.frames_sent();
            status.crc_errors = g_link.status().crc_errors;
            status.echoed_frames = g_link.status().echoed_frames;
            status.endpoint_drops = g_link.status().endpoint_drops;
            status.endpoint_release_ms = g_link.status().endpoint_release_ms;
            reference_link_status_publish(status);
        }
    }

    return 0;
}
