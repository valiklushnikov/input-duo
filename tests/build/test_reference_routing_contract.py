"""Build contract for Task 4: the reference target's link to U2.

Task 3 gave the reference host a keyboard and a mouse on PC1. Task 4 gives it
the second computer, and the only new thing on this board is *where* that link
runs: Core 0 owns SPI, Core 1 owns the USB host, and neither reaches into the
other. That ownership cannot be checked from a native test - main.cpp does not
build on a desktop - so it is checked here, against the source and against the
image that source produced.

What the routing itself does with an event is not asserted here. It is the
existing Core1Runtime and OutputRuntime code, tested against literal event
sequences in tests/firmware_native/.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
REFERENCE_BUILD = ROOT / "build" / "pico-pio-usb-reference-release"
REFERENCE_MAIN = ROOT / "firmware" / "u1_reference" / "main.cpp"
REFERENCE_CMAKE = ROOT / "firmware" / "u1_reference" / "CMakeLists.txt"

sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests" / "build"))

from reference_build_support import rebuild_reference_u1_artifacts


def _symbols(elf: Path) -> dict:
    from dump_usb_descriptors import Elf32

    assert elf.is_file(), (
        f"missing reference ELF: {elf}; build "
        "pico-pio-usb-reference-release first"
    )
    return Elf32(elf.read_bytes()).symbols()


def _main_bodies() -> tuple[str, str]:
    """core1_main's body and main's body, in that order."""

    source = REFERENCE_MAIN.read_text(encoding="utf-8")
    core1 = source[source.index("void core1_main") : source.index("int main(")]
    zero = source[source.index("int main(") :]
    return core1, zero


@pytest.fixture
def fresh_reference_artifacts(tmp_path):
    return rebuild_reference_u1_artifacts(
        root=ROOT,
        build_dir=REFERENCE_BUILD,
        backup_dir=tmp_path / "prior-reference-artifacts",
    )


def test_reference_elf_links_routing_but_not_the_old_host_backends(
    fresh_reference_artifacts,
):
    """The link to U2 is in the image, and nothing this task did not admit."""

    freshly_built_elf, _ = fresh_reference_artifacts
    symbols = _symbols(freshly_built_elf)

    # Mangled substrings rather than bare "SpiMaster": the class name alone is
    # satisfied by a vtable or a debug string, and what has to be in this image
    # is the code that starts the link, decides what PC2 is told, and lets go.
    for required in (
        "SpiMaster5begin",
        "SpiMaster13poll_snapshot",
        "SpiMaster16send_release_all",
    ):
        assert any(required in name for name in symbols), (
            f"{freshly_built_elf} contains no {required}"
        )

    # The output half of routing, which Task 3 admitted and Task 4 now has a
    # second consumer for.
    for required in (
        "OutputRuntime5drain",
        "OutputRuntime17keyboard_reported",
        "OutputRuntime13take_snapshot",
    ):
        assert any(required in name for name in symbols), (
            f"{freshly_built_elf} contains no {required}"
        )

    # Task 5 admits configuration and its core bridge; the failed PIO path and
    # CH375 are never coming back to this target.
    for excluded in ("Ch375Device", "PioUsbBackend", "ConfigService"):
        assert not any(excluded in name for name in symbols), (
            f"{freshly_built_elf} unexpectedly contains {excluded}"
        )


def test_the_link_is_started_on_core_zero_after_the_clock_is_final():
    """SPI's prescalers are computed against clk_peri as it is at begin().

    The reference target sets its final 120 MHz before anything else exists and
    then never moves the clock again - it has no pio_usb backend to reparent
    clk_peri underneath it. That is what makes a single begin() enough, and why
    it must come after set_sys_clock_khz rather than before it.
    """

    _, main_body = _main_bodies()

    ordered = [
        "set_sys_clock_khz(120000, true)",
        "multicore_launch_core1(core1_main)",
        "g_usb.begin()",
        "g_link.begin()",
        "g_usb.task()",
        "g_outputs.drain(",
        "g_usb.publish(g_outputs)",
        "g_link.poll(",
    ]

    position = -1
    for fragment in ordered:
        found = main_body.find(fragment)
        assert found != -1, f"main no longer contains {fragment!r}"
        assert found > position, (
            f"in main, {fragment!r} appears before something that must precede "
            "it; Core 0's order has changed"
        )
        position = found


def test_core_one_never_touches_the_link():
    """Core 1 drives tuh_task. An SPI transfer there is 512 us of host stall.

    It is also a second writer on a peripheral Core 0 owns, which no lock in
    this firmware protects.
    """

    core1_body, _ = _main_bodies()
    code = " ".join(line.split("//", 1)[0] for line in core1_body.splitlines())

    for forbidden in ("g_link", "SpiMaster"):
        assert forbidden not in code, (
            f"{forbidden} is used from core1_main, which owns the USB host"
        )


def test_a_u2_that_came_back_is_told_to_let_go():
    """U2 releases everything when the link dies; U1 has to agree with it.

    SpiMaster sends on change, so after U2 has released a key on its own the
    two ends disagree and nothing changes to correct it. The release is what
    makes them agree again. Whether it happens exactly once per reconnection is
    decided by LinkReconnect and tested natively; that it is wired in at all
    can only be seen here.
    """

    _, main_body = _main_bodies()
    assert "send_release_all" in main_body, (
        "main never tells U2 to let go; a reconnected endpoint keeps whatever "
        "U1 last thought it was holding"
    )


def test_the_reference_build_compiles_the_link_and_not_a_copy_of_it():
    """The same two translation units the shipping backends link."""

    cmake = REFERENCE_CMAKE.read_text(encoding="utf-8")
    for source in ("spi_master.cpp", "spi_master_poll.cpp"):
        assert "${_duo_u1_main_dir}/" + source in cmake, (
            f"the reference target does not link u1_main's {source}"
        )
