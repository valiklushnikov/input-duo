#pragma once

// SHA-256, streaming, no allocation.
//
// The device needs this to answer one question for itself: is what landed in
// flash what the host meant to send? Taking the host's word for it would make
// the verify step a formality - the digest would confirm only that the host
// can compute a digest.
//
// Streaming rather than one-shot because a configuration arrives in 512-byte
// chunks and is up to 360 KiB; there is nowhere on this chip to hold it all
// twice.

#include <cstddef>
#include <cstdint>

namespace duo_input::crypto {

inline constexpr std::size_t kSha256DigestSize = 32;
inline constexpr std::size_t kSha256BlockSize = 64;

class Sha256 {
public:
    Sha256() { reset(); }

    /// Start over.
    void reset();

    /// Absorb ``size`` bytes. Safe to call with a null pointer and zero size.
    void update(const std::uint8_t* data, std::size_t size);

    /// Finish and write the digest. The hasher must be reset before reuse.
    void finish(std::uint8_t (&digest)[kSha256DigestSize]);

private:
    void absorb_block(const std::uint8_t* block);

    std::uint32_t state_[8] = {};
    std::uint8_t buffer_[kSha256BlockSize] = {};
    std::size_t buffered_ = 0;
    std::uint64_t total_bits_ = 0;
};

/// One-shot convenience for the places that already have the whole message.
void sha256(const std::uint8_t* data, std::size_t size,
            std::uint8_t (&digest)[kSha256DigestSize]);

/// Compare two digests without an early exit.
///
/// Constant time in the length, so a mismatch reveals nothing about which byte
/// differed. Nothing here is secret today, but a digest comparison that leaks
/// its position is the kind of thing that becomes a problem after someone
/// reuses it for something that is.
bool digests_equal(const std::uint8_t* left, const std::uint8_t* right);

}  // namespace duo_input::crypto
