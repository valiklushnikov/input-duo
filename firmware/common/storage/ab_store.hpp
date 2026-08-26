#pragma once

// Two configuration slots, written so that losing power is survivable.
//
// The order is the whole design. The spare slot is erased, the payload is
// written into it, the payload is read back and hashed, and only then is the
// header programmed. Until that last page lands the slot describes nothing,
// so a device that reboots at any earlier point finds exactly what it had
// before. A device that reboots during the header finds a header whose CRC
// does not match, which is also nothing.
//
// The digest is recomputed from what is in flash, not carried over from what
// was received. Otherwise the check would confirm only that the bytes survived
// the wire, which is what the frame CRC already said.

#include <cstddef>
#include <cstdint>

#include "crypto/sha256.hpp"
#include "storage/flash_layout.hpp"

namespace duo_input::storage {

/// The flash operations this needs, so a test can supply its own.
class FlashBackend {
public:
    virtual ~FlashBackend() = default;

    /// Erase ``size`` bytes at ``offset``. Both are sector-aligned.
    virtual bool erase(std::uint32_t offset, std::size_t size) = 0;

    /// Program ``size`` bytes at ``offset``. Both are page-aligned.
    virtual bool program(std::uint32_t offset, const std::uint8_t* data,
                         std::size_t size) = 0;

    virtual bool read(std::uint32_t offset, std::uint8_t* data, std::size_t size) const = 0;
};

enum class StoreError : std::uint8_t {
    None = 0,
    /// The package is empty or will not fit in a slot.
    TooLarge,
    /// A chunk that falls outside the package being written.
    OutOfRange,
    /// write_chunk or verify without a begin.
    NotStaging,
    /// commit before a successful verify.
    NotVerified,
    /// What is in flash does not hash to what was promised.
    DigestMismatch,
    /// The flash refused an erase or a program.
    FlashFailed,
    /// Nothing valid is stored.
    NoConfiguration,
};

/// What one slot claims about itself.
struct SlotInfo {
    bool valid = false;
    std::uint32_t generation = 0;
    std::uint32_t size = 0;
    std::uint8_t digest[crypto::kSha256DigestSize] = {};
};

/// Both slots, and which one the device should run from.
struct ScanResult {
    bool has_active = false;
    Slot active = Slot::A;
    SlotInfo slots[2];

    const SlotInfo& active_slot() const {
        return slots[static_cast<std::size_t>(active)];
    }
};

class AbStore {
public:
    explicit AbStore(FlashBackend& flash) : flash_(flash) {}

    /// Read both headers and decide which slot is authoritative.
    ///
    /// Cheap: it reads two pages. Safe to call after every reset.
    ScanResult scan();

    /// Start writing a new configuration into the slot that is not in use.
    ///
    /// Erases that slot, which is why the size is checked first: refusing
    /// afterwards would have destroyed the spare copy for nothing.
    StoreError begin(std::uint32_t size, const std::uint8_t (&digest)[crypto::kSha256DigestSize]);

    /// Write part of the package. Chunks may arrive in any order.
    StoreError write_chunk(std::uint32_t offset, const std::uint8_t* data, std::size_t size);

    /// Hash what is actually in flash and compare it with what was promised.
    StoreError verify();

    /// Make the staged slot authoritative by programming its header.
    ///
    /// This is the only irreversible step, and it is one page.
    StoreError commit();

    /// Give up. The staged slot stays invalid; the running one is untouched.
    void abort();

    /// Is a write in progress?
    bool staging() const { return staging_; }

    /// Read from the currently active slot's payload.
    StoreError read_active(std::uint32_t offset, std::uint8_t* data, std::size_t size);

    /// Recompute one slot's digest from flash and compare it to its header.
    StoreError verify_slot(Slot slot);

private:
    StoreError read_header(Slot slot, SlotInfo& info) const;
    StoreError hash_payload(Slot slot, std::uint32_t size,
                            std::uint8_t (&digest)[crypto::kSha256DigestSize]) const;

    FlashBackend& flash_;
    bool staging_ = false;
    bool verified_ = false;
    Slot staging_slot_ = Slot::A;
    std::uint32_t staging_size_ = 0;
    std::uint32_t staging_generation_ = 0;
    std::uint8_t staging_digest_[crypto::kSha256DigestSize] = {};
};

}  // namespace duo_input::storage
