#pragma once

// A CH375 that exists only as a written-down conversation.
//
// The transport layer is told what to say and what to expect back, in order.
// Anything the code under test sends that the script did not expect is
// recorded rather than ignored, because a port out of step with this chip is
// the failure that matters: the CH375 answers commands positionally, so one
// extra or missing byte turns every later reply into plausible nonsense.
//
// The clock only moves when the code under test looks for a byte that has not
// arrived. That makes a timeout deterministic - it takes exactly as many polls
// as the deadline allows - and it makes a test that would spin forever finish
// and fail instead.

#include <cstddef>
#include <cstdint>
#include <initializer_list>
#include <string>
#include <vector>

#include "ch375/commands.hpp"
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

class ScriptedCh375 final : public ICh375Transport {
public:
    ScriptedCh375(std::initializer_list<Step> script) : script_(script) {}

    // --- the port the transport is written against -------------------------

    void write_command(std::uint8_t command) override;
    void write_data(std::uint8_t value) override;
    bool read_data(std::uint8_t& value) override;
    bool int_asserted() const override { return int_asserted_; }
    std::uint32_t now_us() const override { return now_us_; }

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
    bool int_asserted_ = false;
    std::uint32_t start_us_ = 1000;
    std::uint32_t now_us_ = 1000;
};

}  // namespace duo_input::u1::ch375::testing
