#pragma once

// Two fake CH375s: one that recites, one that behaves.
//
// ScriptedCh375 exists only as a written-down conversation, and is what the
// transport tests need - they are about the exact bytes of each command, in
// order. Ch375Device cannot be tested that way: it decides what to ask next
// from the state it is in, so a fixed script would be asserting the state
// machine's shape rather than its behaviour. FakeCh375Chip answers the way a
// chip does, whatever order it is asked in, and can be told to misbehave.
//
// In both, the clock moves only when the code under test looks for a byte that
// has not arrived. That makes a timeout deterministic - it takes exactly as
// many polls as the deadline allows - and it makes a test that would otherwise
// spin forever finish and fail instead.
//
// ScriptedCh375 records anything sent that the script did not expect rather
// than ignoring it, because a port out of step with this chip is the failure
// that matters: the CH375 answers commands positionally, so one extra or
// missing byte turns every later reply into plausible nonsense.

#include <cstddef>
#include <cstdint>
#include <initializer_list>
#include <string>
#include <vector>

#include "ch375/commands.hpp"
#include "ch375/device.hpp"
#include "ch375/transport.hpp"

namespace duo_input::u1::ch375::testing {

struct Step {
    enum class Kind : std::uint8_t {
        ExpectCommand,
        ExpectData,
        Reply,
    };

    Kind kind = Kind::Reply;
    std::uint8_t value = 0;
};

inline Step expect_command(Ch375Command command) {
    return Step{Step::Kind::ExpectCommand, static_cast<std::uint8_t>(command)};
}

inline Step expect_data(std::uint8_t value) {
    return Step{Step::Kind::ExpectData, value};
}

inline Step reply(std::uint8_t value) {
    return Step{Step::Kind::Reply, value};
}

/// The rate a CH375 comes up at, and goes back to after a reset.
inline constexpr unsigned kScriptedDefaultBaud = 9600;

class ScriptedCh375 final : public ICh375Transport {
public:
    ScriptedCh375(std::initializer_list<Step> script) : script_(script) {}

    // --- the port the transport is written against -------------------------

    void write_command(std::uint8_t command) override;
    void write_data(std::uint8_t value) override;
    bool read_data(std::uint8_t& value) override;
    bool int_asserted() const override { return int_asserted_; }
    std::uint32_t now_us() const override { return now_us_; }

    bool set_baud(unsigned baud) override {
        if (refuse_baud_) {
            return false;
        }
        baud_ = baud;
        ++baud_changes_;
        return true;
    }

    /// Stand in for a port with a fixed rate.
    void refuse_baud_changes() { refuse_baud_ = true; }

    /// Accept anything and answer nothing - a chip that is simply not there.
    void allow_unscripted() { unscripted_ = true; }

    /// Was a chip reset sent while the port was at this rate?
    ///
    /// The question that matters when a rate is abandoned: a chip that moved
    /// has to be told to reset while it can still hear.
    bool saw_reset_at(unsigned baud) const {
        for (unsigned at : reset_baud_) {
            if (at == baud) {
                return true;
            }
        }
        return false;
    }

    unsigned baud() const { return baud_; }
    int baud_changes() const { return baud_changes_; }

    // --- what the test drives and asks ------------------------------------

    /// Every step consumed, in order, with nothing unexpected on the wire.
    bool complete() const { return next_ == script_.size() && violations_.empty(); }

    /// What went wrong, for a failing test to be readable.
    const std::string& violations() const { return violations_; }

    void assert_int(bool asserted) { int_asserted_ = asserted; }

    /// Move the clock without anyone polling, for setting a scene.
    void advance(std::uint32_t micros) { now_us_ += micros; }

    std::uint32_t elapsed_us() const { return now_us_ - start_us_; }

private:
    void note(const char* what, std::uint8_t value);

    std::vector<Step> script_;
    std::size_t next_ = 0;
    std::string violations_;
    unsigned baud_ = kScriptedDefaultBaud;
    bool refuse_baud_ = false;
    bool unscripted_ = false;
    std::vector<unsigned> reset_baud_;
    int baud_changes_ = 0;
    bool int_asserted_ = false;
    std::uint32_t start_us_ = 1000;
    std::uint32_t now_us_ = 1000;
};

/// A CH375 that behaves like one, for testing a state machine against.
///
/// It models what the lifecycle depends on and nothing else: the interrupt
/// line, the pending status byte, the working mode, a device that can be
/// plugged in and pulled out, and reports waiting to be read. It can also be
/// told to answer nonsense or to stop answering, because those are the cases
/// the firmware exists to survive.
class FakeCh375Chip final : public ICh375Transport {
public:
    // --- the port ----------------------------------------------------------

    void write_command(std::uint8_t command) override;
    void write_data(std::uint8_t value) override;
    bool read_data(std::uint8_t& value) override;
    bool int_asserted() const override;
    std::uint32_t now_us() const override { return now_us_; }

    // --- the scene ---------------------------------------------------------

    void attach_device();
    void detach_device();

    /// Give the device something to say the next time it is polled.
    void queue_report(const std::uint8_t* data, std::size_t size);

    /// Was the chip asked to configure the device by itself?
    bool saw_auto_setup() const { return saw_auto_setup_; }

    // --- a device on the far side of the bus -------------------------------
    //
    // Enough of one to be enumerated: it answers descriptor requests, takes an
    // address, and remembers which configuration was chosen. What the tests
    // are about is the order those happen in and whether the host keeps up
    // with the address it handed out.

