# Pins the PIO USB build to exact, independently verified clones under
# .deps/, without disturbing the CH375 build's toolchain in any way.
#
# Task 1's baseline recorded that the CH375 `pico-release` build resolves
# PICO_SDK_PATH from the shell environment to Pico SDK 2.1.0 (95ea6ac). That
# is deliberately left alone: this file is included only when
# DUO_INPUT_BACKEND is PIO_USB or PIO_USB_REFERENCE, and it never touches
# PICO_SDK_PATH for a CH375 configure.
#
# The PIO USB backend instead needs Pico SDK 2.3.0, TinyUSB and Pico-PIO-USB
# at exact revisions. tools/bootstrap_pio_usb_toolchain.ps1 clones/fetches
# those three into the git-ignored .deps/ directory and already verifies
# each one both ways this file does. This file verifies again at configure
# time, so a stale, hand-edited or half-updated .deps/ tree fails
# configuration loudly instead of silently building against the wrong
# revision.

set(_duo_pio_usb_deps_dir "${CMAKE_SOURCE_DIR}/.deps")

# The three revisions the task brief names, character for character.
set(DUO_PIO_USB_PICO_SDK_REVISION "98a542c1a62fb549ffb5d66a3e5892b06276b670")
set(DUO_PIO_USB_TINYUSB_REVISION "86ad6e56c1700e85f1c5678607a762cfe3aa2f47")
set(DUO_PIO_USB_PICO_PIO_USB_REVISION "3c1eec341a5232640e4c00628b889b641af34b28")

find_package(Git QUIET)
if(NOT GIT_FOUND)
    message(FATAL_ERROR
        "git is required to verify the PIO USB toolchain revisions pinned in "
        "cmake/pio_usb_toolchain_lock.cmake, and was not found.")
endif()

# Checks that `dir` is a git checkout of exactly `expected_revision`, with no
# uncommitted changes, and fails configuration otherwise - a mismatch here
# must stop the build, not fall back to whatever `dir` happens to contain
# (e.g. main).
#
# `git rev-parse HEAD` alone is not sufficient: a file edited in place inside
# an already-correct checkout does not move HEAD, so a hand-edited tree would
# otherwise still read as "verified" - exactly the case this function exists
# to catch. `git status --porcelain` is what actually answers whether the
# tree on disk still matches that commit.
function(_duo_pio_usb_verify_clone label dir expected_revision out_verified_path)
    if(NOT EXISTS "${dir}")
        message(FATAL_ERROR
            "${label} is not present at '${dir}'. Run "
            "tools\\bootstrap_pio_usb_toolchain.ps1 to clone the pinned PIO "
            "USB toolchain into .deps/ before configuring this preset.")
    endif()

    execute_process(
        COMMAND "${GIT_EXECUTABLE}" -C "${dir}" rev-parse HEAD
        OUTPUT_VARIABLE _duo_actual_revision
        OUTPUT_STRIP_TRAILING_WHITESPACE
        RESULT_VARIABLE _duo_git_status
        ERROR_VARIABLE _duo_git_error
    )
    if(NOT _duo_git_status EQUAL 0)
        message(FATAL_ERROR
            "${label} at '${dir}' is not a usable git checkout "
            "(git rev-parse HEAD failed: ${_duo_git_error}). Re-run "
            "tools\\bootstrap_pio_usb_toolchain.ps1.")
    endif()

    if(NOT _duo_actual_revision STREQUAL expected_revision)
        message(FATAL_ERROR
            "${label} at '${dir}' is checked out to ${_duo_actual_revision}, "
            "not the pinned ${expected_revision}. Configuration refuses to "
            "silently build against whatever revision happens to be "
            "there - re-run tools\\bootstrap_pio_usb_toolchain.ps1 to fix it.")
    endif()

    execute_process(
        COMMAND "${GIT_EXECUTABLE}" -C "${dir}" status --porcelain
        OUTPUT_VARIABLE _duo_dirty_status
        OUTPUT_STRIP_TRAILING_WHITESPACE
        RESULT_VARIABLE _duo_git_status_status
        ERROR_VARIABLE _duo_git_status_error
    )
    if(NOT _duo_git_status_status EQUAL 0)
        message(FATAL_ERROR
            "${label} at '${dir}': git status failed "
            "(${_duo_git_status_error}). Re-run "
            "tools\\bootstrap_pio_usb_toolchain.ps1.")
    endif()
    if(NOT _duo_dirty_status STREQUAL "")
        message(FATAL_ERROR
            "${label} at '${dir}' is checked out to the pinned "
            "${expected_revision}, but has uncommitted changes "
            "(git status --porcelain is not empty), so the tree on disk may "
            "not actually match that commit. Configuration refuses to build "
            "against a hand-edited clone - discard the changes or re-run "
            "tools\\bootstrap_pio_usb_toolchain.ps1 to get a clean one.")
    endif()

    set(${out_verified_path} "${dir}" PARENT_SCOPE)
endfunction()

_duo_pio_usb_verify_clone("Pico SDK"
    "${_duo_pio_usb_deps_dir}/pico-sdk"
    "${DUO_PIO_USB_PICO_SDK_REVISION}"
    _duo_pico_sdk_path)
_duo_pio_usb_verify_clone("TinyUSB"
    "${_duo_pio_usb_deps_dir}/tinyusb"
    "${DUO_PIO_USB_TINYUSB_REVISION}"
    _duo_tinyusb_path)
_duo_pio_usb_verify_clone("Pico-PIO-USB"
    "${_duo_pio_usb_deps_dir}/pico-pio-usb"
    "${DUO_PIO_USB_PICO_PIO_USB_REVISION}"
    _duo_pico_pio_usb_path)

# PICO_SDK_PATH is read by cmake/pico_sdk_import.cmake, included right after
# this file. PICO_TINYUSB_PATH is a documented Pico SDK override point (see
# the PICO_CMAKE_CONFIG comment in <PICO_SDK_PATH>/src/rp2_common/tinyusb/
# CMakeLists.txt) that lets TinyUSB come from somewhere other than the SDK's
# own lib/tinyusb submodule - which is exactly what is needed here, since
# that submodule is pinned to a different TinyUSB revision than this build
# wants. PICO_PIO_USB_PATH selects the verified Pico-PIO-USB clone for both
# TinyUSB's PIO host-controller integration and the frozen reference target's
# direct add_subdirectory().
#
# FORCE, so a stale value cached from an earlier, differently-configured
# build directory cannot linger and silently win.
set(PICO_SDK_PATH "${_duo_pico_sdk_path}" CACHE PATH
    "Path to the Raspberry Pi Pico SDK (PIO USB build; verified clone under .deps/)" FORCE)
set(PICO_TINYUSB_PATH "${_duo_tinyusb_path}" CACHE PATH
    "Path to TinyUSB (PIO USB build; verified clone under .deps/)" FORCE)
set(PICO_PIO_USB_PATH "${_duo_pico_pio_usb_path}" CACHE PATH
    "Path to Pico-PIO-USB (PIO USB build; verified clone under .deps/)" FORCE)

message(STATUS
    "PIO USB toolchain verified: Pico SDK ${DUO_PIO_USB_PICO_SDK_REVISION}, "
    "TinyUSB ${DUO_PIO_USB_TINYUSB_REVISION}, "
    "Pico-PIO-USB ${DUO_PIO_USB_PICO_PIO_USB_REVISION}")
