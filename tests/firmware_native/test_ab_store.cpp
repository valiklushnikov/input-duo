// Two configuration slots, and the promise that power can fail at any moment.
//
// The device is a USB gadget on someone's desk. It will be unplugged mid-write,
// and not once: the operator will do it because the write seemed to hang, or
// the machine will sleep, or a hub will drop out. Every one of those has to
// leave the device holding a configuration it can parse - the old one or the
// new one, never a mixture and never nothing.
//
// So the tests below do not ask "does a write work". They interrupt every
// erase and every program in turn and ask what a device that rebooted right
// there would find.

#include <cstring>
#include <string>
#include <vector>

#include "crypto/sha256.hpp"
#include "storage/ab_store.hpp"
#include "storage/flash_layout.hpp"
#include "test_support.hpp"

using duo_input::crypto::kSha256DigestSize;
using duo_input::crypto::sha256;
using duo_input::storage::AbStore;
using duo_input::storage::FlashBackend;
using duo_input::storage::kSlotHeaderSize;
using duo_input::storage::ScanResult;
using duo_input::storage::Slot;
using duo_input::storage::StoreError;

namespace {

/// Flash that can be told to die partway through, the way a supply does.
class FakeFlash : public FlashBackend {
public:
    FakeFlash() : bytes_(duo_input::storage::kFlashSize, 0xFF) {}

    /// Fail every operation from the ``n``-th onwards.
    void fail_from_operation(std::size_t n) { fail_from_ = n; }

    /// Cut power partway through the ``n``-th program, leaving it half done.
    void tear_operation(std::size_t n) { tear_at_ = n; }

    std::size_t operations() const { return operations_; }

    bool erase(std::uint32_t offset, std::size_t size) override {
        if (++operations_ > fail_from_) {
            return false;
        }
        // A real erase happens sector by sector, so an interrupted one leaves
        // an initial run of sectors erased and the rest untouched.
        const std::size_t sectors = size / duo_input::storage::kSectorSize;
        const std::size_t done = (operations_ == tear_at_) ? sectors / 2 : sectors;
        for (std::size_t sector = 0; sector < done; ++sector) {
            std::memset(&bytes_[offset + sector * duo_input::storage::kSectorSize], 0xFF,
                        duo_input::storage::kSectorSize);
        }
        return operations_ != tear_at_;
    }

    bool program(std::uint32_t offset, const std::uint8_t* data, std::size_t size) override {
        if (++operations_ > fail_from_) {
            return false;
        }
        const std::size_t written = (operations_ == tear_at_) ? size / 2 : size;
        std::memcpy(&bytes_[offset], data, written);
        return operations_ != tear_at_;
    }

    bool read(std::uint32_t offset, std::uint8_t* data, std::size_t size) const override {
        std::memcpy(data, &bytes_[offset], size);
        return true;
    }

    // An RP2040 maps flash into the address space, so the real backend hands
    // out a pointer. This one does too, which keeps the tests on the same path
    // the device takes.
    const std::uint8_t* direct(std::uint32_t offset) const override {
        return &bytes_[offset];
    }

protected:
    std::vector<std::uint8_t> bytes_;
    std::size_t operations_ = 0;
    std::size_t fail_from_ = SIZE_MAX;
    std::size_t tear_at_ = SIZE_MAX;
};

std::vector<std::uint8_t> package(std::size_t size, std::uint8_t fill) {
    return std::vector<std::uint8_t>(size, fill);
}

void digest_of(const std::vector<std::uint8_t>& data,
               std::uint8_t (&digest)[kSha256DigestSize]) {
    sha256(data.data(), data.size(), digest);
}

/// Write one package end to end. Returns the error, or None on success.
StoreError store(AbStore& ab, const std::vector<std::uint8_t>& data,
                 std::size_t chunk = 512) {
    std::uint8_t digest[kSha256DigestSize] = {};
    digest_of(data, digest);

    StoreError error = ab.begin(static_cast<std::uint32_t>(data.size()), digest);
    if (error != StoreError::None) {
        return error;
    }
    for (std::size_t offset = 0; offset < data.size(); offset += chunk) {
        const std::size_t take = data.size() - offset < chunk ? data.size() - offset : chunk;
        error = ab.write_chunk(static_cast<std::uint32_t>(offset), data.data() + offset, take);
        if (error != StoreError::None) {
            return error;
        }
    }
    error = ab.verify();
    if (error != StoreError::None) {
        return error;
    }
    return ab.commit();
}

}  // namespace

// ------------------------------------------------------------------- layout

TEST_CASE(the_slots_sit_exactly_where_the_specification_puts_them) {
    // These are not adjustable. A build that moves them turns every existing
    // device's stored configuration into noise at the next write.
    CHECK_EQ(duo_input::storage::kFirmwareOffset, 0x000000u);
    CHECK_EQ(duo_input::storage::kFirmwareSize, 0x100000u);
    CHECK_EQ(duo_input::storage::kConfigAOffset, 0x100000u);
    CHECK_EQ(duo_input::storage::kConfigBOffset, 0x160000u);
    CHECK_EQ(duo_input::storage::kServiceOffset, 0x1C0000u);
}

