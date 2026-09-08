#include "input/pipeline.hpp"

namespace duo_input::u1::input {
void InputPipeline::set_kind(DeviceKind kind, const hid::KeyboardReportLayout& keyboard_layout,
                             const hid::MouseReportLayout& mouse_layout) {
    kind_ = kind;
    // Both set on every Ready, not only when a descriptor was read: a device
    // that would not describe itself hands over boot protocol's layout, and
    // setting it is what stops the device before it from being read into this
    // one.
    keyboard_.set_layout(keyboard_layout);
    mouse_.set_layout(mouse_layout);
}

void InputPipeline::emit(const InputEvent* events, std::size_t count, std::uint32_t now_ms) {
    for (std::size_t index = 0; index < count; ++index) {
        handler_.on_input(events[index], now_ms);
    }
}

void InputPipeline::on_report(protocol::ByteView report, std::uint32_t now_ms) {
    InputEvent events[kMaxEventsPerReport];

    switch (kind_) {
        case DeviceKind::Keyboard:
        case DeviceKind::Consumer:
            emit(events, keyboard_.apply(report, events, kMaxEventsPerReport), now_ms);
            return;
        case DeviceKind::Mouse:
            emit(events, mouse_.apply(report, events, kMaxEventsPerReport), now_ms);
            return;
        default:
            // A report from a device nobody identified. Guessing at its layout
            // would put arbitrary keystrokes on somebody's computer, so it is
            // counted and dropped.
            ++unclaimed_;
            return;
    }
}

void InputPipeline::on_detached(std::uint32_t now_ms) {
    InputEvent events[kMaxEventsPerReport];
    std::size_t count = 0;

    // The device is gone, so the release it owed will never arrive. Without
    // this, pulling a cable mid-keystroke leaves that key held on a computer
    // that has no way to find out, and it types until somebody reboots it.
    switch (kind_) {
        case DeviceKind::Keyboard:
        case DeviceKind::Consumer:
            count = keyboard_.release_all(events, kMaxEventsPerReport);
            break;
        case DeviceKind::Mouse:
            count = mouse_.release_all(events, kMaxEventsPerReport);
            break;
        default:
            break;
    }

    emit(events, count, now_ms);
    kind_ = DeviceKind::Unknown;
}

void InputPipeline::on_event(const SourceEvent& event, const SourceIdentity& identity,
                             std::uint32_t now_ms) {
    switch (event.kind) {
        case SourceEventKind::Ready:
            // What it is - and how it is read - becomes known only once it
            // has been configured.
            set_kind(identity.kind, identity.keyboard_layout, identity.mouse_layout);
            return;

        case SourceEventKind::Report:
            on_report(protocol::ByteView{event.report, event.report_size}, now_ms);
            return;

        case SourceEventKind::Detached:
        case SourceEventKind::Fault:
            on_detached(now_ms);
            return;
    }
}

}  // namespace duo_input::u1::input
