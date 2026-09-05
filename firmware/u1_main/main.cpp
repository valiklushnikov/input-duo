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

#ifdef DUO_INPUT_BACKEND_PIO_USB
#include "hardware/clocks.h"
#endif
#include "hardware/watchdog.h"

#include "buttons.hpp"
#ifdef DUO_INPUT_BACKEND_CH375
#include "ch375/descriptor_setup.hpp"
#include "ch375_probe.hpp"
#endif
#include "config_profiles.hpp"
#include "config_service.hpp"
#include "core1_runtime.hpp"
#include "core_bridge.hpp"
#ifdef DUO_INPUT_BACKEND_CH375
#include "diagnostics/ch375_baud_scan.hpp"
#endif
#include "diagnostics_service.hpp"
#include "hid/state_manager.hpp"
#ifdef DUO_INPUT_BACKEND_CH375
#include "input/ch375_source_adapter.hpp"
#else
#include "pio_usb/backend.hpp"
#endif
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
// At namespace scope rather than in main's frame so the diagnostic reply can
// read its counters. Same lifetime either way - it outlives every call.
duo_input::u1::UsbService usb;

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

    /// What Core 0 has not taken yet. Read, never written.
    ///
    /// Core 1 paces its macro output against this. Core 0 holds output state
    /// and not a queue of reports, so a press and its release applied in one
    /// drain leave the state as it was and no report is sent at all - a macro
    /// that outran the drain would type nothing on the far computer.
    std::size_t pending() const override { return g_outputs.pending(); }
};

QueuedCommands g_commands;

/// The stored configuration, pointed at where it lies in flash.
duo_input::u1::StoredProfiles g_profiles;

/// Everything between a peripheral report and a queued command.
duo_input::u1::Core1Runtime g_runtime(g_commands, g_profiles);

#ifdef DUO_INPUT_BACKEND_CH375
/// What one peripheral port has on it, as the host has to see it.
///
/// Read from the two objects that already know - the controller's state machine
/// and the enumeration that configured whatever it found - rather than kept as
/// a third copy that could disagree with either.
duo_input::u1::PeripheralPort describe_port(
    const duo_input::u1::ch375::Ch375Device& device,
    const duo_input::u1::ch375::DescriptorSetup& setup) {
    duo_input::u1::PeripheralPort port;
    const duo_input::u1::ch375::Ch375State state = device.state();
    port.attached = state != duo_input::u1::ch375::Ch375State::Absent;
    port.ready = state == duo_input::u1::ch375::Ch375State::Ready;
    port.kind = static_cast<std::uint8_t>(setup.kind());
    port.vendor_id = setup.vendor_id();
    port.product_id = setup.product_id();
    // Only from a descriptor the device actually gave up. A boot-protocol
    // mouse is read under an assumed three-button layout, and reporting that
    // assumption as the device's own declaration would put a number in the
    // matrix that the peripheral never said.
    port.buttons = setup.has_mouse_layout()
                       ? static_cast<std::uint8_t>(setup.mouse_layout().buttons.bits)
                       : 0;
    port.report_descriptor_bytes = setup.report_descriptor_bytes();
    std::memcpy(port.descriptor_hash, setup.report_descriptor_hash(),
                sizeof(port.descriptor_hash));
    return port;
}
#else
/// What one logical role slot has on it, as the host has to see it.
///
/// The PIO USB backend has one bus and no channels, so a "port" here is a
/// logical role slot - the one keyboard and the one mouse V1 accepts - and the
/// registry's own role owner is the device in it. Read straight from the
/// registry rather than kept as a second copy that could disagree with it,
/// exactly as the CH375 branch above reads its controller and enumeration.
duo_input::u1::PeripheralPort describe_role(
    const duo_input::u1::pio_usb::DeviceRegistry& registry,
    duo_input::u1::input::DeviceKind kind) {
    duo_input::u1::PeripheralPort port;
    const auto* interface = registry.owner(kind);
    if (interface == nullptr) {
        // Nothing holds this role. That is an empty slot, reported as one -
        // and note that an interface which mounted but was ignored (a second
        // keyboard, or something nothing could classify) leaves the slot empty
        // here too, which is exactly what the backend's ignored-interface
        // counters below exist to explain.
        return port;
    }
    port.attached = true;
    // latch_fault() hands a faulted interface's role slot back, so an owner is
    // normally a working one; the two flags are still read rather than assumed,
    // because "attached but not reading" is the state an operator most needs
    // named and inferring it from an absence would be the same mistake the
    // whole reply exists to avoid.
    port.ready = !interface->fault_pending && !interface->faulted;
    port.kind = static_cast<std::uint8_t>(interface->identity.kind);
    port.vendor_id = interface->identity.vendor_id;
    port.product_id = interface->identity.product_id;
    // Only what the device's own report descriptor declared. A boot-protocol
    // mouse is read under an assumed layout, and reporting that assumption as
    // the device's own declaration would put a number in a compatibility
    // matrix that the peripheral never said - the same rule the CH375 branch
    // above applies, for the same reason.
    port.buttons = interface->descriptor_present
                       ? static_cast<std::uint8_t>(interface->identity.mouse_layout.buttons.bits)
                       : 0;
    port.report_descriptor_bytes = interface->descriptor_bytes;
    std::memcpy(port.descriptor_hash, interface->identity.descriptor_hash,
                sizeof(port.descriptor_hash));
    return port;
}

