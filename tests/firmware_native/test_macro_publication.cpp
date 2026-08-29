// What both computers receive when one macro types, on clocks that are not
// the test's own.
//
// test_macro_reports.cpp asks whether a macro types at all. It answers that
// with a rig in which Core 0 makes exactly one pass per millisecond and every
// report it hands to the endpoint is taken. Real hardware gives neither: Core
// 0's pass is whatever USB, the CDC service, a drain, a publish and a 64-byte
// SPI frame add up to, and the host collects the keyboard endpoint on its own
// frame grid, which drifts against the device's millisecond counter. A macro
// that types correctly at one pass per millisecond can lose most of its
// letters at another rate, and lose *different* letters on each computer,
// because PC1 is fed by UsbService::publish and PC2 by SpiMaster::poll and the
// two are busy at different moments.
//
// So this rig runs both cores on a microsecond timeline with those periods as
// parameters, and sweeps them. Core 1 is given a turn between one pop and the
// next, because that is what a second core does: the command queue is empty
// from the instant a command is popped, not from the instant the state it
// produced is published, and drain's own loop re-checks the queue in between.
//
// The property under test is not "some text arrived". It is that every state
// the macro produced is announced to both computers - which is the same thing
// as saying every press is matched by its release on both, and that is the
// hazard this project is built around: a key stranded down on a computer the
// operator is not watching.

#include "core1_runtime.hpp"
#include "fakes/spi_link.hpp"
#include "fakes/usb_host.hpp"
#include "link/spi_protocol.hpp"
#include "output_runtime.hpp"
#include "spi_master.hpp"
#include "test_support.hpp"
#include "usb_service.hpp"

#include <cstdio>
#include <string>
#include <vector>

using duo_input::config::KeyboardRoute;
using duo_input::config::MacroStepType;
using duo_input::hid::KeyboardSnapshot;
using duo_input::hid::Target;
using duo_input::runtime::OutputCommand;
using duo_input::u1::Core1Runtime;
using duo_input::u1::ICommandSink;
using duo_input::u1::IProfileSource;
using duo_input::u1::OutputRuntime;
using duo_input::u1::SpiMaster;
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
};

/// Core 1's only reach into the output, as main.cpp defines it.
struct QueuedCommands final : ICommandSink {
    OutputRuntime& outputs;

    explicit QueuedCommands(OutputRuntime& runtime) : outputs(runtime) {}

    bool submit(const OutputCommand& command) override { return outputs.submit(command); }
    std::size_t pending() const override { return outputs.pending(); }
};

/// ``PROFILE-3 `` as the text compiler lays it out: modifier, usage, repeated.
///
/// The acceptance macro, character for character. Seven of its ten characters
/// are a shift held across a keystroke, so it owes thirty-four edges, and the
/// difference between a lost press and a lost release is visible in the text.
const std::uint8_t kProfile3Pairs[20] = {
    0x02, 0x13,  // P
    0x02, 0x15,  // R
    0x02, 0x12,  // O
    0x02, 0x09,  // F
    0x02, 0x0C,  // I
    0x02, 0x0F,  // L
    0x02, 0x08,  // E
    0x00, 0x2D,  // -
    0x00, 0x20,  // 3
    0x00, 0x2C,  // space
};

/// The trailing space is written ``_`` so a lost one is visible in a failure.
const char* const kWantedText = "PROFILE-3_";

char letter_of(std::uint8_t usage, bool shifted) {
    switch (usage) {
        case 0x13: return shifted ? 'P' : 'p';
        case 0x15: return shifted ? 'R' : 'r';
        case 0x12: return shifted ? 'O' : 'o';
        case 0x09: return shifted ? 'F' : 'f';
        case 0x0C: return shifted ? 'I' : 'i';
        case 0x0F: return shifted ? 'L' : 'l';
        case 0x08: return shifted ? 'E' : 'e';
        case 0x2D: return '-';
        case 0x20: return '3';
        case 0x2C: return '_';
        default: return '?';
    }
}

/// One computer's view: the sequence of keyboard states it was actually told.
///
/// PC1's comes out of the USB endpoint; PC2's out of the KBD_STATE frames,
/// which U2 applies verbatim - it releases the target and re-presses what the
/// frame names - so the frames are PC2's report sequence.
struct Received {
    std::vector<KeyboardSnapshot> states;

    /// What a person watching that computer would see typed.
    std::string typed() const {
        std::string text;
        KeyboardSnapshot previous;
        for (const KeyboardSnapshot& state : states) {
            for (std::uint8_t index = 0; index < state.key_count; ++index) {
                bool already_down = false;
                for (std::uint8_t before = 0; before < previous.key_count; ++before) {
                    if (previous.keys[before] == state.keys[index]) {
                        already_down = true;
                    }
                }
                if (!already_down) {
                    text.push_back(
                        letter_of(state.keys[index], (state.modifiers & 0x22) != 0));
                }
            }
            previous = state;
        }
        return text;
    }

