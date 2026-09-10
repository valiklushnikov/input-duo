#include "input/ch375_source_adapter.hpp"

#include <algorithm>
#include <cstring>
#include <new>

namespace duo_input::u1::input {
namespace {

DeviceKind neutral_kind(ch375::DeviceKind kind) {
    switch (kind) {
        case ch375::DeviceKind::Keyboard:
            return DeviceKind::Keyboard;
        case ch375::DeviceKind::Mouse:
            return DeviceKind::Mouse;
        case ch375::DeviceKind::Unknown:
        default:
            return DeviceKind::Unknown;
    }
}

DeviceKind neutral_kind(hid::ReportRole role) {
    switch (role) {
        case hid::ReportRole::Keyboard:
            return DeviceKind::Keyboard;
        case hid::ReportRole::Consumer:
            return DeviceKind::Consumer;
        case hid::ReportRole::Mouse:
            return DeviceKind::Mouse;
    }
    return DeviceKind::Unknown;
}

}  // namespace

bool Ch375SourceAdapter::convert(const ch375::Ch375Event& event, SourceEvent& out) const {
    SourceEventKind kind;
    switch (event.kind) {
        case ch375::Ch375EventKind::Ready:
            kind = SourceEventKind::Ready;
            break;
        case ch375::Ch375EventKind::Report:
            kind = SourceEventKind::Report;
            break;
        case ch375::Ch375EventKind::AuxiliaryReport:
            kind = SourceEventKind::AuxiliaryReport;
            break;
        case ch375::Ch375EventKind::Detached:
            kind = SourceEventKind::Detached;
            break;
        case ch375::Ch375EventKind::Fault:
            kind = SourceEventKind::Fault;
            break;
        case ch375::Ch375EventKind::None:
        case ch375::Ch375EventKind::Attached:
        default:
            // Nothing to route: a device that has merely been plugged in is
            // not usable yet, and the pipeline's own on_event ignored both
            // of these before this boundary existed.
            return false;
    }

    out.kind = kind;
    out.source_id = source_id_;
    out.endpoint = event.endpoint;
    out.received_us = event.received_us;
    const std::size_t copied = std::min(event.report_size, kMaxSourceReportBytes);
    std::memcpy(out.report, event.report, copied);
    out.report_size = copied;
    return true;
}

void Ch375SourceAdapter::identity(const ch375::DescriptorSetup& setup, SourceIdentity& out) const {
    // Reinitialize caller-owned scratch in place, including default diagnostic
    // fields, without constructing a second full configuration on the stack.
    new (&out) SourceIdentity{};
    out.report_set = setup.report_set();
    out.kind = out.report_set.count != 0
                   ? neutral_kind(out.report_set.entries[0].role)
                   : neutral_kind(setup.kind());
    out.vendor_id = setup.vendor_id();
    out.product_id = setup.product_id();
    out.keyboard_layout = setup.keyboard_layout();
    out.mouse_layout = setup.mouse_layout();
    std::memcpy(out.descriptor_hash, setup.report_descriptor_hash(), sizeof(out.descriptor_hash));
}

}  // namespace duo_input::u1::input
