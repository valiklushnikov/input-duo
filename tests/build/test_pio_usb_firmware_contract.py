"""What the PIO USB dual-role scaffold must actually build, not merely claim to.

Task 5 makes ``firmware/u1_main/pio_usb/backend.cpp`` exist, which is the file
whose absence made every PIO USB configure stop deliberately (see the
``FATAL_ERROR`` in the repository's top-level ``CMakeLists.txt``). Once it
exists, the CH375 build must still be the CH375 build - not a byte of TinyUSB
host code - and the PIO USB build must actually link a TinyUSB host, PIO
USB's own controller driver, and the InputPipeline boundary it will one day
feed, while linking none of CH375's sources.

Two kinds of evidence, in one file because they check one split:

* The CMake inputs (``firmware/u1_main/CMakeLists.txt``,
  ``firmware/u1_main/tusb_config.h``) - read directly, the way a human
  reviewing a diff would, and cheap enough to run on every ``pytest`` pass
  with no build required.
* The linked ELF, for each backend - inspected with the same
  ``dump_usb_descriptors.Elf32`` reader ``tests/build/test_firmware_artifacts.py``
  already uses on the CH375 image, so a misplaced preprocessor guard that
  still looks plausible in ``main.cpp`` cannot pass by looking at source
  text alone. Skipped when the corresponding build does not exist locally.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "tools"))


# ------------------------------------------------------------- CMake inputs

def _u1_cmakelists_text() -> str:
    return (REPOSITORY_ROOT / "firmware" / "u1_main" / "CMakeLists.txt").read_text(encoding="utf-8")


def _tusb_config_text() -> str:
    return (REPOSITORY_ROOT / "firmware" / "u1_main" / "tusb_config.h").read_text(encoding="utf-8")


def _source_text(relative: str) -> str:
    return (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")


#: Both the source list and the target_link_libraries() call in
#: firmware/u1_main/CMakeLists.txt branch the same way: CH375 sources/libs in
#: the "if", PIO USB sources/libs in the "else". Reading every such block
#: out of the real file - rather than asserting against a string this test
#: made up itself - is what makes "tinyusb_host is linked" mean something
#: other than "this test contains the word tinyusb_host".
_BACKEND_BLOCK = re.compile(
    r'if\(DUO_INPUT_BACKEND STREQUAL "CH375"\)(?P<ch375>.*?)else\(\)(?P<pio>.*?)endif\(\)',
    re.DOTALL,
)


def _backend_blocks(text: str) -> list[re.Match]:
    matches = list(_BACKEND_BLOCK.finditer(text))
    if not matches:
        raise AssertionError(
            'firmware/u1_main/CMakeLists.txt has no '
            'if(DUO_INPUT_BACKEND STREQUAL "CH375") ... else() ... endif() '
            "block to read either backend's inputs out of - the PIO USB "
            "scaffolding this test checks for does not exist yet."
        )
    return matches


def _ch375_branch_text(text: str) -> str:
    return "\n".join(match.group("ch375") for match in _backend_blocks(text))


def _pio_usb_branch_text(text: str) -> str:
    return "\n".join(match.group("pio") for match in _backend_blocks(text))


def test_pio_usb_branch_links_the_tinyusb_host_stack():
    assert "tinyusb_host" in _pio_usb_branch_text(_u1_cmakelists_text())


def test_pio_usb_branch_links_pico_pio_usb():
    assert "tinyusb_pico_pio_usb" in _pio_usb_branch_text(_u1_cmakelists_text())


def test_pio_usb_branch_compiles_the_backend_sources():
    text = _pio_usb_branch_text(_u1_cmakelists_text())
    assert "pio_usb/backend.cpp" in text
    assert "pio_usb/device_registry.cpp" in text
    assert "pio_usb/tinyusb_host_callbacks.cpp" in text


def test_ch375_branch_still_compiles_the_ch375_device():
    # Task 5 must not have moved or removed what the CH375 image links.
    assert "ch375/device.cpp" in _ch375_branch_text(_u1_cmakelists_text())


def test_ch375_sources_are_absent_from_the_pio_usb_branch():
    text = _pio_usb_branch_text(_u1_cmakelists_text())
    assert "ch375/device.cpp" not in text
    assert "ch375_probe.cpp" not in text
    assert "input/ch375_source_adapter.cpp" not in text


def test_pio_usb_sources_are_absent_from_the_ch375_branch():
    assert "pio_usb/backend.cpp" not in _ch375_branch_text(_u1_cmakelists_text())


def test_tusb_config_enables_the_pio_usb_host_only_under_the_pio_backend():
    match = re.search(r"#ifdef DUO_INPUT_BACKEND_PIO_USB(.*?)#endif", _tusb_config_text(), re.DOTALL)
    assert match, "tusb_config.h has no #ifdef DUO_INPUT_BACKEND_PIO_USB block"
    block = match.group(1)
    assert "CFG_TUH_ENABLED" in block
    assert "CFG_TUH_HUB" in block
    assert "CFG_TUH_RPI_PIO_USB" in block
    assert "CFG_TUH_HID" in block
    assert re.search(r"#define\s+CFG_TUH_DEVICE_MAX\s+4\b", block)
    assert re.search(r"#define\s+CFG_TUH_HID\s+\(2 \* CFG_TUH_DEVICE_MAX\)", block)
    # RHPort 0 stays the device port outside this block; RHPort 1 is what
    # the PIO backend adds.
    assert "CFG_TUSB_RHPORT1_MODE" in block
    assert "OPT_MODE_HOST" in block


def test_tusb_config_device_rhport_is_unconditional():
    # RHPort 0 as device must not have moved inside the PIO-only block above
    # - it is what both backends share, and CH375 never sees
    # DUO_INPUT_BACKEND_PIO_USB defined at all.
    text = _tusb_config_text()
    before_pio_block = text.split("#ifdef DUO_INPUT_BACKEND_PIO_USB")[0]
    assert "CFG_TUSB_RHPORT0_MODE" in before_pio_block
    assert "OPT_MODE_DEVICE" in before_pio_block


def test_clock_handoff_is_a_release_acquire_atomic_contract():
    header = _source_text("firmware/u1_main/pio_usb/backend.hpp")
    implementation = _source_text("firmware/u1_main/pio_usb/backend.cpp")
    main = _source_text("firmware/u1_main/main.cpp")

    assert "std::atomic<std::uint32_t>" in header
    assert "std::memory_order_acquire" in header
    assert "std::memory_order_release" in header
    assert "set_sys_clock_khz(120000, true)" not in implementation
    main_body = main[main.index("int main()") :]
    clock = main_body.index("set_sys_clock_khz(120000, true)")
    settle = main_body.index("sleep_ms(10)", clock)
    first_peripheral = main_body.index("configure_indicator()")
    assert clock < settle < first_peripheral
    assert implementation.index("sleep_ms(10)") < implementation.index("tuh_configure")
    assert implementation.index("tuh_init") < implementation.index("publish_settled()")


def test_spi_baud_is_restored_to_the_requested_one_megahertz():
    header = _source_text("firmware/u1_main/spi_master.hpp")
    implementation = _source_text("firmware/u1_main/spi_master.cpp")

    assert re.search(r"kSpiBaudRate\s*=\s*1'000'000", header)
    refresh = re.search(
        r"void\s+SpiMaster::refresh_baudrate\(\)\s*\{(?P<body>.*?)\}",
        implementation,
        re.DOTALL,
    )
    assert refresh, "SpiMaster::refresh_baudrate() is missing"
    assert "spi_set_baudrate(kSpi, kSpiBaudRate)" in refresh.group("body")


def test_every_core0_runtime_spi_transfer_uses_the_clock_startup_gate():
    main = _source_text("firmware/u1_main/main.cpp")
    guarded = re.findall(
        r"with_link\(\[&\]\s*\{\s*"
        r"link\.(send_release_all\(now_ms\)|poll\(now_ms,\s*g_outputs\));\s*"
        r"\}\);",
        main,
    )

    assert guarded.count("poll(now_ms, g_outputs)") == 1
    assert guarded.count("send_release_all(now_ms)") == 2


def test_host_observation_mapping_carries_every_appended_wire_progress_field():
    main = _source_text("firmware/u1_main/main.cpp")
    start = main.index("HostObservation describe_host_observation")
    end = main.index("return out;", start)
    mapping = main[start:end]

    for field in (
        "mount_events",
        "umount_events",
        "hid_mount_events",
        "ep_slots_opened",
        "ep_max_failed_count",
        "max_pass_gap_us",
        "max_sof_gap",
        "root_port_resets",
        "hub_mount_events",
    ):
        assert f"out.{field} = observed.{field};" in mapping


def test_task6_callbacks_only_capture_records_and_never_arm_or_route():
    callbacks = _source_text("firmware/u1_main/pio_usb/tinyusb_host_callbacks.cpp")
    without_comments = re.sub(r"//.*?$|/\*.*?\*/", "", callbacks, flags=re.MULTILINE | re.DOTALL)

    assert "tuh_hid_receive_report" not in without_comments
    for callback in ("tuh_hid_mount_cb", "tuh_hid_umount_cb", "tuh_hid_report_received_cb"):
        assert re.search(rf"void\s+{callback}\s*\(", without_comments), (
            f"{callback} must remain an explicit bounded callback"
        )

    for forbidden in ("InputPipeline", "parse_", "Normalizer", "logical_port"):
        assert forbidden not in without_comments


def test_failed_host_initialization_is_recorded_before_clock_settled_is_published():
    backend = _source_text("firmware/u1_main/pio_usb/backend.cpp")
    configure = backend.index("const bool configured = tuh_configure")
    initialize = backend.index("const bool initialized = tuh_init")
    record = backend.index("registry_.record_host_initialization(configured, initialized)")
    publish = backend.index("clock_change_.publish_settled()")

    assert configure < initialize < record < publish


# ------------------------------------------------------------------ the ELFs

def _pio_usb_build_dir() -> Path:
    override = os.environ.get("DUO_INPUT_PIO_USB_BUILD")
    return Path(override) if override else REPOSITORY_ROOT / "build" / "pico-pio-usb-release"


def _ch375_build_dir() -> Path:
    override = os.environ.get("DUO_INPUT_PICO_BUILD")
    return Path(override) if override else REPOSITORY_ROOT / "build" / "pico-release"


def _u1_elf(build_dir: Path) -> Path:
    return build_dir / "firmware" / "u1_main" / "duo_u1_main.elf"


def _symbols(elf: Path):
    from dump_usb_descriptors import Elf32

    return Elf32(elf.read_bytes()).symbols()


def _tool_from_cache(build_dir: Path, cache_name: str) -> Path:
    cache = (build_dir / "CMakeCache.txt").read_text(encoding="utf-8", errors="replace")
    match = re.search(rf"^{re.escape(cache_name)}:[^=]*=(.+)$", cache, re.MULTILINE)
    assert match, f"{cache_name} is absent from {build_dir / 'CMakeCache.txt'}"
    tool = Path(match.group(1).strip())
    assert tool.is_file(), f"{cache_name} points to missing tool {tool}"
    return tool


def _disassembly(build_dir: Path, elf: Path) -> str:
    objdump = _tool_from_cache(build_dir, "CMAKE_OBJDUMP")
    completed = subprocess.run(
        [str(objdump), "-d", "-C", str(elf)],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


def _function_disassembly(disassembly: str, symbol: str) -> str:
    lines = disassembly.splitlines()
    marker = f"<{symbol}>:"
    start = next((index for index, line in enumerate(lines) if marker in line), None)
    assert start is not None, f"ELF disassembly has no {symbol}"

    body = []
    for line in lines[start + 1 :]:
        if re.match(r"^[0-9a-fA-F]+ <.*>:$", line):
            break
        body.append(line)
    return "\n".join(body)


_pio_elf = _u1_elf(_pio_usb_build_dir())
_ch375_elf = _u1_elf(_ch375_build_dir())

pio_usb_elf_required = pytest.mark.skipif(
    not _pio_elf.is_file(),
    reason="no PIO USB U1 build; run cmake --build --preset pico-pio-usb-release first",
)
ch375_elf_required = pytest.mark.skipif(
    not _ch375_elf.is_file(),
    reason="no CH375 U1 build; run cmake --build --preset pico-release first",
)


@pio_usb_elf_required
def test_pio_usb_elf_contains_tuh_task():
    symbols = _symbols(_pio_elf)
    assert any("tuh_task" in name for name in symbols), (
        "PIO USB ELF does not contain tuh_task (or its tuh_task_ext, which "
        "TinyUSB's always-inline tuh_task() compiles down to) - Core 1 "
        "cannot be servicing the host stack without it"
    )


@pio_usb_elf_required
def test_pio_usb_elf_contains_tuh_hid_receive_report():
    symbols = _symbols(_pio_elf)
    assert any("tuh_hid_receive_report" in name for name in symbols), (
        "PIO USB ELF does not contain the HID receive API that Task 6 arms"
    )


@pio_usb_elf_required
def test_pio_usb_elf_contains_input_pipeline_on_event():
    symbols = _symbols(_pio_elf)
    assert any("InputPipeline8on_event" in name for name in symbols), (
        "PIO USB ELF does not contain InputPipeline::on_event - Core 1's "
        "drain loop is not wired to the same pipeline entry point the "
        "CH375 build feeds"
    )


@pio_usb_elf_required
def test_pio_usb_elf_contains_no_ch375_device_tick():
    symbols = _symbols(_pio_elf)
    assert not any("Ch375Device4tick" in name for name in symbols), (
        "PIO USB ELF contains Ch375Device::tick - the CH375 sources leaked "
        "into a build that must never link them"
    )


@pio_usb_elf_required
def test_task6_callbacks_do_not_arm_or_route_in_the_linked_elf():
    disassembly = _disassembly(_pio_usb_build_dir(), _pio_elf)

    for callback in ("tuh_hid_mount_cb", "tuh_hid_umount_cb", "tuh_hid_report_received_cb"):
        body = _function_disassembly(disassembly, callback)
        assert "tuh_hid_receive_report" not in body
        assert "InputPipeline" not in body
        assert "parse_" not in body


@pio_usb_elf_required
def test_pio_usb_elf_contains_the_bounded_registry_and_real_arm_path():
    symbols = _symbols(_pio_elf)
    # Matched on the un-suffixed prefix rather than a full mangled name:
    # process_pending()'s argument list is an implementation detail that has
    # already changed once (Task 8 added a now_us parameter, then its fix
    # round removed it again when the capture timestamp moved into the
    # callback record), and this assertion is about the symbol being linked
    # in at all, not about its signature.
    assert any("DeviceRegistry15process_pendingE" in name for name in symbols)
    assert any("DeviceRegistry13arm_if_needed" in name for name in symbols)


@pio_usb_elf_required
def test_linked_clock_handoff_contains_both_arm_memory_barriers():
    disassembly = _disassembly(_pio_usb_build_dir(), _pio_elf)
    begin = _function_disassembly(
        disassembly, "duo_input::u1::pio_usb::PioUsbBackend::begin()"
    )
    settled = _function_disassembly(
        disassembly, "duo_input::u1::pio_usb::PioUsbBackend::clock_settled() const"
    )

    assert "dmb" in begin, "release publication compiled without an ARM memory barrier"
    assert "dmb" in settled, "acquire observation compiled without an ARM memory barrier"


@pio_usb_elf_required
def test_linked_baud_refresh_requests_one_megahertz():
    disassembly = _disassembly(_pio_usb_build_dir(), _pio_elf)
    refresh = _function_disassembly(
        disassembly, "duo_input::u1::SpiMaster::refresh_baudrate()"
    )

    assert "spi_set_baudrate" in refresh
    assert "000f4240" in refresh.lower(), (
        "refresh_baudrate does not load the literal 1,000,000 (0x000f4240)"
    )


@pio_usb_elf_required
def test_linked_clock_change_runs_from_main_and_never_from_backend_begin():
    bodies = _function_bodies(_disassembly(_pio_usb_build_dir(), _pio_elf))
    main_reachable = _directly_reachable(bodies, "main")
    backend_reachable = _directly_reachable(
        bodies, "duo_input::u1::pio_usb::PioUsbBackend::begin()"
    )

    # set_sys_clock_khz() is inline in this SDK; these are its validation and
    # clock-programming calls in the linked image.
    for callee in ("check_sys_clock_khz", "set_sys_clock_pll"):
        assert callee in main_reachable
        assert callee not in backend_reachable
    assert "sleep_ms" in backend_reachable


@ch375_elf_required
def test_ch375_linked_main_never_runs_the_pio_usb_clock_change():
    bodies = _function_bodies(_disassembly(_ch375_build_dir(), _ch375_elf))
    reachable = _directly_reachable(bodies, "main")

    for callee in ("check_sys_clock_khz", "set_sys_clock_pll"):
        assert callee not in reachable


@ch375_elf_required
def test_ch375_elf_contains_no_tinyusb_host_symbols():
    symbols = _symbols(_ch375_elf)
    assert not any("tuh_task" in name for name in symbols), (
        "CH375 ELF contains a TinyUSB host symbol - the two backends' "
        "sources are no longer isolated from each other"
    )


# --------------------------------- the device stack must not start the host

#: Every symbol whose reachability from ``UsbService::begin()`` would mean the
#: device-stack bring-up has started the host stack again.
#:
#: ``tusb_rhport_init`` is the argument-less ``tusb_init()``'s only expansion
#: (``.deps/tinyusb/src/tusb.h:142`` -> ``tusb_rhport_init(0, NULL)``), and its
#: ``rh_init == NULL`` branch (``src/tusb.c:61-86``) calls BOTH
#: ``tud_rhport_init`` and ``tuh_rhport_init``. ``tuh_rhport_init`` is the host
#: bring-up itself. Either one reachable from Core 0's ``usb.begin()`` puts the
#: Pico-PIO-USB host on the wrong core, at the wrong clock, before
#: ``tuh_configure()`` - which is what made this board enumerate nothing.
FORBIDDEN_FROM_USB_SERVICE_BEGIN = ("tusb_rhport_init", "tuh_rhport_init")

#: What ``UsbService::begin()`` must still reach. Without this the two
#: assertions above would also pass on a ``begin()`` that had been emptied out,
#: which is the failure mode this whole file exists to refuse: the source-order
#: assertion in ``test_failed_host_initialization_is_recorded_...`` above passed
#: for weeks with the board completely dead.
REQUIRED_FROM_USB_SERVICE_BEGIN = "tud_rhport_init"

#: Instructions that transfer control to a named symbol. Restricted to real
#: branch mnemonics rather than every ``<symbol>`` on a line: a literal pool
#: printed with a symbolic comment is data, not an edge, and admitting it would
#: make a "does not reach" assertion depend on the linker's constant placement.
_BRANCH_TO_SYMBOL = re.compile(
    r"\b(?:bl|blx|bx|b|b\.n|b\.w|bl\.w|beq|bne|bcc|bcs|bmi|bpl|bhi|bls|bge|blt|bgt|ble)"
    r"(?:\.[nw])?\s+(?:0x)?[0-9a-fA-F]+\s+<(?P<symbol>[^>+]+)(?:\+0x[0-9a-fA-F]+)?>"
)


def _function_bodies(disassembly: str) -> dict[str, str]:
    """Every function in the image, by the name objdump printed for it."""
    bodies: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in disassembly.splitlines():
        header = re.match(r"^[0-9a-fA-F]+ <(?P<name>.*)>:$", line)
        if header:
            current = bodies.setdefault(header.group("name"), [])
            continue
        if current is not None:
            current.append(line)
    return {name: "\n".join(lines) for name, lines in bodies.items()}


def _directly_reachable(bodies: dict[str, str], root: str) -> set[str]:
    """Every symbol reachable from ``root`` by direct branches.

    Direct branches only. A call through a function pointer - TinyUSB's class
    driver tables, for one - is not followed, so this under-approximates what
    the image can do and therefore cannot manufacture a violation that is not
    there. It is exactly the right instrument for this defect regardless: the
    call it must never find is a plain ``bl``, emitted by the compiler from a
    macro expansion in this repository's own source.
    """
    assert root in bodies, f"ELF disassembly has no {root}"
    seen: set[str] = set()
    pending = [root]
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        for match in _BRANCH_TO_SYMBOL.finditer(bodies.get(name, "")):
            target = match.group("symbol")
            if target not in seen:
                pending.append(target)
    seen.discard(root)
    return seen


@pio_usb_elf_required
def test_pio_usb_elf_never_reaches_the_host_stack_from_usb_service_begin():
    bodies = _function_bodies(_disassembly(_pio_usb_build_dir(), _pio_elf))
    reachable = _directly_reachable(bodies, "duo_input::u1::UsbService::begin()")

    for forbidden in FORBIDDEN_FROM_USB_SERVICE_BEGIN:
        assert forbidden not in reachable, (
            f"UsbService::begin() reaches {forbidden} in the linked PIO USB "
            "image. Core 0 is starting the Pico-PIO-USB host stack again - "
            "before tuh_configure(), before Core 1's set_sys_clock_khz(120000) "
            "- and Core 1's own tuh_init(1) will silently no-op and still "
            "report success. Bring up the device stack by naming the port and "
            "the role (tud_init(0)), never with the argument-less tusb_init()."
        )

    assert REQUIRED_FROM_USB_SERVICE_BEGIN in reachable, (
        f"UsbService::begin() no longer reaches {REQUIRED_FROM_USB_SERVICE_BEGIN} "
        "in the linked PIO USB image - the device stack is not being brought "
        "up at all, which passes the assertions above for the wrong reason"
    )


@ch375_elf_required
def test_ch375_elf_still_brings_the_device_stack_up_from_usb_service_begin():
    """The one-line fix must not have changed the CH375 image's behaviour.

    The CH375 build resolves its Pico SDK from the environment and therefore
    links that SDK's bundled TinyUSB 0.17, where ``tusb_init(void)`` was a real
    function whose whole body is ``tud_init(TUD_OPT_RHPORT)`` (CFG_TUH_ENABLED
    is 0 there, so the host half is not compiled at all). ``tud_init`` is a
    real function in that tree, so ``begin()`` reaches it by name rather than
    inlined - either spelling is fine, and what has to hold is that the device
    stack is still started and no host symbol appears.
    """
    bodies = _function_bodies(_disassembly(_ch375_build_dir(), _ch375_elf))
    reachable = _directly_reachable(bodies, "duo_input::u1::UsbService::begin()")

    assert any(name in reachable for name in ("tud_init", "tud_rhport_init")), (
        "CH375 UsbService::begin() no longer starts the device stack"
    )
    assert not any("tuh_" in name for name in reachable), (
        "CH375 UsbService::begin() reaches a TinyUSB host symbol"
    )
