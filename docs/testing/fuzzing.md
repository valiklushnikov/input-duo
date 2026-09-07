# Parser fuzzing

The CDC, SPI, and configuration parsers have independent libFuzzer targets.  Each target calls
only its public parser API and aborts if an accepted borrowed view points outside its caller-owned
input or CDC scratch buffer.  Production parser code is not duplicated or prevalidated by a
harness.

## Portable corpus smoke

The committed corpus is generated deterministically from `tests/vectors/frame_vectors.json` and
`tests/vectors/config_vectors/valid_minimal.bin`.  Recreate it after changing either vector:

```powershell
python tests/fuzz/generate_corpus.py
python tests/fuzz/generate_corpus.py --check
cmake -S . -B build/native-fuzz-smoke -G Ninja -DDUO_NATIVE_TESTS=ON
cmake --build build/native-fuzz-smoke
ctest --test-dir build/native-fuzz-smoke --output-on-failure -R '^fuzz_corpus_'
```

These smoke targets are intentionally built only for `DUO_NATIVE_TESTS=ON`; a
`DUO_FUZZ_TESTS=ON` Clang build contains only libFuzzer targets, so it does not link its
sanitizer-instrumented parser library into non-sanitized smoke executables. The two options are
mutually exclusive; use separate build directories for the native corpus smoke and fuzz campaign.

The generator's `--check` mode verifies exact byte content and rejects extra corpus files.  Each
seed is below 4 KiB.  CDC's `valid-shared-vector.bin` and SPI's equivalent are byte-for-byte
copies of the shared frame vectors; configuration's `valid-minimal-shared-vector.bin` is a
byte-for-byte copy of the minimal committed configuration vector.  The other seeds cover declared
length 0, legal maximum, maximum plus one, and `0xFFFF` with separately named valid and corrupt
CRC variants; a valid-header config seed whose only mutation is its CRC field; header/payload/CRC
truncations; and unknown type or nonzero flags.

## Sanitizer campaign

Use a Clang installation that includes libFuzzer.  `DUO_FUZZ_TESTS` is deliberately OFF by default
and fails CMake configuration under non-Clang compilers instead of producing unfuzzed lookalikes.

```powershell
cmake -S . -B build/fuzz-clang -G Ninja -DDUO_FUZZ_TESTS=ON -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_CXX_COMPILER=clang++
cmake --build build/fuzz-clang
build/fuzz-clang/tests/fuzz/duo_fuzz_cdc_frame.exe -max_total_time=60 tests/fuzz/corpus/cdc
build/fuzz-clang/tests/fuzz/duo_fuzz_spi_frame.exe -max_total_time=60 tests/fuzz/corpus/spi
build/fuzz-clang/tests/fuzz/duo_fuzz_config.exe -max_total_time=120 tests/fuzz/corpus/config
```

These targets compile and link with `-fsanitize=fuzzer,address,undefined`, frame pointers, and
debug information. `RelWithDebInfo` also selects the release STL ABI expected
by LLVM's Windows libFuzzer runtime; the targets select its matching static
CRT automatically on MSVC-ABI Clang. Send crash artifacts to a build directory
rather than the committed corpus.
