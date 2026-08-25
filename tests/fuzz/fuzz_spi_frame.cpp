#include "protocol/frame.hpp"

#include <cstddef>
#include <cstdint>
#include <cstdlib>

namespace {

bool view_is_within(duo_input::protocol::ByteView view, const std::uint8_t* begin,
                    std::size_t size) {
    if (view.data == nullptr || begin == nullptr) {
        return false;
    }
    const std::uintptr_t view_begin = reinterpret_cast<std::uintptr_t>(view.data);
    const std::uintptr_t owner_begin = reinterpret_cast<std::uintptr_t>(begin);
    const std::uintptr_t owner_end = owner_begin + size;
    return view_begin >= owner_begin && view_begin <= owner_end &&
           view.size <= owner_end - view_begin;
}

}  // namespace

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    duo_input::protocol::DecodeResult result{};
    if (!duo_input::protocol::decode_spi_frame({data, size}, result)) {
        return 0;
    }
    if (!view_is_within(result.spi.payload, data, size)) {
        std::abort();
    }
    return 0;
}