/// Every counter the registry keeps, in the fixed order the wire carries.
duo_input::u1::BackendCounters describe_backend_counters(
    const duo_input::u1::pio_usb::DeviceRegistry& registry) {
    duo_input::u1::BackendCounters counters;
    counters.ignored_interfaces = registry.ignored_interface_count();
    counters.ignored_role_already_claimed = registry.ignored_role_taken_count();
    counters.event_overflows = registry.event_overflow_count();
    counters.detach_overflows = registry.detach_overflow_count();
    counters.stale_events_discarded = registry.stale_event_discard_count();
    counters.arm_failures = registry.arm_failure_count();
    counters.arm_escalations = registry.arm_escalation_count();
    counters.stall_signals = registry.stall_signal_count();
    counters.duplicate_mounts = registry.duplicate_mount_count();
    counters.device_overflows = registry.device_overflow_count();
    counters.interface_overflows = registry.interface_overflow_count();
    counters.callback_overflows = registry.callback_overflow_count();
    return counters;
}

/// What the host stack and its root port are doing, in the wire's own shape.
///
/// Read here rather than kept as a second copy for the same reason
/// describe_role reads the registry: a copy that Core 0 refreshed on its own
/// schedule could disagree with the thing it describes, and the whole point of
/// these fields is that they are the readings nothing else can give.
duo_input::u1::HostObservation describe_host_observation(
    const duo_input::u1::pio_usb::PioUsbBackend& backend) {
    const duo_input::u1::pio_usb::HostObservability observed = backend.observe();
    duo_input::u1::HostObservation out;
    // The bit positions are the same on both sides by construction - the
    // pio_usb constants below and the wire's documented bit order are one
    // definition each, and this is where they meet.
    out.init_flags = observed.init_flags;
    out.clk_hz_at_begin = observed.clk_hz_at_begin;
    out.clk_hz_now = observed.clk_hz_now;
    out.sof_frame_count = observed.sof_frame_count;
    out.root_port_state = observed.root_port_state;
    out.root_port_connects = observed.root_port_connects;
    out.core1_passes = observed.core1_passes;
    out.mount_events = observed.mount_events;
    out.umount_events = observed.umount_events;
    out.hid_mount_events = observed.hid_mount_events;
    out.ep_slots_opened = observed.ep_slots_opened;
    out.ep_max_failed_count = observed.ep_max_failed_count;
    out.max_pass_gap_us = observed.max_pass_gap_us;
    out.max_sof_gap = observed.max_sof_gap;
    out.root_port_resets = observed.root_port_resets;
    out.hub_mount_events = observed.hub_mount_events;
    return out;
}
#endif  // DUO_INPUT_BACKEND_CH375

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

#ifdef DUO_INPUT_BACKEND_CH375
// Reads CH375's own events and setup across the neutral source boundary. The
// id each carries is just this channel's index - the pipeline never
// interprets it.
duo_input::u1::input::Ch375SourceAdapter g_keyboard_source(0);
duo_input::u1::input::Ch375SourceAdapter g_mouse_source(1);

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
#else
// U1's whole USB host: one XL334P4 hub on RHPort 1, standing in for both
// CH375 channels above. See firmware/u1_main/pio_usb/backend.hpp - it is
// Core 1's only door into it, the same way the two Ch375SourceAdapters above
// are Core 1's only door into a CH375 channel.
duo_input::u1::pio_usb::PioUsbBackend g_pio_usb_backend;
#endif

#if DUO_CH375_PROBE
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

/// What the start-up probe saw, at namespace scope rather than in main's frame.
///
/// Core 0's whole stack is two kilobytes, and the deepest chain on it runs
/// from main through the CDC service into the SHA-256 of a staged
/// configuration. These are one-shot observations taken once before the loop
/// starts and read once inside it; on the stack they were most of an
/// eight-hundred-byte frame that the whole of that chain sits on top of.
struct ProbeObservations {
    duo_input::u1::PinActivity keyboard_pad;
    duo_input::u1::PinActivity mouse_pad;
    duo_input::u1::PinActivity keyboard_int;
    duo_input::u1::PinActivity mouse_int;
    duo_input::u1::PinActivity tx_while_high;
    duo_input::u1::PinActivity rx_while_high;
    duo_input::u1::PinActivity tx_while_low;
    duo_input::u1::PinActivity rx_while_low;
    duo_input::u1::PinActivity mouse_tx_while_high;
    duo_input::u1::PinActivity mouse_rx_while_high;
    duo_input::u1::PinActivity mouse_tx_while_low;
    std::uint16_t keyboard_bad_at_boot = 0;
    std::uint16_t mouse_bad_at_boot = 0;
    std::uint16_t keyboard_quiet_at_boot = 0;
    std::uint16_t mouse_quiet_at_boot = 0;
    duo_input::u1::Ch375ProbeResult keyboard_probe;
    duo_input::u1::Ch375ProbeResult mouse_probe;
    duo_input::diagnostics::Ch375SingleProbeObservation single_probe;
};

ProbeObservations g_probe;

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
/// rewrites.
///
/// Runs on Core 1 once that core is up, and only after the scheduler has been
/// stopped and drained - by swap_profile, or by the release_all that starts
/// adopt_configuration below. Core 0 reaches it only through
/// hand_configuration_to_core1's !core1_running() branch, which is before the
/// second core exists and therefore has nobody to race. Nothing is mid-macro
/// reading what this replaces, and no other core is inside these structures:
/// once Core 1 is running, Core 0 asks for a configuration change and waits to
/// be told it happened rather than reaching in and making it.
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

/// Where Core 0 leaves a configuration for Core 1 to pick up.
duo_input::u1::ConfigHandoff g_config_handoff;

