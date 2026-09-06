# The two firmware images.
#
# Both are built only when the Pico SDK is present. The native test build
# consumes firmware/common on its own, without a cross compiler, so the shared
# code stays testable on a developer machine that has no ARM toolchain at all -
# which is most of them, most of the time.


# The build date the SDK stamps into both binary info blocks. Derived from the
# source (SOURCE_DATE_EPOCH, else the HEAD commit) rather than read off the
# clock, so that one commit has one image. See cmake/source_date.cmake.
include(${CMAKE_SOURCE_DIR}/cmake/source_date.cmake)
duo_source_date_string(DUO_PROGRAM_BUILD_DATE DUO_PROGRAM_BUILD_DATE_ORIGIN)
message(STATUS "Firmware build date: ${DUO_PROGRAM_BUILD_DATE} (from ${DUO_PROGRAM_BUILD_DATE_ORIGIN})")
add_compile_definitions(PICO_PROGRAM_BUILD_DATE="${DUO_PROGRAM_BUILD_DATE}")

if(DUO_INPUT_BACKEND STREQUAL "PIO_USB_REFERENCE")
    add_subdirectory(u1_reference)
else()
    add_subdirectory(u1_main)
endif()
add_subdirectory(u2_endpoint)
