#include "input/source_table.hpp"

namespace duo_input::u1::input {

SourceTable::SourceTable(IInputHandler& handler) : handler_(handler) {
    for (std::size_t index = 0; index < slots_.size(); ++index) {
        slots_[index].handler.set_target(*this, static_cast<std::uint8_t>(index));
    }
}

void SourceTable::on_event(const SourceEvent& event, const SourceIdentity& identity,
                           std::uint32_t now_ms) {
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
            return;
        }

        slot->occupied = true;
        slot->source_id = event.source_id;
        slot->identity = identity;
        slot->pipeline.set_kind(identity.kind, identity.keyboard_layout, identity.mouse_layout);
        return;
    }

    if (slot == nullptr) {
        return;
    }

    switch (event.kind) {
        case SourceEventKind::Report:
        case SourceEventKind::AuxiliaryReport:
            slot->pipeline.on_report(protocol::ByteView{event.report, event.report_size}, now_ms);
            return;

        case SourceEventKind::Detached:
        case SourceEventKind::Fault:
            slot->pipeline.on_detached(now_ms);
            slot->occupied = false;
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