    /// Did anything this computer was told to press never come back up?
    ///
    /// The states are absolute, so this is simply whether the last one is
    /// empty - a key or a modifier still named there is a key still held on a
    /// computer whose macro finished.
    bool everything_released() const {
        if (states.empty()) {
            return true;
        }
        return states.back().key_count == 0 && states.back().modifiers == 0;
    }
};

/// How fast each part of the board runs, in microseconds.
struct Timing {
    /// One turn round Core 0's loop, without the SPI frame.
    unsigned core0_period_us;
    /// One turn round Core 1's loop - two controller ticks and the runtime.
    unsigned core1_period_us;
    /// Where the host's frame grid sits against the device's millisecond count.
    unsigned host_phase_us;
};

/// The host polls the keyboard endpoint once per frame; the descriptor asks
/// for one millisecond and a full-speed host obliges.
constexpr unsigned kHostFrameUs = 1000;

/// A 64-byte frame at 1 MHz, charged to the Core 0 pass that sends it.
constexpr unsigned kSpiFrameUs = 512;

/// One pop and the command it applies. The window in which the other core can
/// slip the next edge in behind it.
constexpr unsigned kPopUs = 2;

struct Board {
    OutputRuntime outputs;
    QueuedCommands commands{outputs};
    NoProfiles profiles;
    Core1Runtime core1{commands, profiles};
    UsbService usb;
    SpiMaster link;
    Timing timing;

    Received pc1;
    Received pc2;

    explicit Board(Timing chosen) : timing(chosen) {
        duo::test::usb_host().reset();
        duo::test::spi_link().reset();
    }

    /// The macro's steps outlive the call that installs them: a definition is
    /// a pointer into the step pool, and Core 1 reads it every pass.
    MacroStep steps[1];

    void type_the_acceptance_macro() {
        steps[0].kind = MacroStepType::TEXT;
        steps[0].pairs = kProfile3Pairs;
        steps[0].pair_bytes = sizeof(kProfile3Pairs);
        core1.define_macro(0, MacroDefinition{steps, 1});
        // Both computers, which is where the acceptance leaves the route and
        // the only setting in which the two paths can be compared at all.
        (void)core1.engine().set_keyboard_route(KeyboardRoute::BOTH);
        CHECK(core1.run_macro(0, 1000));
    }

    /// Run both cores for this long, each at its own rate.
    void run_us(unsigned long long duration_us) {
        unsigned long long now_us = 1'000'000;
        const unsigned long long end_us = now_us + duration_us;
        unsigned long long next_core0 = now_us;
        unsigned long long next_core1 = now_us;
        unsigned long long endpoint_free_at = now_us;

        while (now_us < end_us) {
            now_us = next_core0 < next_core1 ? next_core0 : next_core1;

            if (now_us >= next_core1) {
                core1.tick(static_cast<std::uint32_t>(now_us / 1000));
                next_core1 = now_us + timing.core1_period_us;
            }
            if (now_us < next_core0) {
                continue;
            }

            duo::test::usb_host().keyboard_ready = now_us >= endpoint_free_at;
            const std::size_t before_reports = duo::test::usb_host().keyboard_count;
            const std::size_t before_frames = duo::test::spi_link().count;

            // Core 1 is on the other core, so it gets its turns between one
            // pop and the next rather than only between whole passes.
            while (outputs.pending() != 0) {
                const std::size_t applied = outputs.drain(1);
                now_us += kPopUs;
                if (now_us >= next_core1) {
                    core1.tick(static_cast<std::uint32_t>(now_us / 1000));
                    next_core1 = now_us + timing.core1_period_us;
                }
                if (applied == 0) {
                    // Nothing was applied, so the loop inside drain would have
                    // ended here too. Going round again would spin the clock,
                    // not the queue.
                    break;
                }
            }

            const std::uint32_t now_ms = static_cast<std::uint32_t>(now_us / 1000);
            usb.publish(outputs);
            link.poll(now_ms, outputs);

            unsigned cost = timing.core0_period_us;
            if (duo::test::usb_host().keyboard_count != before_reports) {
                // Handed to the endpoint now; the host collects it at its next
                // frame boundary, and only then is the endpoint free again.
                const unsigned long long since = now_us - timing.host_phase_us;
                endpoint_free_at =
                    (since / kHostFrameUs + 1) * kHostFrameUs + timing.host_phase_us;
                for (std::size_t index = before_reports;
                     index < duo::test::usb_host().keyboard_count; ++index) {
                    pc1.states.push_back(duo::test::usb_host().keyboard[index]);
                }
            }
            for (std::size_t index = before_frames; index < duo::test::spi_link().count;
                 ++index) {
                if (duo::test::spi_link().type[index] !=
                    duo_input::protocol::SpiMessageType::KBD_STATE) {
                    continue;
                }
                KeyboardSnapshot state;
                if (duo_input::link::decode_keyboard_state(
                        duo_input::protocol::ByteView{
                            duo::test::spi_link().payload[index],
                            duo::test::spi_link().payload_size[index]},
                        state)) {
                    pc2.states.push_back(state);
                }
            }
            if (duo::test::spi_link().count != before_frames) {
                cost += kSpiFrameUs;
            }
            next_core0 = now_us + cost;
        }
    }
};

