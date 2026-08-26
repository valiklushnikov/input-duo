#include "crypto/sha256.hpp"

#include <cstring>

namespace duo_input::crypto {
namespace {

// FIPS 180-4 section 4.2.2: the first 32 bits of the fractional parts of the
// cube roots of the first 64 primes.
constexpr std::uint32_t kRoundConstants[64] = {
    0x428A2F98, 0x71374491, 0xB5C0FBCF, 0xE9B5DBA5, 0x3956C25B, 0x59F111F1,
    0x923F82A4, 0xAB1C5ED5, 0xD807AA98, 0x12835B01, 0x243185BE, 0x550C7DC3,
    0x72BE5D74, 0x80DEB1FE, 0x9BDC06A7, 0xC19BF174, 0xE49B69C1, 0xEFBE4786,
    0x0FC19DC6, 0x240CA1CC, 0x2DE92C6F, 0x4A7484AA, 0x5CB0A9DC, 0x76F988DA,
    0x983E5152, 0xA831C66D, 0xB00327C8, 0xBF597FC7, 0xC6E00BF3, 0xD5A79147,
    0x06CA6351, 0x14292967, 0x27B70A85, 0x2E1B2138, 0x4D2C6DFC, 0x53380D13,
    0x650A7354, 0x766A0ABB, 0x81C2C92E, 0x92722C85, 0xA2BFE8A1, 0xA81A664B,
    0xC24B8B70, 0xC76C51A3, 0xD192E819, 0xD6990624, 0xF40E3585, 0x106AA070,
    0x19A4C116, 0x1E376C08, 0x2748774C, 0x34B0BCB5, 0x391C0CB3, 0x4ED8AA4A,
    0x5B9CCA4F, 0x682E6FF3, 0x748F82EE, 0x78A5636F, 0x84C87814, 0x8CC70208,
    0x90BEFFFA, 0xA4506CEB, 0xBEF9A3F7, 0xC67178F2,
};

std::uint32_t rotate_right(std::uint32_t value, unsigned bits) {
    return (value >> bits) | (value << (32 - bits));
}

std::uint32_t big_endian(const std::uint8_t* bytes) {
    return (static_cast<std::uint32_t>(bytes[0]) << 24) |
           (static_cast<std::uint32_t>(bytes[1]) << 16) |
           (static_cast<std::uint32_t>(bytes[2]) << 8) |
           static_cast<std::uint32_t>(bytes[3]);
}

void put_big_endian(std::uint8_t* bytes, std::uint32_t value) {
    bytes[0] = static_cast<std::uint8_t>(value >> 24);
    bytes[1] = static_cast<std::uint8_t>(value >> 16);
    bytes[2] = static_cast<std::uint8_t>(value >> 8);
    bytes[3] = static_cast<std::uint8_t>(value);
}

}  // namespace

void Sha256::reset() {
    // FIPS 180-4 section 5.3.3: the fractional parts of the square roots of
    // the first eight primes.
    state_[0] = 0x6A09E667;
    state_[1] = 0xBB67AE85;
    state_[2] = 0x3C6EF372;
    state_[3] = 0xA54FF53A;
    state_[4] = 0x510E527F;
    state_[5] = 0x9B05688C;
    state_[6] = 0x1F83D9AB;
    state_[7] = 0x5BE0CD19;
    buffered_ = 0;
    total_bits_ = 0;
    std::memset(buffer_, 0, sizeof(buffer_));
}

void Sha256::absorb_block(const std::uint8_t* block) {
    std::uint32_t schedule[64];
    for (std::size_t index = 0; index < 16; ++index) {
        schedule[index] = big_endian(block + index * 4);
    }
    for (std::size_t index = 16; index < 64; ++index) {
        const std::uint32_t s0 = rotate_right(schedule[index - 15], 7) ^
                                 rotate_right(schedule[index - 15], 18) ^
                                 (schedule[index - 15] >> 3);
        const std::uint32_t s1 = rotate_right(schedule[index - 2], 17) ^
                                 rotate_right(schedule[index - 2], 19) ^
                                 (schedule[index - 2] >> 10);
        schedule[index] = schedule[index - 16] + s0 + schedule[index - 7] + s1;
    }

    std::uint32_t a = state_[0];
    std::uint32_t b = state_[1];
    std::uint32_t c = state_[2];
    std::uint32_t d = state_[3];
    std::uint32_t e = state_[4];
    std::uint32_t f = state_[5];
    std::uint32_t g = state_[6];
    std::uint32_t h = state_[7];

    for (std::size_t round = 0; round < 64; ++round) {
        const std::uint32_t s1 =
            rotate_right(e, 6) ^ rotate_right(e, 11) ^ rotate_right(e, 25);
        const std::uint32_t choose = (e & f) ^ (~e & g);
        const std::uint32_t temp1 = h + s1 + choose + kRoundConstants[round] + schedule[round];
        const std::uint32_t s0 =
            rotate_right(a, 2) ^ rotate_right(a, 13) ^ rotate_right(a, 22);
        const std::uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
        const std::uint32_t temp2 = s0 + majority;

        h = g;
        g = f;
        f = e;
        e = d + temp1;
        d = c;
        c = b;
        b = a;
        a = temp1 + temp2;
    }

    state_[0] += a;
    state_[1] += b;
    state_[2] += c;
    state_[3] += d;
    state_[4] += e;
    state_[5] += f;
    state_[6] += g;
    state_[7] += h;
}

void Sha256::update(const std::uint8_t* data, std::size_t size) {
    if (data == nullptr || size == 0) {
        return;
    }
    total_bits_ += static_cast<std::uint64_t>(size) * 8;

    // Top up a partly filled block first, then take whole blocks straight from
    // the caller's buffer without copying them.
    if (buffered_ != 0) {
        const std::size_t needed = kSha256BlockSize - buffered_;
        const std::size_t taken = size < needed ? size : needed;
        std::memcpy(buffer_ + buffered_, data, taken);
        buffered_ += taken;
        data += taken;
        size -= taken;
        if (buffered_ == kSha256BlockSize) {
            absorb_block(buffer_);
            buffered_ = 0;
        }
    }

    while (size >= kSha256BlockSize) {
        absorb_block(data);
        data += kSha256BlockSize;
        size -= kSha256BlockSize;
    }

    if (size != 0) {
        std::memcpy(buffer_, data, size);
        buffered_ = size;
    }
}

void Sha256::finish(std::uint8_t (&digest)[kSha256DigestSize]) {
    const std::uint64_t length_bits = total_bits_;

    // A single 1 bit, then zeros, then the 64-bit length. When the length will
    // not fit in the block being closed, it goes in a whole extra one.
    const std::uint8_t one = 0x80;
    update(&one, 1);
    total_bits_ = length_bits;  // the padding is not part of the message

    const std::uint8_t zero = 0;
    while (buffered_ != kSha256BlockSize - 8) {
        update(&zero, 1);
        total_bits_ = length_bits;
    }

    std::uint8_t length_bytes[8];
    for (std::size_t index = 0; index < 8; ++index) {
        length_bytes[index] = static_cast<std::uint8_t>(length_bits >> (56 - index * 8));
    }
    std::memcpy(buffer_ + buffered_, length_bytes, sizeof(length_bytes));
    absorb_block(buffer_);
    buffered_ = 0;

    for (std::size_t index = 0; index < 8; ++index) {
        put_big_endian(digest + index * 4, state_[index]);
    }
}

void sha256(const std::uint8_t* data, std::size_t size,
            std::uint8_t (&digest)[kSha256DigestSize]) {
    Sha256 hasher;
    hasher.update(data, size);
    hasher.finish(digest);
}

bool digests_equal(const std::uint8_t* left, const std::uint8_t* right) {
    std::uint8_t difference = 0;
    for (std::size_t index = 0; index < kSha256DigestSize; ++index) {
        difference |= static_cast<std::uint8_t>(left[index] ^ right[index]);
    }
    return difference == 0;
}

}  // namespace duo_input::crypto
