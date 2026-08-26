# Building the Duo Input firmware

Two UF2 images, one per board. The host test build and the firmware build
cannot share a build directory — one cross-compiles for ARM and the other
compiles for this machine — so `CMakeLists.txt` refuses that combination rather
than producing half of each.

## What you need

| Tool | Version used | Where it comes from |
|---|---|---|
| Raspberry Pi Pico SDK | 2.1.0 | `git clone --branch 2.1.0 https://github.com/raspberrypi/pico-sdk` |
| TinyUSB | SDK submodule | `git submodule update --init lib/tinyusb` inside the SDK |
| arm-none-eabi GCC | 10.3-2021.10 | `choco install gcc-arm-embedded` |
| A host C++ compiler | MSVC 14.4x | Visual Studio Build Tools — **required**, see below |
| CMake | 3.21+ | — |
| Ninja | any | — |

### Why a host compiler is needed for a cross build

The SDK builds `picotool` — a host program — to turn an ELF into a UF2. So the
firmware build needs *both* toolchains present: the ARM one for the firmware
and the host one for picotool. On Windows that means running the build from a
shell where `vcvars64.bat` has been sourced. Without it the configure step
fails with `No CMAKE_C_COMPILER could be found` from picotool's own
`CMakeLists.txt`, which is confusing until you know it is not your project
failing.

## Build

```bat
call "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
set PICO_SDK_PATH=C:\Users\<you>\pico-sdk
set PICO_TOOLCHAIN_PATH=C:\ProgramData\chocolatey\lib\gcc-arm-embedded\tools\gcc-arm-none-eabi-10.3-2021.10
set PATH=%PICO_TOOLCHAIN_PATH%\bin;%PATH%

cmake --preset pico-release
cmake --build --preset pico-release --parallel
python -m pytest tests/build/test_firmware_artifacts.py -q
```

Output:

- `build/pico-release/firmware/u1_main/duo_u1_main.uf2`
- `build/pico-release/firmware/u2_endpoint/duo_u2_endpoint.uf2`

## The build contract

`tests/build/test_firmware_artifacts.py` reads the UF2 files themselves rather
than a linker map, because a UF2 block states the exact flash address its
payload is written to — which is the thing that actually matters.

It asserts:

- exactly two images are produced, and nothing else;
- each starts at `0x10000000`, the beginning of flash;
- each ends below `0x10100000`.

That last one is not a style rule. U1 keeps its configuration in two 384 KiB
slots starting at 1 MiB. Firmware that grows past the boundary does not fail to
link — it silently begins overwriting the operator's configuration the first
time one is written. The check exists to catch that at build time instead of in
someone's hands.

## Presets

| Preset | What it builds |
|---|---|
| `native` | firmware logic compiled for this machine, with CTest |
| `pico-release` | both UF2 images, MinSizeRel |
| `pico-debug` | the same images with symbols, for a debug probe |

## Flashing

See `docs/user/uf2-update-ru.md`. Both boards take the same procedure and
different files; the images are not interchangeable.