/// The timings the sweep covers, chosen to bracket the hardware rather than to
/// be tidy: a Core 0 pass from well under the millisecond the macro is paced
/// at to well over it, a Core 1 pass from a spin to a slow controller round,
/// and the host's frame grid at four places against the device's clock.
struct Named {
    const char* label;
    Timing timing;
};

const Named kSweep[] = {
    {"fast loop, aligned host", Timing{80, 30, 0}},
    {"fast loop, host at 300us", Timing{120, 30, 300}},
    {"half-millisecond loop", Timing{400, 30, 300}},
    {"half-millisecond loop, fast core 1", Timing{550, 5, 0}},
    {"half-millisecond loop, slow core 1", Timing{550, 30, 0}},
    {"loop as long as the pacing", Timing{1000, 5, 0}},
    {"loop as long as the pacing, slow core 1", Timing{1000, 60, 500}},
    {"loop longer than the pacing", Timing{1500, 20, 750}},
    {"slow loop", Timing{2500, 60, 250}},
};

void check_one(const Named& entry) {
    Board board(entry.timing);
    board.type_the_acceptance_macro();
    // Generous: thirty-four edges paced at one a millisecond, in a fifth of a
    // second, and the slowest loop in the sweep still gets eighty passes.
    board.run_us(200'000);

    const std::string on_pc1 = board.pc1.typed();
    const std::string on_pc2 = board.pc2.typed();
    if (on_pc1 != kWantedText || on_pc2 != kWantedText ||
        !board.pc1.everything_released() || !board.pc2.everything_released()) {
        std::printf("  %-40s PC1=\"%s\" PC2=\"%s\"\n", entry.label, on_pc1.c_str(),
                    on_pc2.c_str());
    }
    CHECK(on_pc1 == kWantedText);
    CHECK(on_pc2 == kWantedText);
    // The defining hazard, on both computers: nothing the macro pressed may
    // still be down once it has finished.
    CHECK(board.pc1.everything_released());
    CHECK(board.pc2.everything_released());
    CHECK_EQ(board.outputs.snapshot(Target::Pc1).keyboard.key_count, 0u);
    CHECK_EQ(board.outputs.snapshot(Target::Pc2).keyboard.key_count, 0u);
    // Nothing was refused; whatever went wrong was not the queue.
    CHECK_EQ(board.core1.dropped_commands(), 0u);
}

}  // namespace

TEST_CASE(the_acceptance_macro_types_its_whole_text_on_pc1_at_every_rate) {
    for (const Named& entry : kSweep) {
        Board board(entry.timing);
        board.type_the_acceptance_macro();
        board.run_us(200'000);
        const std::string typed = board.pc1.typed();
        if (typed != kWantedText) {
            std::printf("  PC1 at %-40s typed \"%s\"\n", entry.label, typed.c_str());
        }
        CHECK(typed == kWantedText);
    }
}

TEST_CASE(the_acceptance_macro_types_its_whole_text_on_pc2_at_every_rate) {
    for (const Named& entry : kSweep) {
        Board board(entry.timing);
        board.type_the_acceptance_macro();
        board.run_us(200'000);
        const std::string typed = board.pc2.typed();
        if (typed != kWantedText) {
            std::printf("  PC2 at %-40s typed \"%s\"\n", entry.label, typed.c_str());
        }
        CHECK(typed == kWantedText);
    }
}

TEST_CASE(both_computers_receive_the_same_text_from_the_same_command_stream) {
    // The sharpest of the three. One command stream cannot legitimately
    // produce two different results, and the hardware failure was exactly
    // that: PC1 short of most of its letters, PC2 with a different wrong
    // answer of its own.
    for (const Named& entry : kSweep) {
        Board board(entry.timing);
        board.type_the_acceptance_macro();
        board.run_us(200'000);
        const std::string on_pc1 = board.pc1.typed();
        const std::string on_pc2 = board.pc2.typed();
        if (on_pc1 != on_pc2) {
            std::printf("  %-40s PC1=\"%s\" PC2=\"%s\"\n", entry.label, on_pc1.c_str(),
                        on_pc2.c_str());
        }
        CHECK(on_pc1 == on_pc2);
    }
}

TEST_CASE(every_key_a_macro_presses_comes_back_up_on_both_computers) {
    for (const Named& entry : kSweep) {
        Board board(entry.timing);
        board.type_the_acceptance_macro();
        board.run_us(200'000);
        if (!board.pc1.everything_released() || !board.pc2.everything_released()) {
            std::printf("  %-40s left something held\n", entry.label);
        }
        CHECK(board.pc1.everything_released());
        CHECK(board.pc2.everything_released());
        CHECK_EQ(board.outputs.snapshot(Target::Pc1).keyboard.key_count, 0u);
        CHECK_EQ(board.outputs.snapshot(Target::Pc2).keyboard.key_count, 0u);
    }
}

TEST_CASE(the_whole_acceptance_macro_survives_every_rate_in_the_sweep) {
    for (const Named& entry : kSweep) {
        check_one(entry);
    }
}
