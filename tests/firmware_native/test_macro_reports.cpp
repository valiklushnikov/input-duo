// What the far computer actually receives when a macro types.
//
// Every other macro test in this tree stops at the OutputCommand, and that is
// exactly where the defect this file exists for was invisible. Core 0 does not
// hold a queue of reports; it holds *state*. A press and the release that
// follows it, applied inside one drain, leave that state identical to what was
// last sent - so UsbService::publish sends nothing, and the letter is never
// typed. A test that counts commands sees four keystrokes and passes. This one
// counts reports.
//
// So the rig is both loops, wired the way main.cpp wires them: Core 1 iterating
// many times for each pass Core 0 makes, because that is the ratio on the
// hardware - Core 1's pass is two controller ticks, Core 0's is USB, the CDC
// service, a drain, a publish and an SPI transaction.

#include "core1_runtime.hpp"
#include "fakes/usb_host.hpp"
#include "output_runtime.hpp"
#include "test_support.hpp"
#include "usb_service.hpp"

#include <vector>

using duo_input::config::MacroStepType;
using duo_input::hid::Target;
using duo_input::runtime::OutputCommand;
using duo_input::runtime::RuntimeFault;
using duo_input::u1::Core1Runtime;
using duo_input::u1::ICommandSink;
using duo_input::u1::IProfileSource;
using duo_input::u1::OutputRuntime;
using duo_input::u1::UsbService;
using duo_input::u1::macros::MacroDefinition;
using duo_input::u1::macros::MacroStep;
using duo_input::u1::mapping::Binding;

namespace {

/// No bindings. This is about what a macro emits, not what starts it.
struct NoProfiles final : IProfileSource {
    std::size_t bindings_for(std::uint8_t profile, Binding* out) const override {
        (void)profile;
        (void)out;
        return 0;
    }

    /// No profile, so no stored routes, so the runtime keeps the ones it has.
    bool routes_for(std::uint8_t profile, duo_input::config::KeyboardRoute& keyboard,
                    duo_input::config::MouseRoute& mouse) const override {
        (void)profile;
        (void)keyboard;
        (void)mouse;
        return false;
    }
};

/// Core 1's only reach into the output, as main.cpp defines it.
struct QueuedCommands final : ICommandSink {
    OutputRuntime& outputs;

    explicit QueuedCommands(OutputRuntime& runtime) : outputs(runtime) {}

    bool submit(const OutputCommand& command) override { return outputs.submit(command); }
    std::size_t pending() const override { return outputs.pending(); }
};

/// Both cores, and the millisecond that separates one Core 0 pass from the next.
struct Board {
    OutputRuntime outputs;
    QueuedCommands commands{outputs};
    NoProfiles profiles;
    Core1Runtime core1{commands, profiles};
    UsbService usb;
    std::uint32_t now_ms = 1000;

    /// How many times Core 1 goes round for each pass Core 0 makes.
    ///
    /// Any number above one reproduces the defect; on the hardware it is far
    /// more than eight.
    static constexpr int kCore1PassesPerCore0Pass = 8;

    Board() { duo::test::usb_host().reset(); }

    void pass() {
        for (int index = 0; index < kCore1PassesPerCore0Pass; ++index) {
            core1.tick(now_ms);
        }
        // The clock the drain needs is the one that decides how long it may
        // wait for a computer that has stopped answering; publish is what
        // answers for PC1.
        outputs.drain(now_ms, now_ms * 1000);
        usb.publish(outputs);
        ++now_ms;
    }

    void run(int passes) {
        for (int index = 0; index < passes; ++index) {
            pass();
        }
    }
};

/// A TEXT step spelling four letters, with no modifiers on any of them.
struct FourLetters {
    std::uint8_t pairs[8] = {0, 0x04, 0, 0x05, 0, 0x06, 0, 0x07};
    MacroStep steps[1];

    FourLetters() {
        steps[0].kind = MacroStepType::TEXT;
        steps[0].pairs = pairs;
        steps[0].pair_bytes = sizeof(pairs);
    }

