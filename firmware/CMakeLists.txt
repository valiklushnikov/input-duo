# The two firmware images.
#
# Both are built only when the Pico SDK is present. The native test build
# consumes firmware/common on its own, without a cross compiler, so the shared
# code stays testable on a developer machine that has no ARM toolchain at all -
# which is most of them, most of the time.

add_subdirectory(u1_main)
add_subdirectory(u2_endpoint)
