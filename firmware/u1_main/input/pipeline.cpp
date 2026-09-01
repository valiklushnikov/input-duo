#include "input/pipeline.hpp"

namespace duo_input::u1::input {
namespace {

constexpr std::uint16_t kMouseButton4 = 3;
constexpr std::uint8_t kKeychronSideUsage = 0x4F;
constexpr std::uint16_t kKeychronVendorId = 0x3434;
constexpr std::uint16_t kKeychronProductId = 0xD030;

bool keychron_side_state(protocol::ByteView report, bool& held) {
    // Report ID 1 followed by an eight-byte boot-keyboard report. Only the
    // complete nine-byte wire report is input; the old eight-byte value came
    // from a diagnostic display which truncated what it retained.
    if (report.data == nullptr || report.size != 9 ||
        report.data[0] != 0x01 || report.data[2] != 0x00) {
        return false;
    }

    // Keychron sometimes changes the Ctrl modifier and usage 0x4F in
    // separate frames. Both modifier values have been captured for the same
    // physical hold; any other modifier or key makes this a different input.
    if (report.data[1] != 0x00 && report.data[1] != 0x01) {
        return false;
    }

    held = false;
    for (std::size_t index = 3; index < report.size; ++index) {
        if (report.data[index] == kKeychronSideUsage) {
            held = true;
        } else if (index == 8 && report.size == 9 && report.data[index] == 0x03) {
            // Full hardware capture: the receiver sometimes keeps usage 03
            // in its sixth key slot for both halves of this side-button
            // shortcut. It carries no edge; the zero-tail variant and this
            // variant are the same physical control.
            continue;
        } else if (report.data[index] != 0x00) {
            return false;
        }
    }
    return true;
}

}  // namespace

void InputPipeline::set_kind(ch375::DeviceKind kind,
                             const ch375::MouseReportLayout& mouse_layout) {
    kind_ = kind;
    // Set on every Ready, not only when a descriptor was read: a device that
    // would not describe itself hands over boot protocol's layout, and setting
    // it is what stops the mouse before it from being read into this one.
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

void InputPipeline::on_auxiliary_report(std::uint8_t endpoint, protocol::ByteView report,
                                        std::uint32_t now_ms) {
    // Captured from Keychron M3 receiver 3434:D030. Its side button is emitted
    // by the receiver's later keyboard interface, never by the mouse report.
    // Endpoint and the report structure are both required so an unrelated
    // composite device cannot become a mouse click by resemblance.
    if (kind_ != ch375::DeviceKind::Mouse || !keychron_receiver_ || endpoint != 1) {
        return;
    }

    InputEvent event;
    event.code = kMouseButton4;
    bool held = false;
    if (!keychron_side_state(report, held) ||
        held == keychron_side_button_held_.load(std::memory_order_relaxed)) {
        return;
    }

    keychron_side_button_held_.store(held, std::memory_order_relaxed);
#if DUO_CH375_PROBE
    if (held) {
        keychron_side_presses_.fetch_add(1, std::memory_order_relaxed);
    } else {
        keychron_side_releases_.fetch_add(1, std::memory_order_relaxed);
    }
#endif
    event.kind = held ? InputEventKind::MouseButtonDown : InputEventKind::MouseButtonUp;
    handler_.on_input(event, now_ms);
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
            if (keychron_side_button_held_.load(std::memory_order_relaxed) &&
                count < kMaxEventsPerReport) {
                events[count].kind = InputEventKind::MouseButtonUp;
                events[count].code = kMouseButton4;
                ++count;
            }
            break;
        default:
            break;
    }

    emit(events, count, now_ms);
    keychron_side_button_held_.store(false, std::memory_order_relaxed);
    keychron_receiver_ = false;
    kind_ = ch375::DeviceKind::Unknown;
}

void InputPipeline::on_event(const ch375::Ch375Event& event, ch375::DeviceKind kind,
                             const ch375::MouseReportLayout& mouse_layout,
                             std::uint32_t now_ms, std::uint16_t vendor_id,
                             std::uint16_t product_id) {
    switch (event.kind) {
        case ch375::Ch375EventKind::Ready:
            // What it is - and how it is read - becomes known only once it
            // has been configured.
            keychron_receiver_ = vendor_id == kKeychronVendorId &&
                                 product_id == kKeychronProductId;
            set_kind(kind, mouse_layout);
            return;

        case ch375::Ch375EventKind::Report:
            on_report(protocol::ByteView{event.report, event.report_size}, now_ms);
            return;

        case ch375::Ch375EventKind::AuxiliaryReport:
            on_auxiliary_report(event.endpoint,
                                protocol::ByteView{event.report, event.report_size}, now_ms);
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