/// Switch every flash-backed runtime view. Runs on Core 1.
///
/// Let go of everything first. What was held was held under the old
/// configuration's meaning, and the computer it was sent to will never hear
/// about it again; release_all also stops and drains the scheduler, which is
/// what makes rewriting the step pool underneath it safe.
///
/// The ReleaseAll this queues is submitted by Core 1, which is the queue's one
/// producer. Core 0 submitting it - which is what this code used to do, under
/// a lockout - races the producer at whatever instruction the lockout
/// interrupt landed on, and the command it overwrites may be the release that
/// stops a key repeating forever.
bool adopt_configuration(duo_input::protocol::ByteView package) {
    g_runtime.release_all();
    const bool loaded = g_profiles.load(package);
    // A package that is not a configuration leaves StoredProfiles empty, which
    // is also what a factory reset asks for. Profile zero is what an empty
    // configuration answers to.
    const std::uint8_t profile = loaded ? g_profiles.active_profile_id() : 0;
    g_runtime.set_profile_now(profile);
    install_macros(profile);
    return loaded;
}

/// How long Core 0 waits for Core 1 to adopt a configuration.
///
/// A pass round Core 1 is microseconds to a few milliseconds; this is orders
/// of magnitude longer, and still far short of the two-second watchdog, so a
/// core that has genuinely stopped produces a refused WRITE_COMMIT rather than
/// a board that hangs inside a USB callback.
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

/// Switch every flash-backed runtime view, from Core 0's side.
///
/// A committed A/B slot becomes the spare slot on the following write.  Core
/// 0 therefore cannot acknowledge WRITE_COMMIT until Core 1 has stopped
/// reading the old slot and all macro pointers have been rebuilt against the
/// new one.
///
/// It asks and waits rather than stopping Core 1 and doing the work itself.
/// `multicore_lockout` pauses the other core at an arbitrary instruction: in
/// the middle of set_profile_now's binding table, or of install_macros'
/// definition loop. Rebuilding those underneath a core that then resumes its
/// own half-finished rebuild produces a binding table interleaved from two
/// profiles, which is reachable whenever a binding- or macro-driven profile
/// swap coincides with a WRITE_COMMIT. Here Core 1 does the work at a point it
/// chose, and the acknowledgement means the work is finished rather than
/// merely interrupted.
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

#ifndef DUO_INPUT_BACKEND_CH375
    // The physical input path is product functionality, not a bring-up
    // probe - compare the CH375 branch's g_keyboard_port.begin() /
    // g_mouse_port.begin() calls in main(), which run before Core 1 exists.
    // This backend's tuh_init has to run on Core 1 itself - see backend.hpp -
    // so it happens here instead, as the first thing this core does once it
    // can be stopped by a flash write, and before anything below reads a
    // clock or a device it changes.
    g_pio_usb_backend.begin();
