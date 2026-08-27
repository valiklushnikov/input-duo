#include "fakes/scripted_ch375.hpp"

namespace duo_input::u1::ch375::testing {
namespace {

/// How far the clock moves when the code under test polls an empty port.
///
/// Small enough that a deadline is not overshot by much, large enough that a
/// wait of milliseconds does not take millions of iterations to expire.
constexpr std::uint32_t kPollCostUs = 10;

std::string hex(std::uint8_t value) {
    static const char* digits = "0123456789ABCDEF";
    std::string out = "0x";
    out += digits[value >> 4];
    out += digits[value & 0x0F];
    return out;
}

}  // namespace

void ScriptedCh375::note(const char* what, std::uint8_t value) {
    if (!violations_.empty()) {
        violations_ += "; ";
    }
    violations_ += what;
    violations_ += " ";
    violations_ += hex(value);
}

void ScriptedCh375::write_command(std::uint8_t command) {
    if (next_ >= script_.size()) {
        note("unexpected command past the end of the script:", command);
        return;
    }
    const Step& step = script_[next_];
    if (step.kind != Step::Kind::ExpectCommand || step.value != command) {
        note("unexpected command:", command);
        return;
    }
    ++next_;
}

void ScriptedCh375::write_data(std::uint8_t value) {
    if (next_ >= script_.size()) {
        note("unexpected data past the end of the script:", value);
        return;
    }
    const Step& step = script_[next_];
    if (step.kind != Step::Kind::ExpectData || step.value != value) {
        note("unexpected data:", value);
        return;
    }
    ++next_;
}

bool ScriptedCh375::read_data(std::uint8_t& value) {
    if (next_ < script_.size() && script_[next_].kind == Step::Kind::Reply) {
        value = script_[next_].value;
        ++next_;
        return true;
    }
    // Nothing to read. Time passes, which is what lets a bounded wait end.
    now_us_ += kPollCostUs;
    return false;
}

}  // namespace duo_input::u1::ch375::testing
