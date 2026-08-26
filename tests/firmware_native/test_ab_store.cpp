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

private:
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

TEST_CASE(a_package_with_a_hole_in_it_fails_verification) {
    FakeFlash flash;
    AbStore ab(flash);
    const auto data = package(1024, 9);
    std::uint8_t digest[kSha256DigestSize] = {};
    digest_of(data, digest);
    ab.begin(1024, digest);
    // The middle chunk never arrives.
    ab.write_chunk(0, data.data(), 256);
    ab.write_chunk(512, data.data() + 512, 512);

    CHECK_EQ(ab.verify(), StoreError::DigestMismatch);
}

TEST_CASE(aborting_leaves_the_running_configuration_alone) {
    FakeFlash flash;
    AbStore ab(flash);
    store(ab, package(512, 1));
    const std::uint32_t before = ab.scan().active_slot().generation;

    std::uint8_t digest[kSha256DigestSize] = {};
    ab.begin(512, digest);
    ab.abort();

    CHECK_EQ(ab.scan().active_slot().generation, before);
    CHECK_EQ(ab.scan().active, Slot::A);
}

// ------------------------------------------------------------- power cuts

TEST_CASE(power_lost_during_the_erase_leaves_the_old_configuration_running) {
    FakeFlash flash;
    AbStore ab(flash);
    const auto original = package(1024, 0x11);
    store(ab, original);
    const std::uint32_t before = ab.scan().active_slot().generation;

    // Die on the very next operation, which is the erase of the spare slot.
    flash.fail_from_operation(flash.operations());
    store(ab, package(1024, 0x22));

    AbStore after_reboot(flash);
    const ScanResult found = after_reboot.scan();
    CHECK(found.has_active);
    CHECK_EQ(found.active, Slot::A);
    CHECK_EQ(found.active_slot().generation, before);
}

TEST_CASE(power_lost_partway_through_the_erase_leaves_the_old_one_running) {
    FakeFlash flash;
    AbStore ab(flash);
    store(ab, package(1024, 0x11));

    flash.tear_operation(flash.operations() + 1);
    store(ab, package(1024, 0x22));

    AbStore after_reboot(flash);
    CHECK_EQ(after_reboot.scan().active, Slot::A);
}

TEST_CASE(power_lost_while_writing_the_payload_leaves_the_old_one_running) {
    FakeFlash flash;
    AbStore ab(flash);
    const auto original = package(4096, 0x11);
    store(ab, original);
    const std::uint32_t before = ab.scan().active_slot().generation;

    // Two operations in: past the erase, into the payload.
    flash.fail_from_operation(flash.operations() + 2);
    store(ab, package(4096, 0x22));

    AbStore after_reboot(flash);
    const ScanResult found = after_reboot.scan();
    CHECK_EQ(found.active, Slot::A);
    CHECK_EQ(found.active_slot().generation, before);
    // The half-written slot must not look like a configuration.
    CHECK_FALSE(found.slots[1].valid);
}

TEST_CASE(power_lost_just_before_the_header_leaves_the_old_one_running) {
    FakeFlash flash;
    AbStore ab(flash);
    store(ab, package(1024, 0x11));

    const auto data = package(1024, 0x22);
    std::uint8_t digest[kSha256DigestSize] = {};
    digest_of(data, digest);
    ab.begin(1024, digest);
    ab.write_chunk(0, data.data(), data.size());
    ab.verify();
    // Everything is in place and correct - and the commit never happens.
    flash.fail_from_operation(flash.operations());
    ab.commit();

    AbStore after_reboot(flash);
    CHECK_EQ(after_reboot.scan().active, Slot::A);
}

TEST_CASE(power_lost_during_the_header_itself_leaves_the_old_one_running) {
    FakeFlash flash;
    AbStore ab(flash);
    store(ab, package(1024, 0x11));

    const auto data = package(1024, 0x22);
    std::uint8_t digest[kSha256DigestSize] = {};
    digest_of(data, digest);
    ab.begin(1024, digest);
    ab.write_chunk(0, data.data(), data.size());
    ab.verify();
    // Half a header. This is the case the header CRC exists for: without it,
    // a torn header could read as a valid one describing the wrong bytes.
    flash.tear_operation(flash.operations() + 1);
    ab.commit();

    AbStore after_reboot(flash);
    const ScanResult found = after_reboot.scan();
    CHECK_EQ(found.active, Slot::A);
    CHECK_FALSE(found.slots[1].valid);
}

TEST_CASE(the_device_is_never_left_with_nothing_at_any_cut_point) {
    // The property that matters, checked at every operation rather than at the
    // few someone thought of.
    const auto original = package(2048, 0x11);
    const auto replacement = package(2048, 0x22);

    std::size_t total_operations = 0;
    {
        FakeFlash probe;
        AbStore ab(probe);
        store(ab, original);
        const std::size_t after_first = probe.operations();
        store(ab, replacement);
        total_operations = probe.operations() - after_first;
    }

    for (std::size_t cut = 1; cut <= total_operations; ++cut) {
        FakeFlash flash;
        AbStore ab(flash);
        store(ab, original);
        flash.fail_from_operation(flash.operations() + cut - 1);
        store(ab, replacement);

        AbStore after_reboot(flash);
        const ScanResult found = after_reboot.scan();
        CHECK(found.has_active);
        // Whichever it is, it must be one of the two whole packages.
        CHECK(found.active_slot().size == original.size());
    }
}

TEST_CASE(a_torn_program_at_any_point_still_leaves_a_usable_device) {
    const auto original = package(2048, 0x11);
    const auto replacement = package(2048, 0x22);

    std::size_t total_operations = 0;
    {
        FakeFlash probe;
        AbStore ab(probe);
        store(ab, original);
        const std::size_t after_first = probe.operations();
        store(ab, replacement);
        total_operations = probe.operations() - after_first;
    }

    for (std::size_t cut = 1; cut <= total_operations; ++cut) {
        FakeFlash flash;
        AbStore ab(flash);
        store(ab, original);
        flash.tear_operation(flash.operations() + cut);
        store(ab, replacement);

        AbStore after_reboot(flash);
        CHECK(after_reboot.scan().has_active);
    }
}

// -------------------------------------------------------------- corruption

TEST_CASE(a_slot_whose_payload_rotted_is_not_offered) {
    FakeFlash flash;
    AbStore ab(flash);
    store(ab, package(1024, 0x11));

    // Something flipped a bit in the stored bytes after the fact.
    std::uint8_t byte = 0;
    flash.read(duo_input::storage::kConfigAOffset + kSlotHeaderSize + 10, &byte, 1);
    byte ^= 0xFF;
    flash.program(duo_input::storage::kConfigAOffset + kSlotHeaderSize + 10, &byte, 1);

    AbStore after_reboot(flash);
    CHECK_EQ(after_reboot.verify_slot(Slot::A), StoreError::DigestMismatch);
}

TEST_CASE(the_newer_of_two_valid_slots_wins) {
    FakeFlash flash;
    AbStore ab(flash);
    store(ab, package(512, 1));
    store(ab, package(1024, 2));

    const ScanResult found = ab.scan();

    CHECK(found.slots[0].valid);
    CHECK(found.slots[1].valid);
    CHECK_EQ(found.active, Slot::B);
    CHECK_EQ(found.active_slot().size, 1024u);
}
