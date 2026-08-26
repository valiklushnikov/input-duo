#include "storage/ab_store.hpp"

#include <cstring>
#include <initializer_list>

#include "protocol/bytes.hpp"
#include "protocol/crc.hpp"

namespace duo_input::storage {
namespace {

/// Identifies a slot header. Chosen so an erased slot (all 0xFF) is not one.
constexpr std::uint8_t kMagic[4] = {'D', 'U', 'O', 'C'};

/// Header layout, little-endian, inside the first page of a slot:
///
///     0   magic[4]
///     4   schema major, schema minor, reserved[2]
///     8   generation (u32)
///    12   size (u32)
///    16   digest[32]
///    48   zero filler
///   252   header CRC-32 over bytes 0..251
///
/// The CRC sits at the very end and covers the whole page, and the space in
/// between is filled with zeros rather than left erased. Both are deliberate.
///
/// A power cut during a page program leaves some of the page written and the
/// rest erased. With the CRC near the front, a page that only got as far as
/// its first few dozen bytes would carry a complete, self-consistent header
/// describing a payload that was never finished - which is precisely the
/// torn-write case this whole design exists to survive. Putting the CRC last
/// means a short program leaves 0xFF where the CRC belongs, and filling the
/// middle with zeros means a short program leaves 0xFF inside the covered
/// region too. Either way the header does not verify, and the slot is nothing.
constexpr std::size_t kMagicOffset = 0;
constexpr std::size_t kSchemaOffset = 4;
constexpr std::size_t kGenerationOffset = 8;
constexpr std::size_t kSizeOffset = 12;
constexpr std::size_t kDigestOffset = 16;
constexpr std::size_t kFillerOffset = 48;
constexpr std::size_t kCrcOffset = kSlotHeaderSize - 4;
constexpr std::size_t kHeaderCoveredBytes = kCrcOffset;

/// Reading and hashing the payload happens through this, a page at a time, so
/// nothing needs a 360 KiB buffer on a chip that does not have one to spare.
constexpr std::size_t kScratchSize = 256;

std::uint32_t read_u32(const std::uint8_t* bytes) {
    return static_cast<std::uint32_t>(bytes[0]) |
           (static_cast<std::uint32_t>(bytes[1]) << 8) |
           (static_cast<std::uint32_t>(bytes[2]) << 16) |
           (static_cast<std::uint32_t>(bytes[3]) << 24);
}

void write_u32(std::uint8_t* bytes, std::uint32_t value) {
    bytes[0] = static_cast<std::uint8_t>(value);
    bytes[1] = static_cast<std::uint8_t>(value >> 8);
    bytes[2] = static_cast<std::uint8_t>(value >> 16);
    bytes[3] = static_cast<std::uint8_t>(value >> 24);
}

/// Is ``candidate`` newer than ``incumbent``, allowing for the counter wrapping?
bool is_newer(std::uint32_t candidate, std::uint32_t incumbent) {
    return static_cast<std::int32_t>(candidate - incumbent) > 0;
}

}  // namespace

StoreError AbStore::read_header(Slot slot, SlotInfo& info) const {
    std::uint8_t header[kSlotHeaderSize];
    if (!flash_.read(slot_offset(slot), header, sizeof(header))) {
        return StoreError::FlashFailed;
    }

    info = SlotInfo{};
    if (std::memcmp(header + kMagicOffset, kMagic, sizeof(kMagic)) != 0) {
        return StoreError::NoConfiguration;
    }

    const std::uint32_t stored_crc = read_u32(header + kCrcOffset);
    const std::uint32_t actual_crc =
        protocol::crc32_ieee(protocol::ByteView{header, kHeaderCoveredBytes});
    if (stored_crc != actual_crc) {
        // A header torn by a power cut can carry the magic and still describe
        // the wrong bytes. The CRC is what tells the two apart.
        return StoreError::NoConfiguration;
    }

    if (header[kSchemaOffset] != protocol::SCHEMA_VERSION_MAJOR) {
        // A configuration written by firmware that spoke a different schema.
        // Refusing it is safer than reading it as if it were this one.
        return StoreError::NoConfiguration;
    }

    const std::uint32_t size = read_u32(header + kSizeOffset);
    if (size == 0 || size > kSlotPayloadCapacity) {
        return StoreError::NoConfiguration;
    }

    info.valid = true;
    info.generation = read_u32(header + kGenerationOffset);
    info.size = size;
    std::memcpy(info.digest, header + kDigestOffset, sizeof(info.digest));
    return StoreError::None;
}

ScanResult AbStore::scan() {
    ScanResult result;
    for (const Slot slot : {Slot::A, Slot::B}) {
        SlotInfo info;
        if (read_header(slot, info) == StoreError::None) {
            result.slots[static_cast<std::size_t>(slot)] = info;
        }
    }

    for (const Slot slot : {Slot::A, Slot::B}) {
        const SlotInfo& info = result.slots[static_cast<std::size_t>(slot)];
        if (!info.valid) {
            continue;
        }
        if (!result.has_active || is_newer(info.generation, result.active_slot().generation)) {
            result.has_active = true;
            result.active = slot;
        }
    }
    return result;
}

StoreError AbStore::begin(std::uint32_t size,
                          const std::uint8_t (&digest)[crypto::kSha256DigestSize]) {
    // Checked before anything is erased: refusing afterwards would have
    // destroyed the spare copy for nothing.
    if (size == 0 || size > kSlotPayloadCapacity) {
        return StoreError::TooLarge;
    }

    const ScanResult current = scan();
    staging_slot_ = current.has_active ? other_slot(current.active) : Slot::A;
    staging_generation_ = current.has_active ? current.active_slot().generation + 1 : 1;
    staging_size_ = size;
    std::memcpy(staging_digest_, digest, sizeof(staging_digest_));
    verified_ = false;

    if (!flash_.erase(slot_offset(staging_slot_), kSlotSize)) {
        staging_ = false;
        return StoreError::FlashFailed;
    }
    staging_ = true;
    return StoreError::None;
}

StoreError AbStore::write_chunk(std::uint32_t offset, const std::uint8_t* data,
                                std::size_t size) {
    if (!staging_) {
        return StoreError::NotStaging;
    }
    if (data == nullptr || size == 0) {
        return StoreError::OutOfRange;
    }
    if (offset > staging_size_ || size > staging_size_ - offset) {
        return StoreError::OutOfRange;
    }

    // Any change to the bytes invalidates a verification that already ran.
    verified_ = false;

    const std::uint32_t destination = slot_offset(staging_slot_) + kSlotHeaderSize + offset;
    if (!flash_.program(destination, data, size)) {
        return StoreError::FlashFailed;
    }
    return StoreError::None;
}

StoreError AbStore::hash_payload(Slot slot, std::uint32_t size,
                                 std::uint8_t (&digest)[crypto::kSha256DigestSize]) const {
    crypto::Sha256 hasher;
    std::uint8_t scratch[kScratchSize];
    const std::uint32_t base = slot_offset(slot) + kSlotHeaderSize;

    for (std::uint32_t done = 0; done < size;) {
        const std::uint32_t take = size - done < kScratchSize ? size - done : kScratchSize;
        if (!flash_.read(base + done, scratch, take)) {
            return StoreError::FlashFailed;
        }
        hasher.update(scratch, take);
        done += take;
    }
    hasher.finish(digest);
    return StoreError::None;
}

StoreError AbStore::verify() {
    if (!staging_) {
        return StoreError::NotStaging;
    }

    // Hashed from flash, not from what was received: otherwise this would
    // confirm only that the bytes survived the wire, which the frame CRC
    // already said.
    std::uint8_t actual[crypto::kSha256DigestSize] = {};
    const StoreError error = hash_payload(staging_slot_, staging_size_, actual);
    if (error != StoreError::None) {
        return error;
    }
    if (!crypto::digests_equal(actual, staging_digest_)) {
        return StoreError::DigestMismatch;
    }
    verified_ = true;
    return StoreError::None;
}

StoreError AbStore::commit() {
    if (!staging_) {
        return StoreError::NotStaging;
    }
    if (!verified_) {
        // The commit is what makes a slot authoritative. Doing it on bytes
        // nobody checked would make the verify step decorative.
        return StoreError::NotVerified;
    }

    std::uint8_t header[kSlotHeaderSize];
    // Zero, not 0xFF: an unwritten byte in an erased page reads as 0xFF, so
    // filling with zeros is what makes a partial program detectable.
    std::memset(header, 0x00, sizeof(header));
    std::memcpy(header + kMagicOffset, kMagic, sizeof(kMagic));
    header[kSchemaOffset] = protocol::SCHEMA_VERSION_MAJOR;
    header[kSchemaOffset + 1] = protocol::SCHEMA_VERSION_MINOR;
    header[kSchemaOffset + 2] = 0;
    header[kSchemaOffset + 3] = 0;
    write_u32(header + kGenerationOffset, staging_generation_);
    write_u32(header + kSizeOffset, staging_size_);
    std::memcpy(header + kDigestOffset, staging_digest_, sizeof(staging_digest_));
    write_u32(header + kCrcOffset,
              protocol::crc32_ieee(protocol::ByteView{header, kHeaderCoveredBytes}));

    // The last thing written, and the only irreversible one. Everything before
    // this point can be abandoned without the device noticing.
    if (!flash_.program(slot_offset(staging_slot_), header, sizeof(header))) {
        return StoreError::FlashFailed;
    }

    staging_ = false;
    verified_ = false;
    return StoreError::None;
}

void AbStore::abort() {
    // The staged slot has no header, so it is already nothing. There is
    // nothing to undo and no reason to spend an erase saying so.
    staging_ = false;
    verified_ = false;
}

StoreError AbStore::read_active(std::uint32_t offset, std::uint8_t* data, std::size_t size) {
    const ScanResult current = scan();
    if (!current.has_active) {
        return StoreError::NoConfiguration;
    }
    const SlotInfo& info = current.active_slot();
    if (offset > info.size || size > info.size - offset) {
        return StoreError::OutOfRange;
    }
    const std::uint32_t base = slot_offset(current.active) + kSlotHeaderSize + offset;
    if (!flash_.read(base, data, size)) {
        return StoreError::FlashFailed;
    }
    return StoreError::None;
}

StoreError AbStore::verify_slot(Slot slot) {
    SlotInfo info;
    const StoreError error = read_header(slot, info);
    if (error != StoreError::None) {
        return error;
    }

    std::uint8_t actual[crypto::kSha256DigestSize] = {};
    const StoreError hashed = hash_payload(slot, info.size, actual);
    if (hashed != StoreError::None) {
        return hashed;
    }
    return crypto::digests_equal(actual, info.digest) ? StoreError::None
                                                      : StoreError::DigestMismatch;
}

}  // namespace duo_input::storage
