// SHA-256, checked against the vectors that define it.
//
// The firmware needs this because a configuration write is only trustworthy if
// the device can confirm, on its own, that what landed in flash is what the
// host meant to send. Trusting the host's digest without recomputing it would
// make the check a formality.
//
// The expected values here are the published FIPS 180-4 examples and the
// standard empty-input value. They are not "what our implementation produced";
// an implementation that agrees with itself proves nothing.

#include <cstring>
#include <string>

#include "crypto/sha256.hpp"
#include "test_support.hpp"

using duo_input::crypto::kSha256DigestSize;
using duo_input::crypto::Sha256;
using duo_input::crypto::sha256;

namespace {

std::string to_hex(const std::uint8_t (&digest)[kSha256DigestSize]) {
    static const char* digits = "0123456789abcdef";
    std::string text;
    for (std::size_t index = 0; index < kSha256DigestSize; ++index) {
        text.push_back(digits[digest[index] >> 4]);
        text.push_back(digits[digest[index] & 0x0F]);
    }
    return text;
}

std::string digest_of(const std::string& message) {
    std::uint8_t digest[kSha256DigestSize] = {};
    sha256(reinterpret_cast<const std::uint8_t*>(message.data()), message.size(), digest);
    return to_hex(digest);
}

}  // namespace

// ------------------------------------------------------------ known answers

TEST_CASE(the_empty_message_has_the_published_digest) {
    CHECK(digest_of("") ==
          "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
}

TEST_CASE(abc_has_the_published_digest) {
    // FIPS 180-4, one-block message.
    CHECK(digest_of("abc") ==
          "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
}

TEST_CASE(the_two_block_example_has_the_published_digest) {
    // FIPS 180-4, multi-block message: 448 bits, so the length pushes the
    // padding into a second block. This is the case a naive implementation
    // gets wrong.
    CHECK(digest_of("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq") ==
          "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
}

TEST_CASE(a_message_exactly_one_block_long_is_padded_into_a_second) {
    // 64 bytes: the block is full, so the padding and the length have nowhere
    // to go but a whole extra block.
    CHECK(digest_of(std::string(64, 'a')) ==
          "ffe054fe7ae0cb6dc65c3af9b61d5209f439851db43d0ba5997337df154668eb");
}

TEST_CASE(a_message_of_fifty_five_bytes_still_fits_one_block) {
    // 55 bytes is the largest message whose padding and 8-byte length fit in
    // the same block; 56 is the smallest that does not.
    CHECK(digest_of(std::string(55, 'a')) ==
          "9f4390f8d30c2dd92ec9f095b65e2b9ae9b0a925a5258e241c9f1e910f734318");
    CHECK(digest_of(std::string(56, 'a')) ==
          "b35439a4ac6f0948b6d6f9e3c6af0f5f590ce20f1bde7090ef7970686ec6738a");
}

TEST_CASE(a_long_message_has_the_published_digest) {
    CHECK(digest_of(std::string(1000000, 'a')) ==
          "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0");
}

// ------------------------------------------------------------ streaming use

TEST_CASE(feeding_a_message_in_pieces_gives_the_same_digest) {
    // The configuration arrives in 512-byte chunks, so the streaming path is
    // the one that actually runs.
    // Split from the one message rather than typed out twice: hand-copied
    // halves are how a test ends up hashing something other than what it
    // claims, and reporting an implementation bug that is not there.
    const std::string message = "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq";
    const std::size_t split = 24;

    Sha256 streamed;
    streamed.update(reinterpret_cast<const std::uint8_t*>(message.data()), split);
    streamed.update(reinterpret_cast<const std::uint8_t*>(message.data() + split),
                    message.size() - split);

    std::uint8_t digest[kSha256DigestSize] = {};
    streamed.finish(digest);

    CHECK(to_hex(digest) ==
          "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
}

TEST_CASE(one_byte_at_a_time_gives_the_same_digest) {
    Sha256 streamed;
    const std::string message = "abc";
    for (char letter : message) {
        streamed.update(reinterpret_cast<const std::uint8_t*>(&letter), 1);
    }

    std::uint8_t digest[kSha256DigestSize] = {};
    streamed.finish(digest);

    CHECK(to_hex(digest) ==
          "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
}

TEST_CASE(updating_with_nothing_changes_nothing) {
    Sha256 streamed;
    streamed.update(nullptr, 0);
    streamed.update(reinterpret_cast<const std::uint8_t*>("abc"), 3);
    streamed.update(nullptr, 0);

    std::uint8_t digest[kSha256DigestSize] = {};
    streamed.finish(digest);

    CHECK(to_hex(digest) ==
          "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
}

TEST_CASE(a_reset_hasher_starts_over) {
    Sha256 streamed;
    streamed.update(reinterpret_cast<const std::uint8_t*>("nonsense"), 8);

    streamed.reset();
    streamed.update(reinterpret_cast<const std::uint8_t*>("abc"), 3);

    std::uint8_t digest[kSha256DigestSize] = {};
    streamed.finish(digest);

    CHECK(to_hex(digest) ==
          "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
}

// ------------------------------------------------------------ what it is for

TEST_CASE(one_changed_byte_changes_the_digest) {
    // The whole point: a configuration that arrived with one bit flipped must
    // not verify.
    std::string original(4096, 'x');
    std::string altered = original;
    altered[2048] = 'y';

    CHECK(digest_of(original) != digest_of(altered));
}

TEST_CASE(the_digest_is_the_size_the_protocol_expects) {
    CHECK_EQ(kSha256DigestSize, 32u);
}
