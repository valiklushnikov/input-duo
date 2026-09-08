#pragma once

// "Press the key you want to bind."
//
// While that question is on screen the operator's keyboard belongs to the
// configurator, not to the computer. The press they answer with must not reach
// the far side: asking somebody to bind Ctrl+W and closing their browser tab
// while they do it is not an acceptable way to ask a question.
//
// So capture swallows keys and buttons, and passes motion through - the person
// still has to be able to reach the window that is asking. It ends at the
// first real press, or after ten seconds, because a configurator that crashed
// mid-question must not leave a keyboard that silently eats its own input.
//
// Modifiers are not answers. They are recorded against whatever is pressed
// next, since capturing Control the instant it goes down would make Ctrl+W
// impossible to bind at all. The cost is that a bare modifier cannot be a
// trigger, which is the right trade: one that could would fire constantly.

#include <cstddef>
#include <cstdint>

#include "config/format.hpp"
#include "input/events.hpp"
#include "input/source_table.hpp"

namespace duo_input::u1::mapping {

/// How long a capture waits for an answer.
inline constexpr std::uint32_t kCaptureTimeoutMs = 10000;

/// What the operator pressed, in the shape the host expects on the wire.
struct CapturedTrigger {
    config::TriggerKind kind = config::TriggerKind::KEYBOARD_USAGE;
    /// A HID usage, or a mouse button counting from one.
    std::uint8_t code = 0;
    /// Modifiers held at the moment of the press. Always zero for a mouse
    /// button: the host refuses a mouse trigger that carries any, and a
    /// refused payload is a capture the operator has to repeat for nothing.
    std::uint8_t modifiers = 0;
    /// The source that produced the press, resolved through the source table
    /// at the moment it was captured. All zero when the table could not
    /// resolve it - the same "unknown" a caller that never wired one in gets.
    std::uint16_t vendor_id = 0;
    std::uint16_t product_id = 0;
    std::uint8_t interface_number = 0;
};

enum class CaptureDisposition : std::uint8_t {
    /// Not capture's business. Hand it to the bindings.
    Pass,
    /// Consumed. It must not reach the far side.
    Swallow,
};

/// How many swallowed inputs can be down at once and still be remembered.
///
/// Six keys, eight modifiers and five buttons is what a person can physically
/// hold; the capture ends long before anyone gets near it.
inline constexpr std::size_t kMaxSwallowed = 20;

class CaptureController {
public:
    /// Attach on the input core before events arrive. The table must outlive
    /// us. Without one, a captured trigger's source fields stay zero -
    /// exactly what a caller that never wired a table in got before this
    /// existed.
    void set_sources(const input::SourceTable& sources) { sources_ = &sources; }

    void begin(std::uint32_t now_ms, std::uint32_t timeout_ms = kCaptureTimeoutMs);
    void cancel();

    bool active() const { return active_; }

    /// End the capture if nobody answered in time.
    void tick(std::uint32_t now_ms);

    /// Decide what one input event means while a capture may be running.
    CaptureDisposition handle(const input::InputEvent& event);

    /// Take the captured trigger, if there is one waiting.
    bool take(CapturedTrigger& out);

private:
    bool remember(const input::InputEvent& event);
    /// Was this input swallowed on the way down? Forgets it if so.
    bool forget(const input::InputEvent& event);
    /// Resolve the event's source through the table and stamp trigger_ with
    /// it. Leaves the fields zero when there is no table or the table cannot
    /// resolve the index - the same "unknown" the host already reads a
    /// three-byte payload as.
    void fill_source(const input::InputEvent& event);

    const input::SourceTable* sources_ = nullptr;

    bool active_ = false;
    std::uint32_t deadline_ms_ = 0;

    bool have_trigger_ = false;
    CapturedTrigger trigger_{};

    std::uint8_t modifiers_ = 0;

    /// Inputs swallowed while down. Their releases are swallowed too, even
    /// after the capture has ended, because the far side never saw them press.
    input::InputEvent swallowed_[kMaxSwallowed] = {};
    std::size_t swallowed_count_ = 0;
};

}  // namespace duo_input::u1::mapping