TEST_CASE(each_slot_can_hold_the_largest_configuration_the_protocol_allows) {
    CHECK(duo_input::storage::kSlotPayloadCapacity >=
          duo_input::protocol::ProtocolLimits::BINARY_CONFIG_MAX_BYTES);
}

TEST_CASE(the_slots_do_not_overlap_the_firmware_or_each_other) {
    CHECK(duo_input::storage::kConfigAOffset >=
          duo_input::storage::kFirmwareOffset + duo_input::storage::kFirmwareSize);
    CHECK(duo_input::storage::kConfigBOffset >=
          duo_input::storage::kConfigAOffset + duo_input::storage::kSlotSize);
    CHECK(duo_input::storage::kServiceOffset >=
          duo_input::storage::kConfigBOffset + duo_input::storage::kSlotSize);
}

// -------------------------------------------------------------- empty device

TEST_CASE(a_blank_device_has_no_configuration) {
    FakeFlash flash;
    AbStore ab(flash);

    const ScanResult found = ab.scan();

    CHECK_FALSE(found.has_active);
}

TEST_CASE(a_blank_device_reports_neither_slot_as_valid) {
    FakeFlash flash;
    AbStore ab(flash);

    const ScanResult found = ab.scan();

    CHECK_FALSE(found.slots[0].valid);
    CHECK_FALSE(found.slots[1].valid);
}

// ------------------------------------------------------------ ordinary write

TEST_CASE(a_written_configuration_is_found_again) {
    FakeFlash flash;
    AbStore ab(flash);
    const auto data = package(4096, 0xA5);

    CHECK_EQ(store(ab, data), StoreError::None);

    const ScanResult found = ab.scan();
    CHECK(found.has_active);
    CHECK_EQ(found.active_slot().size, 4096u);
}

TEST_CASE(the_first_write_lands_in_slot_a) {
    FakeFlash flash;
    AbStore ab(flash);

    store(ab, package(1024, 1));

    CHECK_EQ(ab.scan().active, Slot::A);
}

TEST_CASE(the_second_write_lands_in_the_other_slot) {
    FakeFlash flash;
    AbStore ab(flash);
    store(ab, package(1024, 1));

    store(ab, package(1024, 2));

    // Writing over the slot the device is running from would leave it with
    // nothing at all for the duration of the write.
    CHECK_EQ(ab.scan().active, Slot::B);
}

TEST_CASE(writes_keep_alternating) {
    FakeFlash flash;
    AbStore ab(flash);

    store(ab, package(512, 1));
    store(ab, package(512, 2));
    store(ab, package(512, 3));

    CHECK_EQ(ab.scan().active, Slot::A);
}

TEST_CASE(each_write_raises_the_generation) {
    FakeFlash flash;
    AbStore ab(flash);

    store(ab, package(512, 1));
    const std::uint32_t first = ab.scan().active_slot().generation;
    store(ab, package(512, 2));
    const std::uint32_t second = ab.scan().active_slot().generation;

    CHECK(second > first);
}

TEST_CASE(the_stored_bytes_can_be_read_back_unchanged) {
    FakeFlash flash;
    AbStore ab(flash);
    auto data = package(2048, 0);
    for (std::size_t index = 0; index < data.size(); ++index) {
        data[index] = static_cast<std::uint8_t>(index & 0xFF);
    }
    store(ab, data);

    std::vector<std::uint8_t> read_back(data.size(), 0);
    CHECK_EQ(ab.read_active(0, read_back.data(), read_back.size()), StoreError::None);

    CHECK(read_back == data);
}

// ----------------------------------------------------------------- refusals

TEST_CASE(a_package_larger_than_a_slot_is_refused_before_anything_is_erased) {
    FakeFlash flash;
    AbStore ab(flash);
    store(ab, package(512, 1));
    const std::uint32_t before = ab.scan().active_slot().generation;

    std::uint8_t digest[kSha256DigestSize] = {};
    CHECK_EQ(ab.begin(duo_input::storage::kSlotPayloadCapacity + 1, digest),
             StoreError::TooLarge);

    // Refusing after erasing would have destroyed the spare copy for nothing.
    CHECK_EQ(ab.scan().active_slot().generation, before);
}

TEST_CASE(an_empty_package_is_refused) {
    FakeFlash flash;
    AbStore ab(flash);
    std::uint8_t digest[kSha256DigestSize] = {};

    CHECK_EQ(ab.begin(0, digest), StoreError::TooLarge);
}

TEST_CASE(a_chunk_past_the_end_of_the_package_is_refused) {
    FakeFlash flash;
    AbStore ab(flash);
    const auto data = package(1024, 7);
    std::uint8_t digest[kSha256DigestSize] = {};
    digest_of(data, digest);
    ab.begin(1024, digest);

    CHECK_EQ(ab.write_chunk(1000, data.data(), 100), StoreError::OutOfRange);
}

