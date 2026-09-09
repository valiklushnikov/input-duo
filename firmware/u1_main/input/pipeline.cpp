#include "input/pipeline.hpp"

namespace duo_input::u1::input {
void InputPipeline::set_report_set(const hid::HidReportSet& reports) {
    for (auto& decoder : decoders_) decoder = DecoderSlot{};
    decoder_count_ = 0;
    uses_report_ids_ = reports.uses_report_ids;
    kind_ = DeviceKind::Unknown;
    for (std::size_t index = 0; index < reports.count && index < hid::kMaxHidReportEntries;
         ++index) {
        const auto& entry = reports.entries[index];
        auto& decoder = decoders_[decoder_count_++];
        decoder.active = true;
        decoder.role = entry.role;
        decoder.report_id = entry.report_id;
        if (entry.role == hid::ReportRole::Mouse) {
            decoder.mouse.set_layout(entry.mouse);
        } else {
            decoder.keyboard.set_layout(entry.keyboard);
        }
        if (index == 0) {
            kind_ = entry.role == hid::ReportRole::Mouse ? DeviceKind::Mouse
                    : entry.role == hid::ReportRole::Consumer ? DeviceKind::Consumer
                                                               : DeviceKind::Keyboard;
        }
    }
}

void InputPipeline::set_kind(DeviceKind kind, const hid::KeyboardReportLayout& keyboard_layout,
                             const hid::MouseReportLayout& mouse_layout) {
    hid::HidReportSet reports;
    if (kind != DeviceKind::Unknown) {
        reports.count = 1;
        auto& entry = reports.entries[0];
        if (kind == DeviceKind::Mouse) {
            entry.role = hid::ReportRole::Mouse;
            entry.mouse = mouse_layout;
            reports.uses_report_ids = mouse_layout.report_id;
            entry.report_id = mouse_layout.report_id_value;
        } else {
            entry.role = kind == DeviceKind::Consumer ? hid::ReportRole::Consumer
                                                       : hid::ReportRole::Keyboard;
            entry.keyboard = keyboard_layout;
            reports.uses_report_ids = keyboard_layout.report_id;
            entry.report_id = keyboard_layout.report_id_value;
        }
    }
    set_report_set(reports);
}

void InputPipeline::emit(const InputEvent* events, std::size_t count, std::uint32_t now_ms) {
    for (std::size_t index = 0; index < count; ++index) {
        handler_.on_input(events[index], now_ms);
    }
}

void InputPipeline::on_report(protocol::ByteView report, std::uint32_t now_ms) {
    if (decoder_count_ == 0) {
        ++unclaimed_;
        return;
    }
    if (uses_report_ids_ && (report.data == nullptr || report.size == 0)) return;
    InputEvent events[kMaxEventsPerReport];
    const std::size_t count = uses_report_ids_ ? decoder_count_ : 1;
    for (std::size_t index = 0; index < count; ++index) {
        auto& decoder = decoders_[index];
        if (!decoder.active || (uses_report_ids_ && decoder.report_id != report.data[0])) continue;
        // Keep the original bytes: each normalizer validates and strips its
        // own descriptor-declared ID. Unknown IDs never acquire a fallback.
        const auto emitted = decoder.role == hid::ReportRole::Mouse
            ? decoder.mouse.apply(report, events, kMaxEventsPerReport)
            : decoder.keyboard.apply(report, events, kMaxEventsPerReport);
        emit(events, emitted, now_ms);
    }
}

void InputPipeline::on_detached(std::uint32_t now_ms) {
    InputEvent events[kMaxEventsPerReport];

    // The device is gone, so the release it owed will never arrive. Without
    // this, pulling a cable mid-keystroke leaves that key held on a computer
    // that has no way to find out, and it types until somebody reboots it.
    for (auto& decoder : decoders_) {
        if (!decoder.active) continue;
        const auto count = decoder.role == hid::ReportRole::Mouse
            ? decoder.mouse.release_all(events, kMaxEventsPerReport)
            : decoder.keyboard.release_all(events, kMaxEventsPerReport);
        emit(events, count, now_ms);
    }
    for (auto& decoder : decoders_) decoder = DecoderSlot{};
    decoder_count_ = 0;
    uses_report_ids_ = false;
    kind_ = DeviceKind::Unknown;
}

void InputPipeline::on_event(const SourceEvent& event, const SourceIdentity& identity,
                             std::uint32_t now_ms) {
    switch (event.kind) {
        case SourceEventKind::Ready:
            // What it is - and how it is read - becomes known only once it
            // has been configured.
            set_report_set(identity.report_set);
            return;

        case SourceEventKind::Report:
        case SourceEventKind::AuxiliaryReport:
            on_report(protocol::ByteView{event.report, event.report_size}, now_ms);
            return;

        case SourceEventKind::Detached:
        case SourceEventKind::Fault:
            on_detached(now_ms);
            return;
    }
}

}  // namespace duo_input::u1::input
