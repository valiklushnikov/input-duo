#include "input/pipeline.hpp"

namespace duo_input::u1::input {

void InputPipeline::set_kind(ch375::DeviceKind kind) { kind_ = kind; }

void InputPipeline::emit(const InputEvent* events, std::size_t count, std::uint32_t now_ms) {
    for (std::size_t index = 0; index < count; ++index) {
        handler_.on_input(events[index], now_ms);
    }
}

void InputPipeline::on_report(protocol::ByteView report, std::uint32_t now_ms) {
    InputEvent events[kMaxEventsPerReport];

    switch (kind_) {
        case ch375::DeviceKind::Keyboard:
            emit(events, keyboard_.apply(report, events, kMaxEventsPerReport), now_ms);
            return;
        case ch375::DeviceKind::Mouse:
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
        case ch375::DeviceKind::Keyboard:
            count = keyboard_.release_all(events, kMaxEventsPerReport);
            break;
        case ch375::DeviceKind::Mouse:
            count = mouse_.release_all(events, kMaxEventsPerReport);
            break;
        default:
            break;
    }

    emit(events, count, now_ms);
    kind_ = ch375::DeviceKind::Unknown;
}

void InputPipeline::on_event(const ch375::Ch375Event& event, ch375::DeviceKind kind,
                             std::uint32_t now_ms) {
    switch (event.kind) {
        case ch375::Ch375EventKind::Ready:
            // What it is becomes known only once it has been configured.
            set_kind(kind);
            return;

        case ch375::Ch375EventKind::Report:
            on_report(protocol::ByteView{event.report, event.report_size}, now_ms);
            return;

        case ch375::Ch375EventKind::Detached:
        case ch375::Ch375EventKind::Fault:
            on_detached(now_ms);
            return;

        default:
            return;
    }
}

}  // namespace duo_input::u1::input
