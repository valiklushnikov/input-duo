#include "protocol/cobs.hpp"

#include <cstdint>

namespace duo_input::protocol {

namespace {

constexpr std::uint8_t COBS_MAX_CODE = 0xFF;

bool has_valid_data(ByteView view) {
    return view.size == 0 || view.data != nullptr;
}

bool has_valid_data(MutableByteView view) {
    return view.size == 0 || view.data != nullptr;
}

}  // namespace

bool cobs_encode(ByteView input, MutableByteView output, std::size_t& output_size) {
    if (!has_valid_data(input) || !has_valid_data(output)) {
        return false;
    }

    const std::size_t required_size = input.size + (input.size / 254U) + 1U;
    if (output.size < required_size) {
        return false;
    }

    std::size_t read_index = 0;
    std::size_t write_index = 1;
    std::size_t code_index = 0;
    std::uint8_t code = 1;

    while (read_index < input.size) {
        const std::uint8_t byte = input.data[read_index++];
        if (byte == 0) {
            output.data[code_index] = code;
            code_index = write_index++;
            code = 1;
        } else {
            output.data[write_index++] = byte;
            ++code;
            if (code == COBS_MAX_CODE) {
                output.data[code_index] = code;
                code_index = write_index++;
                code = 1;
            }
        }
    }

    output.data[code_index] = code;
    output_size = write_index;
    return true;
}

bool cobs_decode(ByteView input, MutableByteView output, std::size_t& output_size) {
    if (!has_valid_data(input) || !has_valid_data(output) || input.size == 0) {
        return false;
    }

    std::size_t read_index = 0;
    std::size_t decoded_size = 0;
    while (read_index < input.size) {
        const std::uint8_t code = input.data[read_index++];
        if (code == 0) {
            return false;
        }

        const std::size_t block_size = static_cast<std::size_t>(code - 1U);
        if (block_size > input.size - read_index) {
            return false;
        }
        read_index += block_size;
        decoded_size += block_size;
        if (read_index < input.size && code != COBS_MAX_CODE) {
            ++decoded_size;
        }
    }

    if (output.size < decoded_size) {
        return false;
    }

    read_index = 0;
    std::size_t write_index = 0;
    while (read_index < input.size) {
        const std::uint8_t code = input.data[read_index++];
        const std::size_t block_size = static_cast<std::size_t>(code - 1U);
        for (std::size_t index = 0; index < block_size; ++index) {
            output.data[write_index++] = input.data[read_index++];
        }
        if (read_index < input.size && code != COBS_MAX_CODE) {
            output.data[write_index++] = 0;
        }
    }

    output_size = write_index;
    return true;
}

}  // namespace duo_input::protocol