    /// Serve the descriptors of an ordinary boot mouse on endpoint 2.
    void serve_boot_mouse();
    /// A composite whose first HID interface is consumer controls.
    void serve_composite_keyboard();
    /// A hub, which this firmware does not support.
    void serve_hub();

    /// Refuse everything after this many control transfers have succeeded.
    void stall_after(int transfers) { stall_after_ = transfers; }

    std::uint8_t device_address() const { return device_address_; }
    std::uint8_t host_address() const { return host_address_; }
    std::uint8_t configuration_value() const { return configuration_value_; }

    /// Was every step taken before the step that depends on it?
    bool order_was_correct() const { return order_ok_; }

    /// Make the attached device answer as a 1.5 Mbps one.
    void set_low_speed(bool low) { low_speed_ = low; }

    /// What the bus was last set to.
    UsbSpeed bus_speed() const { return bus_speed_; }

    /// Was the speed set after the most recent working mode?
    ///
    /// Setting a mode puts the bus back to full speed (DS2 1.1), so a speed
    /// chosen before it is quietly undone.
    bool speed_set_after_last_mode() const { return speed_after_mode_; }

    /// Report a disconnect the moment the USB bus is held in reset, which is
    /// what the real chip does with a device attached.
    void report_disconnect_on_reset(bool reporting) { report_disconnect_on_reset_ = reporting; }

    /// Keep reporting it once frames are switched on, which is what the chip
    /// does while a device is still coming back after the reset.
    void report_disconnect_while_settling(bool reporting) {
        report_disconnect_settling_ = reporting;
    }

    /// Refuse to configure, as a device the chip cannot talk to would cause.
    void fail_auto_setup(bool failing) { fail_auto_setup_ = failing; }

    /// Answer every command with a byte that means nothing.
    void answer_garbage(bool broken) { garbage_ = broken; }

    /// Stop answering at all, as a chip with a broken port would.
    void go_silent(bool silent) { silent_ = silent; }

    void advance(std::uint32_t micros) { now_us_ += micros; }

    UsbMode mode() const { return mode_; }
    bool saw_bus_reset() const { return saw_bus_reset_; }
    /// How many times the USB bus has been reset - one per setup attempt.
    std::uint32_t reset_count() const { return reset_count_; }

    /// How many times a working mode has been asked for.
    ///
    /// This is what counts attempts when the chip is misbehaving: a controller
    /// that refuses host mode never gets as far as resetting the bus, so
    /// reset_count would sit at zero however many times it was retried.
    std::uint32_t mode_set_count() const { return mode_set_count_; }

    std::uint32_t command_count() const { return command_count_; }
    void reset_command_count() { command_count_ = 0; }

private:
    void queue(std::uint8_t value);

    std::uint8_t pending_command_ = 0;
    bool expecting_data_ = false;

    std::vector<std::uint8_t> outgoing_;
    std::size_t outgoing_read_ = 0;

    std::vector<std::uint8_t> report_;
    bool report_waiting_ = false;

    UsbMode mode_ = UsbMode::DeviceDisabled;
    bool attached_ = false;
    bool int_asserted_ = false;
    std::uint8_t pending_status_ = 0;

    bool garbage_ = false;
    bool silent_ = false;
    bool saw_bus_reset_ = false;
    bool saw_auto_setup_ = false;
    bool fail_auto_setup_ = false;
    bool report_disconnect_on_reset_ = false;
    bool report_disconnect_settling_ = false;
    bool low_speed_ = false;
    std::vector<std::uint8_t> configuration_;
    bool read_device_descriptor_ = false;
    bool read_configuration_ = false;
    std::uint8_t device_address_ = 0;
    std::uint8_t host_address_ = 0;
    std::uint8_t configuration_value_ = 0;
    bool order_ok_ = true;
    int stall_after_ = -1;
    int transfers_done_ = 0;
    /// What the next RD_USB_DATA0 will hand back.
    std::vector<std::uint8_t> pending_read_;
    void finish_transfer(bool stalled);
    UsbSpeed bus_speed_ = UsbSpeed::Full12Mbps;
    bool speed_after_mode_ = false;
    /// AUTO_SETUP is several control transfers, so its answer is not instant.
    bool auto_setup_running_ = false;
    std::uint32_t auto_setup_at_us_ = 0;
    std::uint32_t reset_count_ = 0;
    std::uint32_t mode_set_count_ = 0;
    std::uint32_t command_count_ = 0;
    std::uint32_t now_us_ = 1000;
};

/// Configuring the attached device, faked.
///
/// Task 3 replaces this with real enumeration and descriptor parsing. What the
/// lifecycle needs from it now is only that it takes more than one tick, can
/// fail, and eventually names an endpoint to poll.
class FakeDeviceSetup final : public IDeviceSetup {
public:
    void begin(std::uint32_t now_us) override;
    SetupProgress poll(std::uint32_t now_us, bool interrupted, InterruptStatus status) override;
    std::uint8_t interrupt_endpoint() const override { return 1; }

    void always_fail(bool failing) { failing_ = failing; }

    /// Has anyone asked this to start yet?
    bool was_begun() const { return begun_; }

private:
    std::uint32_t started_us_ = 0;
    bool running_ = false;
    bool failing_ = false;
    bool begun_ = false;
};

}  // namespace duo_input::u1::ch375::testing
