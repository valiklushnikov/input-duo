#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

#include "input/pipeline.hpp"

namespace duo_input::u1::input {

/// Fixed source slots for the HID interfaces currently attached to U1.
///
/// A slot is assigned to one interface, never to a whole composite device.
/// Its pipeline consequently owns the keyboard or mouse normalizer state for
/// that interface alone.
class SourceTable {
public:
    static constexpr std::size_t kMaxSources = 8;

    explicit SourceTable(IInputHandler& handler);
    SourceTable(const SourceTable&) = delete;
    SourceTable& operator=(const SourceTable&) = delete;
    SourceTable(SourceTable&&) = delete;
    SourceTable& operator=(SourceTable&&) = delete;

    void on_event(const SourceEvent& event, const SourceIdentity& identity,
                  std::uint32_t now_ms);
    bool resolve(std::uint8_t index, SourceIdentity& out) const;
    std::uint32_t unclaimed_interfaces() const;

private:
    class SlotHandler final : public IInputHandler {
    public:
        void set_target(SourceTable& table, std::uint8_t source_index);
        void on_input(const InputEvent& event, std::uint32_t now_ms) override;

    private:
        SourceTable* table_ = nullptr;
        std::uint8_t source_index_ = 0;
    };

    struct Slot {
        Slot() : pipeline(handler) {}

        bool occupied = false;
        std::uint8_t source_id = 0;
        SourceIdentity identity{};
        SlotHandler handler;
        InputPipeline pipeline;
    };

    Slot* find(std::uint8_t source_id);
    void on_input(std::uint8_t source_index, const InputEvent& event, std::uint32_t now_ms);

    IInputHandler& handler_;
    std::array<Slot, kMaxSources> slots_{};
    std::uint32_t unclaimed_interfaces_ = 0;
};

}  // namespace duo_input::u1::input
