#include "test_pattern.hpp"

namespace duo_input::u1 {
namespace {

using runtime::CommandKind;
using runtime::OutputCommand;

constexpr std::uint32_t kKeyIntervalMs = 1000;
constexpr std::uint32_t kMoveIntervalMs = 20;

// The four sides of the square, in order.
constexpr std::int16_t kMoveX[] = {kTestPatternStep, 0, static_cast<std::int16_t>(-kTestPatternStep), 0};
constexpr std::int16_t kMoveY[] = {0, kTestPatternStep, 0, static_cast<std::int16_t>(-kTestPatternStep)};

std::uint32_t next_key_ms = 0;
std::uint32_t next_move_ms = 0;
bool key_is_down = false;
std::uint8_t side = 0;

OutputCommand key_command(bool pressed) {
    OutputCommand command;
    command.kind = pressed ? CommandKind::KeyPress : CommandKind::KeyRelease;
    command.route = DUO_TEST_PATTERN_ROUTE;
    command.code = kTestPatternUsage;
    return command;
}

}  // namespace

void advance_test_pattern(OutputRuntime& runtime, std::uint32_t now_ms) {
    if (static_cast<std::int32_t>(now_ms - next_key_ms) >= 0) {
        // Tap rather than hold: a generated key that stays down would be
        // indistinguishable from the stuck-key fault this whole design exists
        // to avoid.
        key_is_down = !key_is_down;
        runtime.submit(key_command(key_is_down));
        next_key_ms = now_ms + (key_is_down ? 50 : kKeyIntervalMs);
    }

    if (static_cast<std::int32_t>(now_ms - next_move_ms) >= 0) {
        OutputCommand move;
        move.kind = CommandKind::MouseDelta;
        move.route = DUO_TEST_PATTERN_ROUTE;
        move.delta_x = kMoveX[side];
        move.delta_y = kMoveY[side];
        runtime.submit(move);
        side = static_cast<std::uint8_t>((side + 1) & 3);
        next_move_ms = now_ms + kMoveIntervalMs;
    }
}

}  // namespace duo_input::u1
