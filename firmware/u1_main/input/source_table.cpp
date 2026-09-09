#include "input/source_table.hpp"
#include <cstring>

namespace duo_input::u1::input {

SourceTable::SourceTable(IInputHandler& handler) : handler_(handler) {
    for (std::size_t index = 0; index < slots_.size(); ++index) {
        slots_[index].handler.set_target(*this, static_cast<std::uint8_t>(index));
    }
}

void SourceTable::on_event(const SourceEvent& event, const SourceIdentity& identity,
                           std::uint32_t now_ms) {
    if (event.kind == SourceEventKind::Fault && event.source_id == kWholeHostSource) {
        for (auto& source : slots_) {
            if (!source.occupied) continue;
            source.pipeline.on_detached(now_ms);
            source.occupied = false;
        }
        ++revision_;
        return;
    }
    Slot* slot = find(event.source_id);

    if (event.kind == SourceEventKind::Ready) {
        if (slot == nullptr) {
            for (Slot& candidate : slots_) {
                if (!candidate.occupied) {
                    slot = &candidate;
                    break;
                }
            }
        }
        if (slot == nullptr) {
            ++unclaimed_interfaces_;
            ++revision_;
            return;
        }

        slot->occupied = true;
        slot->source_id = event.source_id;
        slot->identity = identity;
        slot->observation = {};
        ++revision_;
        slot->pipeline.set_report_set(identity.report_set);
        return;
    }

    if (slot == nullptr) {
        return;
    }

    switch (event.kind) {
        case SourceEventKind::Report:
        case SourceEventKind::AuxiliaryReport:
            if (slot->observation.reports != 0xFFFFFFFFu) ++slot->observation.reports;
            slot->observation.last_report_size = event.report_size;
            std::memset(slot->observation.last_report, 0, sizeof(slot->observation.last_report));
            std::memcpy(slot->observation.last_report, event.report,
                        event.report_size < 9 ? event.report_size : 9);
            slot->pipeline.on_report(protocol::ByteView{event.report, event.report_size}, now_ms);
            return;

        case SourceEventKind::Detached:
        case SourceEventKind::Fault:
            slot->pipeline.on_detached(now_ms);
            slot->occupied = false;
            ++revision_;
            return;

        case SourceEventKind::Ready:
            return;
    }
}

bool SourceTable::resolve(std::uint8_t index, SourceIdentity& out) const {
    if (index >= slots_.size() || !slots_[index].occupied) {
        return false;
    }
    out = slots_[index].identity;
    return true;
}

std::uint32_t SourceTable::unclaimed_interfaces() const { return unclaimed_interfaces_; }

void SourceTable::inventory(SourceInventory& out, std::uint32_t backend_rejections) const {
    out = {};
    out.rejected_interfaces = unclaimed_interfaces_ + backend_rejections;
    for (const auto& slot : slots_) {
        if (!slot.occupied) continue;
        auto& info = out.sources[out.count++];
        info = slot.observation;
        info.layout_source = slot.identity.layout_source;
        info.keyboard_error = slot.identity.keyboard_error;
        info.consumer_error = slot.identity.consumer_error;
        const bool mouse = slot.identity.kind == DeviceKind::Mouse;
        const auto& keyboard_layout = slot.identity.keyboard_layout;
        const auto& mouse_layout = slot.identity.mouse_layout;
        info.report_id = mouse ? (mouse_layout.report_id ? mouse_layout.report_id_value : 0)
                               : (keyboard_layout.report_id ? keyboard_layout.report_id_value : 0);
        info.minimum_body_bytes = mouse ? mouse_layout.minimum_body_bytes : keyboard_layout.minimum_body_bytes;
        info.vendor_id = slot.identity.vendor_id; info.product_id = slot.identity.product_id;
        info.interface_number = slot.identity.interface_number;
        info.device_address = slot.identity.device_address;
        info.kind = static_cast<std::uint8_t>(slot.identity.kind);
        std::memcpy(info.product_name, slot.identity.product_name, sizeof(info.product_name));
        info.product_name[sizeof(info.product_name) - 1] = 0;
    }
}

void SourceTable::set_product_name(std::uint8_t address, const char* name) {
    for (auto& slot : slots_) {
        if (!slot.occupied || slot.identity.device_address != address) continue;
        std::strncpy(slot.identity.product_name, name, kProductNameBytes - 1);
        slot.identity.product_name[kProductNameBytes - 1] = 0;
        ++revision_;
    }
}

SourceTable::Slot* SourceTable::find(std::uint8_t source_id) {
    for (Slot& slot : slots_) {
        if (slot.occupied && slot.source_id == source_id) {
            return &slot;
        }
    }
    return nullptr;
}

void SourceTable::on_input(std::uint8_t source_index, const InputEvent& event,
                           std::uint32_t now_ms) {
    InputEvent stamped = event;
    stamped.source_index = source_index;
    if (slots_[source_index].observation.decoded_events != 0xFFFFFFFFu)
        ++slots_[source_index].observation.decoded_events;
    handler_.on_input(stamped, now_ms);
}

void SourceTable::SlotHandler::set_target(SourceTable& table, std::uint8_t source_index) {
    table_ = &table;
    source_index_ = source_index;
}

void SourceTable::SlotHandler::on_input(const InputEvent& event, std::uint32_t now_ms) {
    table_->on_input(source_index_, event, now_ms);
}

}  // namespace duo_input::u1::input