#endif

    std::uint8_t installed = g_profiles.active_profile_id();
    g_runtime.set_profile_now(installed);
    install_macros(installed);

    while (true) {
        const std::uint32_t now_ms = to_ms_since_boot(get_absolute_time());

        // First thing in the pass, between one whole turn and the next: not
        // inside a binding table, not inside the macro definitions, not
        // holding an event half-processed. That is the difference between this
        // and being paused wherever a lockout interrupt happened to land.
        {
            duo_input::protocol::ByteView package{nullptr, 0};
            if (g_config_handoff.take(package)) {
                g_config_handoff.complete(adopt_configuration(package));
                installed = g_runtime.active_profile();
            }
        }

        const std::uint32_t now_us = time_us_32();
#if DUO_CH375_PROBE
        if (g_last_pass_us != 0) {
            const std::uint32_t elapsed = now_us - g_last_pass_us;
            if (elapsed > g_worst_pass_us) {
                g_worst_pass_us = elapsed;
            }
        }
        g_last_pass_us = now_us;
#endif

#ifdef DUO_INPUT_BACKEND_CH375
        duo_input::u1::ch375::Ch375Device* devices[2] = {&g_keyboard_device, &g_mouse_device};
        duo_input::u1::ch375::DescriptorSetup* setups[2] = {&g_keyboard_setup, &g_mouse_setup};
        duo_input::u1::input::InputPipeline* pipelines[2] = {&g_keyboard_pipeline,
                                                             &g_mouse_pipeline};
        duo_input::u1::input::Ch375SourceAdapter* sources[2] = {&g_keyboard_source,
                                                                 &g_mouse_source};
#if DUO_CH375_PROBE
        DeviceTally* tallies[2] = {&g_keyboard_tally, &g_mouse_tally};
#endif
        // Static: one of these is seventy-odd bytes of report buffer, and this
        // core has two kilobytes for everything below it.
        static duo_input::u1::ch375::Ch375Event event;
        for (int index = 0; index < 2; ++index) {
            devices[index]->tick(now_us);
            while (devices[index]->take_event(event)) {
#if DUO_CH375_PROBE
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
#endif
                // Read across the neutral source boundary before anything
                // above it sees this event. convert() returns false for a
                // Ch375EventKind this boundary carries no case for - Attached
                // and the empty None - which the pipeline never acted on
                // either.
                //
                // Static for the same reason as ``event`` above: this core's
                // stack is two kilobytes for everything below it.
                static duo_input::u1::input::SourceEvent source_event;
                if (!sources[index]->convert(event, source_event)) {
                    continue;
                }

                // A detach synthesises the releases the peripheral never sent,
                // which is the only thing standing between a yanked cable and
                // a computer that types until it is rebooted.
                // The moment this report was read out of the controller, so
                // every command it produces carries it and Core 0 can subtract
                // it from the moment it applies them. Set from the event rather
                // than from the clock here: the report may have been queued a
                // pass or two ago, and timing it from now would hide exactly
                // the backlog worth knowing about.
                g_runtime.set_event_origin_us(source_event.received_us);
                pipelines[index]->on_event(source_event, sources[index]->identity(*setups[index]),
                                           now_ms);
                // Cleared immediately. A stamp left standing would be attached
                // to whatever the device did next - a macro step, a timeout's
                // release - and the further from the report that happened, the
                // worse the reading it would produce.
                g_runtime.set_event_origin_us(0);
            }
        }
#else
        // One backend, one door: task() services tuh_task() and whatever it
        // queues comes out through take_event(), in the same neutral shape
        // Ch375SourceAdapter::convert() produces on the CH375 side above.
        // logical_port() is this board's only routing decision - which of
        // the two InputPipelines a resolved DeviceKind belongs to - the same
        // one CH375's per-channel adapters make by construction, having one
        // adapter per physical port.
        g_pio_usb_backend.task(now_us);

        duo_input::u1::input::InputPipeline* pipelines[2] = {&g_keyboard_pipeline,
                                                             &g_mouse_pipeline};
        // Static for the same reason as CH375's own event/source_event
        // above: this core's stack is two kilobytes for everything below it.
        static duo_input::u1::input::SourceEvent source_event;
        static duo_input::u1::input::SourceIdentity source_identity;
        while (g_pio_usb_backend.take_event(source_event, source_identity)) {
            const int port = duo_input::u1::pio_usb::PioUsbBackend::logical_port(
                source_identity.kind);
            if (port < 0) {
                // Unknown resolves to nothing to route - the same thing an
                // Attached CH375 event above resolves to before convert()
                // ever returns true for it.
                continue;
            }
            g_runtime.set_event_origin_us(source_event.received_us);
            pipelines[port]->on_event(source_event, source_identity, now_ms);
            g_runtime.set_event_origin_us(0);
        }
#endif  // DUO_INPUT_BACKEND_CH375

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
static_assert(CFG_TUD_CDC_TX_BUFSIZE >= duo_input::u1::kMaxWireFrame,
              "TinyUSB TX FIFO must hold one complete encoded protocol frame");

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
        case 0xFA:
            return "up, but refused boot protocol";
        case 0xF9:
            return "up, but never answered the protocol request";
        case 0xF8:
            return "boot: the interface declared no report descriptor";
        case 0xF7:
            return "boot: its report descriptor is longer than there is room for";
        case 0xF6:
            return "boot: it refused to hand over its report descriptor";
        case 0xF5:
            return "boot: its report descriptor could not be read as a mouse";
        case 0xF4:
            return "boot: a packet of its report descriptor could not be collected";
        case 0xF3:
            return "boot: silent on the descriptor request too many times to keep asking";
        case 0xF2:
            return "read through its own report descriptor";
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

namespace {
/// Write the bring-up report into the diagnostics reply.
///
/// Its own function, and not for tidiness: forty-odd arguments to one
/// snprintf, three pointer arrays and the format temporaries put four
/// hundred bytes into main's frame, and main is where Core 0's deepest call
/// chain starts - through the CDC service and into the SHA-256 of a staged
/// configuration, on a two-kilobyte stack. Down here those bytes are on a
/// branch of their own that reaches nothing else - which only holds if it
/// stays a branch: called from one place, the compiler folds it straight back
/// into main and the frame comes with it.
[[gnu::noinline]] void report_probe(duo_input::u1::ConfigService& config) {
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
    // Large enough for two compact device summaries and one ordinary raw HID
    // report descriptor.  The previous prose report spent the whole buffer on
    // the first device and cut the second one mid-line, exactly where the
    // failing mouse happened to be.  This format favours discriminating facts
    // over narration and ends with a marker that makes truncation visible.
    static char text[1023] = {};
    int used = 0;
    used += snprintf(
        text + used, sizeof(text) - static_cast<std::size_t>(used),
        "side kaux=%lu/%lu/%u maux=%lu/%lu/%u\n",
        static_cast<unsigned long>(g_keyboard_pipeline.keychron_side_presses()),
        static_cast<unsigned long>(g_keyboard_pipeline.keychron_side_releases()),
        g_keyboard_pipeline.keychron_side_held() ? 1u : 0u,
        static_cast<unsigned long>(g_mouse_pipeline.keychron_side_presses()),
        static_cast<unsigned long>(g_mouse_pipeline.keychron_side_releases()),
        g_mouse_pipeline.keychron_side_held() ? 1u : 0u);

    // What the keyboard normalizer made of what it was handed.
    //
    // kerr is a histogram: how many reports carried exactly N ErrorRollOver
    // values in their key field, for N of 0 through 5. HID 1.11 8.3 has a
    // keyboard that has lost count put ErrorRollOver in *every* array field,
    // and this firmware treats six of them as that signal - so a keyboard
    // that declares five slots can never raise it. If the fifth bucket is
    // climbing while somebody types, reports meaning "I cannot say what is
    // held" are being read as "nothing is held", and that releases keys
    // nobody let go of.
    for (int side = 0; side < 2 && used < static_cast<int>(sizeof(text)) - 1; ++side) {
        const auto& normalizer = (side == 0 ? g_keyboard_pipeline : g_mouse_pipeline)
                                     .keyboard_normalizer();
        used += snprintf(
            text + used, sizeof(text) - static_cast<std::size_t>(used),
            "kerr %s=%u/%u/%u/%u/%u/%u roll=%u ref=%u rej=%u down=%u/%u up=%u unpaced=%u\n"
            "kusb sent=%u same=%u busy=%u\n",
            side == 0 ? "k" : "m",
            normalizer.probe_error_slots(0), normalizer.probe_error_slots(1),
            normalizer.probe_error_slots(2), normalizer.probe_error_slots(3),
            normalizer.probe_error_slots(4), normalizer.probe_error_slots(5),
            normalizer.probe_rollovers(), normalizer.probe_refused(),
            normalizer.probe_rejected(),
            normalizer.probe_key_downs(), normalizer.probe_modifier_downs(),
            normalizer.probe_key_ups(), g_outputs.keyboard_unpaced(),
            usb.keyboard_sent(), usb.keyboard_same(), usb.keyboard_busy());
        if (used < 0 || used > static_cast<int>(sizeof(text)) - 1) {
            used = static_cast<int>(sizeof(text)) - 1;
            break;
        }
    }
    const char* names[2] = {"keyboard", "mouse"};
    for (int index = 0; index < 2 && used < static_cast<int>(sizeof(text)) - 1; ++index) {
        const duo_input::u1::ch375::Ch375Device& device = *devices[index];
        const DeviceTally& tally = *tallies[index];
        const unsigned state = static_cast<unsigned>(device.state());
        used += snprintf(
            text + used, sizeof(text) - static_cast<std::size_t>(used),
            "%s st=%s rate=%s life=%u/%u/%u reports=%u int=%u/%u\n"
            " bus ce=%s usb=%02X status=%u/%u/%u/%u/%u enumfail=%u modefail=%u polls=%u/%u\n"
            " hid=%s ep=%u pkt=%u boot=%s/%s setup=%u last=%02X cfgerr=%u\n"
            " rd=%02X err=%u got/want=%u/%u layout=%s id=%s/%u "
            "b=%u x=%u/%u+%u:%u y=%u/%u+%u:%u w=%u p=%u min=%u\n"
            " klayout=%s kkind=%u kbits=%u/%u@%u kid=%s/%u kmin=%u\n"
            " last=%u:%02X %02X %02X %02X port=%u refused=%u "
            "recover=%u/%u/%u/%u/%u slow=%u drop=%u\n",
            names[index], state < 7 ? kStates[state] : "?",
            device.device_rate_known() ? (device.device_is_low_speed() ? "low" : "full")
                                       : "unknown",
            tally.attached, tally.detached,
            tally.ready, tally.reports,
            device.interrupts_seen(), device.status_reads_failed(),
            (index == 0 ? g_probe.keyboard_probe : g_probe.mouse_probe).check_exist_ok
                ? "0xA8"
                : "WRONG",
            device.last_status(),
            device.status_connect(), device.status_disconnect(), device.status_success(),
            device.status_failure(), device.status_impossible(),
            device.enumerate_failures(), device.mode_failures(),
            device.polls_issued(), device.primary_polls(),
            setups[index]->kind() == duo_input::u1::ch375::DeviceKind::Keyboard
                ? "keyboard"
                : (setups[index]->kind() == duo_input::u1::ch375::DeviceKind::Mouse
                       ? "mouse"
                       : "nothing"),
            setups[index]->interrupt_endpoint(), setups[index]->max_packet(),
            setups[index]->boot_protocol() ? "yes" : "no",
            setups[index]->boot_protocol_selected() ? "yes" : "no",
            setups[index]->attempts(), setups[index]->last_status(),
            static_cast<unsigned>(setups[index]->last_parse_error()),
            setups[index]->last_report_descriptor_status(),
            static_cast<unsigned>(setups[index]->last_report_descriptor_error()),
            setups[index]->report_descriptor_bytes(),
            setups[index]->report_descriptor_wanted(),
            setups[index]->has_mouse_layout() ? "own" : "boot",
            setups[index]->mouse_layout().report_id ? "yes" : "no",
            setups[index]->mouse_layout().report_id_value,
            setups[index]->mouse_layout().buttons.offset,
            setups[index]->mouse_layout().x.offset, setups[index]->mouse_layout().x.bytes,
            setups[index]->mouse_layout().x.bit_offset, setups[index]->mouse_layout().x.bits,
            setups[index]->mouse_layout().y.offset, setups[index]->mouse_layout().y.bytes,
            setups[index]->mouse_layout().y.bit_offset, setups[index]->mouse_layout().y.bits,
            setups[index]->mouse_layout().wheel.offset,
            setups[index]->mouse_layout().pan.offset,
            setups[index]->mouse_layout().minimum_body_bytes,
            // Whether this keyboard is being read in its own protocol or in
            // boot's. The one field that says which of the two paths a run of
            // the compatibility matrix was actually on.
            setups[index]->has_keyboard_layout() ? "report" : "boot",
            static_cast<unsigned>(setups[index]->keyboard_layout().key_kind),
            setups[index]->keyboard_layout().key_element_bits,
            setups[index]->keyboard_layout().key_element_count,
            setups[index]->keyboard_layout().key_bit_offset,
            setups[index]->keyboard_layout().report_id ? "yes" : "no",
            setups[index]->keyboard_layout().report_id_value,
            setups[index]->keyboard_layout().minimum_body_bytes,
            tally.last_size, tally.last[0], tally.last[1], tally.last[2], tally.last[3],
            (index == 0 ? g_keyboard_port : g_mouse_port).baud(),
            device.baud_change_failures(), device.setup_mode_failures(),
            device.recover_mode_failures(), device.presence_lost(),
            device.quiet_rearms(), device.collapses_while_raised(),
            worst_pass_us,
            static_cast<unsigned>(g_runtime.dropped_commands()));
        // snprintf answers with how much it *would* have written. Left
        // unclamped, the next call is handed a negative amount of room
        // and the total runs past the end of the buffer.
        if (used < 0 || used > static_cast<int>(sizeof(text)) - 1) {
            used = static_cast<int>(sizeof(text)) - 1;
            break;
        }

        // Sparse composite-interface reports are the evidence needed to map
        // buttons which never appear on the mouse endpoint. Keep the previous
        // and current packet: a press followed quickly by a release would
        // otherwise overwrite the only interesting half before CDC reads it.
        for (unsigned auxiliary = 0; auxiliary < 2 &&
                                     used < static_cast<int>(sizeof(text)) - 24;
             ++auxiliary) {
            const std::uint8_t endpoint = auxiliary == 0
                                              ? setups[index]->auxiliary_endpoint()
                                              : setups[index]->secondary_auxiliary_endpoint();
            if (endpoint == 0 || devices[index]->auxiliary_reports(auxiliary) == 0) {
                continue;
            }
            used += snprintf(text + used, sizeof(text) - static_cast<std::size_t>(used),
                             " aux%u n=%u p=", endpoint,
                             devices[index]->auxiliary_reports(auxiliary));
            const duo_input::protocol::ByteView previous =
                devices[index]->auxiliary_previous_report(auxiliary);
            for (std::size_t byte = 0; byte < previous.size && byte < 9 &&
                                       used < static_cast<int>(sizeof(text)) - 3;
                 ++byte) {
                used += snprintf(text + used, sizeof(text) - static_cast<std::size_t>(used),
                                 "%02X", previous.data[byte]);
            }
            used += snprintf(text + used, sizeof(text) - static_cast<std::size_t>(used), " l=");
            const duo_input::protocol::ByteView last =
                devices[index]->auxiliary_last_report(auxiliary);
            for (std::size_t byte = 0; byte < last.size && byte < 9 &&
                                       used < static_cast<int>(sizeof(text)) - 3;
                 ++byte) {
                used += snprintf(text + used, sizeof(text) - static_cast<std::size_t>(used),
                                 "%02X", last.data[byte]);
            }
            if (used < static_cast<int>(sizeof(text)) - 1) {
                text[used++] = '\n';
                text[used] = '\0';
            }
            if (endpoint == 1 && used < static_cast<int>(sizeof(text)) - 16) {
                used += snprintf(text + used,
                                 sizeof(text) - static_cast<std::size_t>(used), " trace=");
                const unsigned trace_count = devices[index]->auxiliary_trace_count(auxiliary);
                for (unsigned trace = 0;
                     trace < trace_count && used < static_cast<int>(sizeof(text)) - 19;
                     ++trace) {
                    const duo_input::protocol::ByteView packet =
                        devices[index]->auxiliary_trace_report(auxiliary, trace);
                    for (std::size_t byte = 0; byte < packet.size && byte < 9; ++byte) {
                        used += snprintf(text + used,
                                         sizeof(text) - static_cast<std::size_t>(used), "%02X",
                                         packet.data[byte]);
                    }
                    text[used++] = trace + 1 == trace_count ? '\n' : ',';
                    text[used] = '\0';
                }
            }
        }

        const duo_input::protocol::ByteView raw = setups[index]->report_descriptor();
        if (setups[index]->kind() == duo_input::u1::ch375::DeviceKind::Keyboard &&
            used < static_cast<int>(sizeof(text)) - 24) {
            used += snprintf(text + used, sizeof(text) - static_cast<std::size_t>(used),
                             "kbd-desc=%u/%u:", setups[index]->report_descriptor_bytes(),
                             setups[index]->report_descriptor_wanted());
            for (std::size_t byte = 0;
                 byte < raw.size && used < static_cast<int>(sizeof(text)) - 3; ++byte) {
                used += snprintf(text + used, sizeof(text) - static_cast<std::size_t>(used),
                                 "%02X", raw.data[byte]);
            }
            if (used < static_cast<int>(sizeof(text)) - 1) {
                text[used++] = '\n';
                text[used] = '\0';
            }
        }

        // A parser failure without its bytes is still a guess.  Keep this in
        // the same reply so the exact real descriptor can become a native
        // regression vector before the parser is changed.
        if (setups[index]->kind() == duo_input::u1::ch375::DeviceKind::Mouse &&
            raw.size != 0 && !setups[index]->has_mouse_layout()) {
            used += snprintf(text + used, sizeof(text) - static_cast<std::size_t>(used),
                             " rdhex=");
            for (std::size_t byte = 0;
                 byte < raw.size && used < static_cast<int>(sizeof(text)) - 3; ++byte) {
                used += snprintf(text + used, sizeof(text) - static_cast<std::size_t>(used),
                                 "%02X", raw.data[byte]);
            }
            if (used < static_cast<int>(sizeof(text)) - 1) {
                text[used++] = '\n';
                text[used] = '\0';
            }
        }
    }
    if (used < static_cast<int>(sizeof(text)) - 5) {
        used += snprintf(text + used, sizeof(text) - static_cast<std::size_t>(used), "END\n");
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

}  // namespace

#endif

int main() {
#ifdef DUO_INPUT_BACKEND_PIO_USB
    // Pico-PIO-USB's reference host changes clk_sys before it starts any
    // peripheral or the second core, then gives the PLL ten milliseconds to
    // settle. Keep CH375 on its established startup path: it neither needs nor
    // expects the 120 MHz PIO bit-engine clock.
    set_sys_clock_khz(120000, true);
    sleep_ms(10);
#endif

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
    static duo_input::u1::SpiMaster link;
    static duo_input::u1::PicoFlash flash;
    static duo_input::storage::AbStore store(flash);
    static CdcWriter cdc_writer;
    static RuntimeConfig runtime_config;
    static duo_input::u1::ConfigService config(store, cdc_writer, runtime_config);

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
            config.set_initial_active_profile(g_profiles.active_profile_id());
        }
    }

#if DUO_CH375_PROBE
    // Both ports come up before anything else touches these pins, and before
    // Core 1 - which owns the controllers above them - is launched.
    //
    // Before any state machine touches them, read both receive pads as plain
    // inputs. A pad with nothing on it and a pull-up should sit high and never
    // move; if one of them does move, the answer is about solder, not software.
    g_probe.keyboard_pad = duo_input::u1::watch_bare_pin(duo_input::u1::kPinKeyboardRx, 1000);
    g_probe.mouse_pad = duo_input::u1::watch_bare_pin(duo_input::u1::kPinMouseRx, 1000);

    // Watch both interrupt lines for a second rather than sampling them once.
    //
    // The single-shot version pulled the pin down and called it live only if
    // it stayed high - which is true of an idle INT# and false of one that is
    // asserting, because the signal is active low. So it reported "no wire" for
    // whichever chip happened to have an interrupt pending, which is exactly
    // the chip with a device on it. It measured the wrong thing confidently.
    g_probe.keyboard_int = duo_input::u1::watch_bare_pin(duo_input::u1::kPinKeyboardInt, 1000);
    g_probe.mouse_int = duo_input::u1::watch_bare_pin(duo_input::u1::kPinMouseInt, 1000);

    // Static-level loopback test, deliberately performed before PIO owns the
    // pins.  With S1 tied to S3 the level driven on GP0 must return on GP1.
    // This separates an electrical oscillator from a malformed UART program:
    // no UART state machine is running while these four readings are taken.
    gpio_init(duo_input::u1::kPinKeyboardTx);
    gpio_set_dir(duo_input::u1::kPinKeyboardTx, GPIO_OUT);
    gpio_put(duo_input::u1::kPinKeyboardTx, 1);
    sleep_us(100);
    g_probe.tx_while_high = watch_existing_pin(duo_input::u1::kPinKeyboardTx, 3000);
    g_probe.rx_while_high = watch_existing_pin(duo_input::u1::kPinKeyboardRx, 3000);

    gpio_put(duo_input::u1::kPinKeyboardTx, 0);
    sleep_us(100);
    g_probe.tx_while_low = watch_existing_pin(duo_input::u1::kPinKeyboardTx, 3000);
    g_probe.rx_while_low = watch_existing_pin(duo_input::u1::kPinKeyboardRx, 3000);
    gpio_put(duo_input::u1::kPinKeyboardTx, 1);

    // Repeat the same static test on the independently wired mouse channel.
    // Two channels failing alike implicate their shared translator/power
    // arrangement; one failing alone points back to that channel's wiring.
    gpio_init(duo_input::u1::kPinMouseTx);
    gpio_set_dir(duo_input::u1::kPinMouseTx, GPIO_OUT);
    gpio_put(duo_input::u1::kPinMouseTx, 1);
    sleep_us(100);
    g_probe.mouse_tx_while_high = watch_existing_pin(duo_input::u1::kPinMouseTx, 3000);
    g_probe.mouse_rx_while_high = watch_existing_pin(duo_input::u1::kPinMouseRx, 3000);
    gpio_put(duo_input::u1::kPinMouseTx, 0);
    sleep_us(100);
    g_probe.mouse_tx_while_low = watch_existing_pin(duo_input::u1::kPinMouseTx, 3000);
    gpio_put(duo_input::u1::kPinMouseTx, 1);

#endif

#ifdef DUO_INPUT_BACKEND_CH375
    // The physical input path is product functionality, not a bring-up probe.
    // Only the observations around it are conditional; both ports and both
    // device state machines run in every release image.
    g_keyboard_port.begin(pio0, duo_input::u1::kPinKeyboardTx, duo_input::u1::kPinKeyboardRx,
                          duo_input::u1::kPinKeyboardInt);
    g_mouse_port.begin(pio0, duo_input::u1::kPinMouseTx, duo_input::u1::kPinMouseRx,
                       duo_input::u1::kPinMouseInt);
#endif  // DUO_INPUT_BACKEND_CH375
    // The PIO USB backend has nothing to bring up here: its tuh_init has to
    // run on Core 1 itself (see core1_entry's g_pio_usb_backend.begin()
    // call), and GP0/GP1 are claimed there, not by this core.

#if DUO_CH375_PROBE
    // Before a single byte goes out, on either port.
    g_probe.keyboard_quiet_at_boot = duo_input::u1::listen_without_sending(
        g_keyboard_port, 1000, g_probe.keyboard_bad_at_boot);
    g_probe.mouse_quiet_at_boot = duo_input::u1::listen_without_sending(
        g_mouse_port, 1000, g_probe.mouse_bad_at_boot);

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
    g_probe.keyboard_probe = duo_input::u1::probe_ch375(g_keyboard_port, g_keyboard_commands);
    g_probe.mouse_probe = duo_input::u1::probe_ch375(g_mouse_port, g_mouse_commands);
    // The experiment that left the bus reset out changed nothing - the device
    // was lost at exactly the same rate without it - so the reset is not what
    // loses it, and the datasheet's sequence is back.

    duo_input::diagnostics::Ch375SingleProbeObservation& single_probe = g_probe.single_probe;
    single_probe.pad_low_percent = g_probe.tx_while_high.low_percent;
    single_probe.pad_transitions = g_probe.tx_while_high.transitions;
    single_probe.quiet_frames = g_probe.rx_while_high.low_percent;
    single_probe.quiet_bad_frames = g_probe.rx_while_high.transitions;
    single_probe.probe_quiet_frames = g_probe.tx_while_low.low_percent;
    single_probe.probe_quiet_first = g_probe.rx_while_low.low_percent;
    single_probe.raw_count =
        static_cast<std::uint8_t>(g_probe.tx_while_low.transitions & 0xFFu);
    single_probe.raw[0] = g_probe.rx_while_low.transitions;
    single_probe.raw[1] = g_probe.mouse_tx_while_high.transitions;
    single_probe.raw[2] =
        static_cast<std::uint16_t>(g_probe.mouse_tx_while_high.low_percent) |
        (static_cast<std::uint16_t>(g_probe.mouse_rx_while_high.low_percent) << 8);
    single_probe.raw[3] = g_probe.mouse_rx_while_high.transitions;
    single_probe.framing_errors = g_probe.mouse_tx_while_low.low_percent;
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

#ifdef DUO_INPUT_BACKEND_PIO_USB
    // Every run-time call which can touch SPI goes through this one gate.
    // The clock is already final before link.begin(), so the first baud
    // refresh is normally a no-op. Keep the gate as a safety contract: no
    // transfer runs until Core 1 has finished host bring-up and published it,
    // and a future early link initialization still gets its baud restored.
    // No Core 0 service waits here.
    duo_input::u1::pio_usb::LinkStartupGate link_startup;
    const auto with_link = [&](auto&& action) {
        return link_startup.run_if_ready(
            g_pio_usb_backend.clock_settled(), [&] { link.refresh_baudrate(); }, action);
    };
#endif

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
#ifdef DUO_INPUT_BACKEND_CH375
            link.send_release_all(now_ms);
#else
            with_link([&] { link.send_release_all(now_ms); });
#endif
            // Asked for rather than done here: the command queue has exactly
            // one producer and this core is not it.
            g_runtime.request_release_all();
        }

        // What the host asked of Core 1, and what Core 1 has to say back.
        //
        // In core_bridge.cpp rather than here, and tested there: the ordering
        // this depends on is not visible from the calls, and main.cpp cannot
        // be built on a desktop.
        duo_input::u1::pump_core_bridge(config, g_runtime);

        // Bounded, so a burst of input cannot starve the USB it is for - and
        // held at the keyboard state that has not reached both computers yet,
        // which is why it needs the clock. The publish and the poll below are
        // what release it.
        // The microsecond clock alongside the millisecond one, because every
        // command applied in this pass is timed against it and the budget it
        // is measured against is twenty milliseconds. Read here, immediately
        // before the drain, so the interval it closes is as short as the code
        // allows.
        g_outputs.drain(now_ms, time_us_32());

        usb.publish(g_outputs);

        // PC2's half of the state goes over the link. It sends on change and
        // otherwise heartbeats, so a quiet device does not saturate the bus
        // and does not look severed either.
#ifdef DUO_INPUT_BACKEND_CH375
        link.poll(now_ms, g_outputs);
#else
        with_link([&] { link.poll(now_ms, g_outputs); });
#endif  // DUO_INPUT_BACKEND_CH375

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

        // Published every pass for the same reason, and after the drain that
        // decides it: dropped_commands says input was lost at some point,
        // this says the queue is overflowing right now. The runtime clears it
        // itself once a pass goes by with nothing refused, so what the host
        // reads is a live condition rather than a latch.
        config.set_runtime_fault(g_outputs.fault());

        // Published every pass, like the link state and for the same reason:
        // the output runtime owns these and the CDC service reports them, and
        // neither reaches into the other. What they hold is U1's own interval -
        // a peripheral report reaching Core 1 against the command it produced
        // being applied here - and not a keystroke's journey to a far screen,
        // which this board has no way to observe either end of.
        config.set_input_latency(g_outputs.keyboard_latency(), g_outputs.mouse_latency());

        // What is on the two peripheral ports, published every pass. A device
        // plugged into U1 is on U1's bus and not on either computer's, so this
        // reply is the only place anything can learn what it was - which is
        // what a compatibility matrix row needs and what nothing else can
        // supply. Both ports always: an empty port is a fact about the run.
        //
        // And which backend read them, published in the same pass and from the
        // same place. A report that does not name the host stack cannot be
        // acted on months later: the CH375 pair and the PIO USB host fail in
        // entirely different ways, and half the counters below exist only on
        // one of them.
#ifdef DUO_INPUT_BACKEND_CH375
        config.set_peripherals(describe_port(g_keyboard_device, g_keyboard_setup),
                               describe_port(g_mouse_device, g_mouse_setup));
        // No counters: the CH375 channels keep none of the host-stack figures
        // below, and sending twelve zeros for them would put twelve readings
        // into a report that nothing ever measured.
        config.set_backend(duo_input::protocol::InputBackend::CH375);
        // And no host observation: this image has no TinyUSB host stack, no
        // Pico-PIO-USB root port and no Core 1 backend loop, so every field in
        // that block would be a reading of hardware that is not there. The
        // block is still sent, carrying a length of zero - which says "this
        // firmware has the field and has nothing to put in it", a different
        // fact from an older firmware that sends no block at all.
        config.set_host_observation();
#else
        {
            const auto& registry = g_pio_usb_backend.registry();
            config.set_peripherals(
                describe_role(registry, duo_input::u1::input::DeviceKind::Keyboard),
                describe_role(registry, duo_input::u1::input::DeviceKind::Mouse));
            config.set_backend(duo_input::protocol::InputBackend::PIO_USB,
                               describe_backend_counters(registry));
            // And what is below all of it: whether the host stack started,
            // whose clock its PIO dividers were computed against, whether the
            // root port is being driven, and whether Core 1 is still turning.
            // Every counter above is a reason a device that enumerated was not
            // read; none of them says anything when nothing enumerates.
            config.set_host_observation(describe_host_observation(g_pio_usb_backend));
        }
#endif

        show_link(link.status().answered);


#if DUO_CH375_PROBE
        report_probe(config);
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
#ifdef DUO_INPUT_BACKEND_CH375
                link.send_release_all(now_ms);
#else
                with_link([&] { link.send_release_all(now_ms); });
#endif
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
