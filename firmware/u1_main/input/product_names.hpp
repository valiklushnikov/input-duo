#pragma once

#include "input/source_table.hpp"

namespace duo_input::u1::input {
// Core 1 owns the control transfer buffer and the table it updates.
class ProductNames {
public:
    template<class Offer> void poll(SourceTable& table, Offer offer) {
        if (active_) return;
        if (revision_ != table.revision()) {
            revision_ = table.revision();
            for (auto& attempts : attempts_) attempts = 0;
        }
        table.inventory(inventory_);
        for (std::size_t i = 0; i < inventory_.count; ++i) {
            const auto& source = inventory_.sources[i];
            const auto address = source.device_address;
            if (address == 0 || address >= 128 || source.product_name[0] || attempts_[address] >= 8) continue;
            ++attempts_[address];
            table_ = &table; address_ = address; active_ = true;
            for (auto& byte : buffer_) byte = 0;
            if (!offer(address, buffer_, sizeof(buffer_))) active_ = false;
            return;
        }
    }

    void complete(bool success, std::size_t length) {
        if (!active_) return;
        active_ = false;
        attempts_[address_] = 8;
        if (!success || table_->revision() != revision_ || length < 2 || length > sizeof(buffer_) ||
            buffer_[1] != 3 || buffer_[0] < 2 || buffer_[0] > length || (buffer_[0] & 1)) return;
        char name[kProductNameBytes] = {};
        std::size_t used = 0;
        for (std::size_t i = 2; i < buffer_[0]; i += 2) {
            const std::uint16_t ch = buffer_[i] | (static_cast<std::uint16_t>(buffer_[i + 1]) << 8);
            if (ch == 0) break;
            if (ch < 0x80 && used + 1 < sizeof(name)) name[used++] = static_cast<char>(ch);
            else if (ch < 0x800 && used + 2 < sizeof(name)) {
                name[used++] = static_cast<char>(0xC0 | (ch >> 6));
                name[used++] = static_cast<char>(0x80 | (ch & 0x3F));
            } else if (used + 3 < sizeof(name)) {
                const auto code = (ch >= 0xD800 && ch <= 0xDFFF) ? 0xFFFD : ch;
                name[used++] = static_cast<char>(0xE0 | (code >> 12));
                name[used++] = static_cast<char>(0x80 | ((code >> 6) & 0x3F));
                name[used++] = static_cast<char>(0x80 | (code & 0x3F));
            } else break;
        }
        table_->set_product_name(address_, name);
        revision_ = table_->revision();
    }

private:
    SourceTable* table_ = nullptr;
    SourceInventory inventory_{};
    std::uint32_t revision_ = 0xFFFFFFFFu;
    std::uint8_t attempts_[128] = {};
    alignas(4) std::uint8_t buffer_[96] = {};
    std::uint8_t address_ = 0;
    bool active_ = false;
};
}
