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
#include "input/source_table.hpp"
#include "test_support.hpp"

using duo_input::u1::input::InputEvent;
using duo_input::u1::input::InputEventKind;
using duo_input::u1::input::SourceEvent;
using duo_input::u1::input::SourceEventKind;
using duo_input::u1::input::SourceIdentity;
using duo_input::u1::input::SourceTable;
using duo_input::u1::mapping::CaptureController;
using duo_input::u1::mapping::CaptureDisposition;
using duo_input::u1::mapping::CapturedTrigger;

TEST_CASE(capture_does_not_swallow_another_sources_equal_key_release) {
    for (auto down : {InputEventKind::KeyDown, InputEventKind::MouseButtonDown}) {
        CaptureController capture; capture.begin(0);
        InputEvent event; event.kind = down; event.code = 4; event.source_index = 1;
        CHECK(capture.handle(event) == CaptureDisposition::Swallow);
        event.kind = down == InputEventKind::KeyDown ? InputEventKind::KeyUp : InputEventKind::MouseButtonUp;
        event.source_index = 0;
        CHECK(capture.handle(event) == CaptureDisposition::Pass);
        event.source_index = 1;
        CHECK(capture.handle(event) == CaptureDisposition::Swallow);
    }
}

TEST_CASE(capture_modifiers_belong_to_the_captured_interface) {
    CaptureController capture; capture.begin(0);
    InputEvent event; event.kind = InputEventKind::KeyDown; event.code = 0xE0;
    capture.handle(event);
    event.source_index = 1; capture.handle(event);
    event.kind = InputEventKind::KeyUp; event.source_index = 0; capture.handle(event);
    event.kind = InputEventKind::KeyDown; event.code = 0x4F; event.source_index = 1;
    capture.handle(event);
    CapturedTrigger out; CHECK(capture.take(out)); CHECK(out.modifiers == 1);
    capture.begin(10);
    event.source_index = 2; event.code = 0x50; capture.handle(event);
    CHECK(capture.take(out)); CHECK(out.modifiers == 0);
}

TEST_CASE(consumer_capture_keeps_a_wide_usage_and_suppresses_its_release) {
    CaptureController capture; capture.begin(0);
    InputEvent event; event.kind = InputEventKind::ConsumerDown; event.code = 0x1B1;
    CHECK(capture.handle(event) == CaptureDisposition::Swallow);
    CapturedTrigger out; CHECK(capture.take(out));
    CHECK(static_cast<unsigned>(out.kind) == 3); CHECK(out.code == 0x1B1); CHECK(out.modifiers == 0);
    event.kind = InputEventKind::ConsumerUp;
    CHECK(capture.handle(event) == CaptureDisposition::Swallow);
    CHECK(capture.handle(event) == CaptureDisposition::Pass);
}

namespace {

InputEvent key(InputEventKind kind, std::uint16_t usage) {
    InputEvent event;
    event.kind = kind;
    event.code = usage;
    return event;
}

/// A one-source table, standing in for what SourceTable::on_event would build
/// from a real attach - the same fixture test_binding_engine.cpp uses for the
/// same reason: only SourceTable::resolve's answer matters here, not how a
/// backend fills it in.
struct SourceFixture : duo_input::u1::input::IInputHandler {
    SourceTable sources{*this};
    void on_input(const InputEvent&, std::uint32_t) override {}

    /// Occupies slot 0, since it is the first and only attach.
    void attach(std::uint16_t vid, std::uint16_t pid, std::uint8_t interface_number) {
        SourceEvent event;
        event.kind = SourceEventKind::Ready;
        event.source_id = 1;
        SourceIdentity identity;
        identity.vendor_id = vid;
        identity.product_id = pid;
        identity.interface_number = interface_number;
        sources.on_event(event, identity, 0);
    }
};

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

TEST_CASE(a_captured_trigger_carries_the_source_it_was_pressed_on) {
    SourceFixture fixture;
    fixture.attach(0x3434, 0xD030, 1);

    CaptureController capture;
    capture.set_sources(fixture.sources);
    capture.begin(1000);

    InputEvent press = key(InputEventKind::KeyDown, 0x4F);
    press.source_index = 0;  // the slot attach() just filled
    CHECK(capture.handle(press) == CaptureDisposition::Swallow);

    CapturedTrigger trigger;
    CHECK(capture.take(trigger));
    CHECK_EQ(trigger.vendor_id, 0x3434u);
    CHECK_EQ(trigger.product_id, 0xD030u);
    CHECK_EQ(static_cast<int>(trigger.interface_number), 1);
}

TEST_CASE(a_trigger_from_an_index_the_table_cannot_resolve_carries_no_source) {
    SourceFixture fixture;
    // Nothing attached: slot 0 exists but is not occupied.

    CaptureController capture;
    capture.set_sources(fixture.sources);
    capture.begin(1000);

    InputEvent press = key(InputEventKind::KeyDown, 0x4F);
    press.source_index = 0;
    CHECK(capture.handle(press) == CaptureDisposition::Swallow);

    CapturedTrigger trigger;
    CHECK(capture.take(trigger));
    CHECK_EQ(trigger.vendor_id, 0u);
    CHECK_EQ(trigger.product_id, 0u);
    CHECK_EQ(static_cast<int>(trigger.interface_number), 0);
}

TEST_CASE(a_controller_with_no_table_wired_in_leaves_the_source_zero) {
    // The default a caller who never calls set_sources() gets - exactly what
    // every capture produced before this existed.
    CaptureController capture;
    capture.begin(1000);

    CHECK(capture.handle(key(InputEventKind::MouseButtonDown, 3)) == CaptureDisposition::Swallow);

    CapturedTrigger trigger;
    CHECK(capture.take(trigger));
    CHECK_EQ(trigger.vendor_id, 0u);
    CHECK_EQ(trigger.product_id, 0u);
    CHECK_EQ(static_cast<int>(trigger.interface_number), 0);
}

TEST_CASE(a_second_capture_from_an_unresolvable_source_does_not_keep_the_first_ones) {
    // CaptureController is long-lived - one instance sits inside Core1Runtime
    // for the life of the program - and trigger_ is a member neither begin()
    // nor take() clears. If fill_source only wrote the resolved case, a
    // second capture whose index the table cannot resolve would report
    // whatever source the previous, successful capture last wrote: a binding
    // qualified to the wrong device.
    SourceFixture fixture;
    fixture.attach(0x3434, 0xD030, 1);

    CaptureController capture;
    capture.set_sources(fixture.sources);

    capture.begin(1000);
    InputEvent first = key(InputEventKind::KeyDown, 0x04);
    first.source_index = 0;  // the slot attach() filled
    CHECK(capture.handle(first) == CaptureDisposition::Swallow);
    CapturedTrigger taken;
    CHECK(capture.take(taken));
    CHECK_EQ(taken.vendor_id, 0x3434u);

    capture.begin(2000);
    InputEvent second = key(InputEventKind::KeyDown, 0x05);
    second.source_index = 1;  // nothing attached here
    CHECK(capture.handle(second) == CaptureDisposition::Swallow);

    CapturedTrigger trigger;
    CHECK(capture.take(trigger));
    CHECK_EQ(trigger.vendor_id, 0u);
    CHECK_EQ(trigger.product_id, 0u);
    CHECK_EQ(static_cast<int>(trigger.interface_number), 0);
}