    MacroDefinition definition() const { return MacroDefinition{steps, 1}; }
};

}  // namespace

TEST_CASE(a_text_macro_types_every_one_of_its_letters_on_the_far_computer) {
    Board board;
    FourLetters text;
    board.core1.define_macro(0, text.definition());
    CHECK(board.core1.run_macro(0, board.now_ms));

    // Generous: the macro owes eight edges and the rig gives it fifty passes.
    board.run(50);

    const duo::test::UsbHost& host = duo::test::usb_host();

    // Four letters, four keystrokes. Counting OutputCommands here would have
    // reported eight and been wrong about all of them.
    CHECK(host.typed(0x04));
    CHECK(host.typed(0x05));
    CHECK(host.typed(0x06));
    CHECK(host.typed(0x07));

    // And each of them went down and came back up, in order, in reports of its
    // own - which is what the far computer needs to register a keystroke.
    std::vector<std::uint8_t> down;
    for (std::size_t index = 0; index < host.keyboard_count; ++index) {
        const duo_input::hid::KeyboardSnapshot& report = host.keyboard[index];
        CHECK(report.key_count <= 1u);
        if (report.key_count == 1) {
            down.push_back(report.keys[0]);
        }
    }
    CHECK_EQ(down.size(), 4u);
    if (down.size() == 4) {
        CHECK_EQ(down[0], 0x04);
        CHECK_EQ(down[1], 0x05);
        CHECK_EQ(down[2], 0x06);
        CHECK_EQ(down[3], 0x07);
    }

    // Nothing is left held once it is finished.
    CHECK_EQ(board.outputs.snapshot(Target::Pc1).keyboard.key_count, 0u);
}

TEST_CASE(a_text_macro_never_fills_the_queue_it_shares_with_the_other_core) {
    Board board;
    // A thousand letters is well inside what one TEXT step may carry, and it
    // owes two thousand edges. Unpaced, they arrive faster than Core 0 drains
    // and the 127-slot queue overflows within a millisecond of the macro
    // starting - which used to release everything on both computers and refuse
    // input from then on.
    std::vector<std::uint8_t> pairs(2000, 0);
    for (std::size_t index = 1; index < pairs.size(); index += 2) {
        pairs[index] = 0x04;
    }
    MacroStep text;
    text.kind = MacroStepType::TEXT;
    text.pairs = pairs.data();
    text.pair_bytes = static_cast<std::uint16_t>(pairs.size());
    const MacroStep steps[] = {text};
    board.core1.define_macro(0, MacroDefinition{steps, 1});
    CHECK(board.core1.run_macro(0, board.now_ms));

    board.run(200);

    CHECK_EQ(board.core1.dropped_commands(), 0u);
    CHECK_EQ(board.outputs.fault(), RuntimeFault::None);
    CHECK(board.core1.macro_active());
}

TEST_CASE(a_macro_that_types_the_same_letter_twice_sends_two_keystrokes) {
    Board board;
    // The case a report-counting host cannot fake its way through: the state
    // after the second press is identical to the state after the first, so the
    // only thing that makes two keystrokes out of it is the release between
    // them reaching the host in a report of its own.
    std::uint8_t pairs[4] = {0, 0x04, 0, 0x04};
    MacroStep text;
    text.kind = MacroStepType::TEXT;
    text.pairs = pairs;
    text.pair_bytes = sizeof(pairs);
    const MacroStep steps[] = {text};
    board.core1.define_macro(0, MacroDefinition{steps, 1});
    CHECK(board.core1.run_macro(0, board.now_ms));

    board.run(30);

    const duo::test::UsbHost& host = duo::test::usb_host();
    int presses = 0;
    int releases = 0;
    for (std::size_t index = 0; index < host.keyboard_count; ++index) {
        if (host.keyboard[index].contains(0x04)) {
            ++presses;
        } else {
            ++releases;
        }
    }
    CHECK_EQ(presses, 2);
    CHECK_EQ(releases, 2);
}
