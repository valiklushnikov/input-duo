// CaptureController on its own, with nothing downstream to hide behind.
//
// The behaviour this file exists for is the one that used to be tested through
// Core1Runtime: a key pressed during a capture is swallowed, and so is its
// release. Measured through the runtime, the second half of that is not
// measured at all - BindingEngine::handle only emits an up-event for a key it
// remembers passing down, so a release capture leaked would be dropped there
// anyway and the test stays green with capture's own bookkeeping removed. The
// behaviour is safe either way; the coverage was not.
//
// Held here, against the controller's own answer, the swallow is the thing
// being asserted.

#include "mapping/capture.hpp"
#include "test_support.hpp"

using duo_input::u1::input::InputEvent;
using duo_input::u1::input::InputEventKind;
using duo_input::u1::mapping::CaptureController;
using duo_input::u1::mapping::CaptureDisposition;
using duo_input::u1::mapping::CapturedTrigger;

namespace {

InputEvent key(InputEventKind kind, std::uint16_t usage) {
    InputEvent event;
    event.kind = kind;
    event.code = usage;
    return event;
}

}  // namespace

TEST_CASE(the_release_of_a_captured_key_is_swallowed_by_capture_itself) {
    CaptureController capture;
    capture.begin(1000);

    CHECK(capture.handle(key(InputEventKind::KeyDown, 0x1A)) == CaptureDisposition::Swallow);

    // The press ended the capture, so by now nothing is running - and the key
    // is still under a finger. The far side was never told it went down, so it
    // must not be told it came up. Nothing downstream can decide this: only
    // capture knows the press was swallowed.
    CHECK(capture.handle(key(InputEventKind::KeyUp, 0x1A)) == CaptureDisposition::Swallow);
}

TEST_CASE(the_release_of_a_captured_modifier_is_swallowed_too) {
    CaptureController capture;
    capture.begin(1000);

    // Left Control. A modifier is not an answer, so the capture is still
    // running after it and the release arrives with the capture live.
    CHECK(capture.handle(key(InputEventKind::KeyDown, 0xE0)) == CaptureDisposition::Swallow);

    CHECK(capture.handle(key(InputEventKind::KeyUp, 0xE0)) == CaptureDisposition::Swallow);
}

TEST_CASE(the_release_of_a_captured_mouse_button_is_swallowed) {
    CaptureController capture;
    capture.begin(1000);

    CHECK(capture.handle(key(InputEventKind::MouseButtonDown, 0)) == CaptureDisposition::Swallow);

    CHECK(capture.handle(key(InputEventKind::MouseButtonUp, 0)) == CaptureDisposition::Swallow);
}

TEST_CASE(a_release_of_something_never_swallowed_goes_through) {
    CaptureController capture;
    capture.begin(1000);
    capture.handle(key(InputEventKind::KeyDown, 0x1A));

    // A different key, held since before the capture. The computer saw it go
    // down and is owed the release; swallowing everything indiscriminately
    // would strand it.
    CHECK(capture.handle(key(InputEventKind::KeyUp, 0x04)) == CaptureDisposition::Pass);
}

TEST_CASE(a_cancelled_capture_still_owes_the_releases_of_what_it_swallowed) {
    CaptureController capture;
    capture.begin(1000);
    capture.handle(key(InputEventKind::KeyDown, 0xE0));

    // The configurator went away mid-question. The key is still down, and the
    // far side still never heard it.
    capture.cancel();

    CHECK(capture.handle(key(InputEventKind::KeyUp, 0xE0)) == CaptureDisposition::Swallow);
}