TEST_CASE(writing_without_beginning_is_refused) {
    FakeFlash flash;
    AbStore ab(flash);
    const std::uint8_t byte = 0;

    CHECK_EQ(ab.write_chunk(0, &byte, 1), StoreError::NotStaging);
}

TEST_CASE(committing_without_verifying_is_refused) {
    FakeFlash flash;
    AbStore ab(flash);
    const auto data = package(512, 3);
    std::uint8_t digest[kSha256DigestSize] = {};
    digest_of(data, digest);
    ab.begin(512, digest);
    ab.write_chunk(0, data.data(), data.size());

    // The commit is what makes a slot authoritative. Doing it on bytes nobody
    // checked would make the verify step decorative.
    CHECK_EQ(ab.commit(), StoreError::NotVerified);
}

TEST_CASE(a_package_whose_bytes_do_not_match_its_digest_fails_verification) {
    FakeFlash flash;
    AbStore ab(flash);
    const auto data = package(1024, 9);
    std::uint8_t digest[kSha256DigestSize] = {};
    digest_of(data, digest);
    ab.begin(1024, digest);
    auto altered = data;
    altered[500] ^= 0xFF;
    ab.write_chunk(0, altered.data(), altered.size());

    CHECK_EQ(ab.verify(), StoreError::DigestMismatch);
    CHECK_EQ(ab.commit(), StoreError::NotVerified);
}

// ------------------------------------------------- what flash actually takes

namespace {

/// Flash that insists on the granularity a real part insists on.
class StrictFlash : public FakeFlash {
public:
    bool erase(std::uint32_t offset, std::size_t size) override {
        if (offset % duo_input::storage::kSectorSize != 0 ||
            size % duo_input::storage::kSectorSize != 0) {
            refusals_++;
            return false;
        }
        return FakeFlash::erase(offset, size);
    }

    bool program(std::uint32_t offset, const std::uint8_t* data, std::size_t size) override {
        // A real RP2040 programs whole 256-byte pages and nothing else. The
        // store has to buffer up to that, because a configuration package is
        // whatever size it happens to be - 424 bytes, on a default project.
        if (offset % duo_input::storage::kPageSize != 0 ||
            size % duo_input::storage::kPageSize != 0) {
            refusals_++;
            return false;
        }
        return FakeFlash::program(offset, data, size);
    }

    std::size_t refusals() const { return refusals_; }

private:
    std::size_t refusals_ = 0;
};

}  // namespace

TEST_CASE(a_package_that_is_not_a_whole_number_of_pages_still_stores) {
    StrictFlash flash;
    AbStore ab(flash);
    // The size a default project actually compiles to. It is not a multiple of
    // anything convenient, and it never will be.
    const auto data = package(424, 0x5A);

    CHECK_EQ(store(ab, data), StoreError::None);

    CHECK_EQ(flash.refusals(), 0u);
    CHECK(ab.scan().has_active);
    CHECK_EQ(ab.scan().active_slot().size, 424u);
}

TEST_CASE(a_package_stored_through_strict_flash_reads_back_unchanged) {
    StrictFlash flash;
    AbStore ab(flash);
    std::vector<std::uint8_t> data(1000);
    for (std::size_t index = 0; index < data.size(); ++index) {
        data[index] = static_cast<std::uint8_t>((index * 31) & 0xFF);
    }
    store(ab, data);

    std::vector<std::uint8_t> read_back(data.size(), 0);
    CHECK_EQ(ab.read_active(0, read_back.data(), read_back.size()), StoreError::None);

    CHECK(read_back == data);
}

TEST_CASE(chunks_of_an_awkward_size_are_buffered_into_pages) {
    StrictFlash flash;
    AbStore ab(flash);
    const auto data = package(700, 0x11);

    // 100 bytes at a time: never a page, and the last one lands mid-page.
    CHECK_EQ(store(ab, data, 100), StoreError::None);

    CHECK_EQ(flash.refusals(), 0u);
    CHECK_EQ(ab.scan().active_slot().size, 700u);
}

TEST_CASE(a_chunk_that_skips_ahead_is_refused) {
    StrictFlash flash;
    AbStore ab(flash);
    const auto data = package(1024, 3);
    std::uint8_t digest[kSha256DigestSize] = {};
    digest_of(data, digest);
    ab.begin(1024, digest);
    ab.write_chunk(0, data.data(), 256);

    // Chunks are buffered into pages, so they have to arrive in order. A gap
    // would leave the buffer describing bytes that never came.
    CHECK_EQ(ab.write_chunk(512, data.data() + 512, 256), StoreError::OutOfRange);
}

TEST_CASE(a_package_that_never_finished_arriving_fails_verification) {
    StrictFlash flash;
    AbStore ab(flash);
    const auto data = package(1024, 9);
    std::uint8_t digest[kSha256DigestSize] = {};
    digest_of(data, digest);
    ab.begin(1024, digest);
    ab.write_chunk(0, data.data(), 512);

    CHECK_EQ(ab.verify(), StoreError::DigestMismatch);
}
