#include <array>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <system_error>

#ifndef DUO_FUZZ_CORPUS_DIR
#error "DUO_FUZZ_CORPUS_DIR must identify this harness corpus directory"
#endif

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size);

int main() {
    constexpr std::size_t MAX_SEED_BYTES = 4096U;
    std::array<std::uint8_t, MAX_SEED_BYTES> buffer{};
    std::error_code error;
    std::size_t seed_count = 0U;
    const std::filesystem::path corpus{DUO_FUZZ_CORPUS_DIR};
    std::filesystem::directory_iterator iterator{corpus, error};
    const std::filesystem::directory_iterator end{};
    if (error) {
        return 1;
    }
    while (iterator != end) {
        if (iterator->is_regular_file(error)) {
            const std::uintmax_t file_size = iterator->file_size(error);
            if (error || file_size > buffer.size()) {
                return 2;
            }
            std::ifstream input{iterator->path(), std::ios::binary};
            if (!input) {
                return 3;
            }
            input.read(reinterpret_cast<char*>(buffer.data()), static_cast<std::streamsize>(file_size));
            if (!input || LLVMFuzzerTestOneInput(buffer.data(), static_cast<std::size_t>(file_size)) != 0) {
                return 4;
            }
            ++seed_count;
        }
        iterator.increment(error);
        if (error) {
            return 5;
        }
    }
    return seed_count == 0U ? 6 : 0;
}
