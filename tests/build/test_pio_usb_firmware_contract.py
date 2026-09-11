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
sys.path.insert(0, str(REPOSITORY_ROOT / "tests" / "build"))

from pio_usb_flash_contract import assert_flash_path_sram_safe


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


def test_core0_publishes_core1_before_it_can_be_launched():
    source = _source_text("firmware/u1_main/main.cpp")
    core1_body = source[source.index("void core1_entry()") : source.index("int main()")]
    main_body = source[source.index("int main()") :]

    publish = main_body.index("set_core1_running(true)")
    launch = main_body.index("multicore_launch_core1(core1_entry)")
    assert publish < launch
    assert "set_core1_running(true)" not in core1_body


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
def test_flash_keepalive_executes_entirely_outside_xip_flash():
    """Catch code or fixed USB data added to the flash-time path in XIP."""
    assert_flash_path_sram_safe(
        _pio_usb_build_dir(),
        _pio_elf,
        "duo_input::u1::service_core1_flash_window()",
    )


@pio_usb_elf_required
def test_core1_flash_window_is_wired_pause_park_resume_finish_in_order():
    disassembly = _disassembly(_pio_usb_build_dir(), _pio_elf)
    body = _function_disassembly(disassembly, "(anonymous namespace)::core1_entry()")
    ordered = (
        "pio_usb_host_flash_pause",
        "service_core1_flash_windowEv_veneer",
        "pio_usb_host_flash_resume",
        "duo_input::u1::finish_core1_flash_window()",
    )
    positions = [body.index(symbol) for symbol in ordered]
    assert positions == sorted(positions)


def test_flash_sof_cadence_is_retained_across_page_program_windows():
    source = (REPOSITORY_ROOT / "firmware/u1_main/pico_flash.cpp").read_text(
        encoding="utf-8"
    )
    service = source[
        source.index("service_core1_flash_window") :
        source.index("finish_core1_flash_window")
    ]

    assert "FlashSofCadence g_flash_sof_cadence" in source
    assert "g_flash_sof_cadence.due(now_us)" in service
    assert "FlashSofCadence cadence" not in service


@pio_usb_elf_required
def test_flash_backend_uses_cooperative_core1_window_not_multicore_lockout():
    disassembly = _disassembly(_pio_usb_build_dir(), _pio_elf)
    for symbol in (
        "duo_input::u1::PicoFlash::erase(unsigned long, unsigned int)",
        "duo_input::u1::PicoFlash::program(unsigned long, unsigned char const*, unsigned int)",
    ):
        body = _function_disassembly(disassembly, symbol)
        assert "duo_input::u1::begin_core1_flash_window()" in body
        assert "duo_input::u1::end_core1_flash_window()" in body
        assert "multicore_lockout_start_blocking" not in body
        assert "multicore_lockout_end_blocking" not in body


@pio_usb_elf_required
def test_pio_usb_elf_contains_tuh_hid_receive_report():
    symbols = _symbols(_pio_elf)
    assert any("tuh_hid_receive_report" in name for name in symbols), (
        "PIO USB ELF does not contain the HID receive API that Task 6 arms"
    )


@pio_usb_elf_required
def test_pio_usb_elf_contains_input_pipeline_on_event():
    symbols = _symbols(_pio_elf)
    assert any("SourceTable8on_event" in name for name in symbols), (
        "PIO USB ELF does not contain SourceTable::on_event - Core 1's "
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


@pio_usb_elf_required
def test_linked_main_launches_the_host_core_before_starting_device_usb_and_spi():
    """Guard the reference startup as executed, not as source spelling.

    Moving either initialization call above the launch recreates the ordering
    that failed on the bench, regardless of variable names or formatting.
    """
    main = _function_disassembly(
        _disassembly(_pio_usb_build_dir(), _pio_elf), "main"
    )
    calls = [match.group("symbol") for match in _BRANCH_TO_SYMBOL.finditer(main)]

    launch = calls.index("multicore_launch_core1")
    device_usb = calls.index("duo_input::u1::UsbService::begin()")
    spi = calls.index("duo_input::u1::SpiMaster::begin()")

    assert launch < device_usb < spi


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


# ------------------------------- the round-4 observations, against the image


def _host_path_function_bodies(disassembly: str) -> dict[str, str]:
    """Every function this repository wrote that lives on the host path.

    Selected by namespace rather than by a list of entry points, deliberately.
    The publication helpers are in unnamed namespaces and the compiler is free
    to keep them as separate symbols or fold them into their callers; a fixed
    list of entry points would have inspected only the callers and missed a
    library call one frame down, which is exactly the hole the first version of
    this guard had - a mutation that turned one relaxed store into a fetch_add
    survived it.

    Scoped to duo_input::u1::pio_usb rather than to the whole of duo_input:
    TinyUSB, the SDK and this project's own Core-0 output runtime all use
    atomic read-modify-write, legitimately, outside the SOF interrupt's path.
    """
    owned = {
        symbol: body
        for symbol, body in _function_bodies(disassembly).items()
        if symbol.startswith("duo_input::u1::pio_usb::")
    }
    assert owned, "no duo_input::u1::pio_usb functions in the disassembly at all"
    return owned


@pio_usb_elf_required
def test_no_published_observation_becomes_an_atomic_library_call():
    """The lock-free claim, checked where it is actually true or false.

    ``PioUsbBackend::observation_publication_is_always_lock_free()`` is a
    static assertion the NATIVE build evaluates, and on x86 it is trivially
    true. On the board it would be FALSE: ARMv6-M has no LDREX/STREX, so GCC
    reports ``std::atomic<uint32_t>::is_always_lock_free`` as false because a
    read-modify-write would be a libcall. Nothing here performs one - every
    publication is a relaxed load and a relaxed store, which compile to a plain
    LDR and STR and are naturally atomic for an aligned word.

    That distinction is the whole safety argument for sampling inside the SOF
    interrupt, and until now nothing checked it against the shipping image. A
    library call to ``__atomic_*`` in any of these functions could take a lock
    inside an interrupt handler, so this looks for one in the linked ELF rather
    than trusting a trait evaluated on a different architecture.
    """
    disassembly = _disassembly(_pio_usb_build_dir(), _pio_elf)
    bodies = _host_path_function_bodies(disassembly)
    bodies["tuh_event_hook_cb"] = _function_disassembly(
        disassembly, "tuh_event_hook_cb"
    )
    for symbol, body in bodies.items():
        assert "__atomic_" not in body, (
            f"{symbol} calls an atomic library routine in the linked image; "
            "on ARMv6-M that is not a lock-free publication and it runs "
            "inside the SOF interrupt"
        )


@pio_usb_elf_required
def test_the_linked_host_event_hook_is_this_firmwares_and_not_tinyusbs_stub():
    """The weak symbol has to have actually been overridden.

    ``tuh_event_hook_cb`` is ``TU_ATTR_WEAK`` in usbh.c with an empty body. If
    this firmware's definition ever stopped being linked - renamed, moved into
    a namespace, dropped from the source list - the image would still build,
    still run, and report zero host events for ever. That is this project's
    recorded dominant defect in its purest form: working code with a passing
    test that production never reaches.

    The proof is that the linked body calls ``duo_core1_stack_pointer``, which
    only this firmware's definition does.
    """
    disassembly = _disassembly(_pio_usb_build_dir(), _pio_elf)
    body = _function_disassembly(disassembly, "tuh_event_hook_cb")
    assert "duo_core1_stack_pointer" in body, (
        "the linked tuh_event_hook_cb does not sample the stack pointer, so "
        "it is TinyUSB's empty weak stub rather than this firmware's hook"
    )


@pio_usb_elf_required
def test_the_host_event_hook_is_reached_from_both_of_core1s_contexts():
    """Both callers, in the image, because the split exists for both.

    TinyUSB queues events from two contexts on Core 1: hub.c queues its port
    events from inside ``tuh_task``, and hcd_pio_usb.c queues attach, remove
    and transfer completion from the SOF alarm interrupt. The hook keeps one
    counter word per context precisely because RP2040 cannot do an atomic
    read-modify-write, and that design is only worth anything if both callers
    are really there.
    """
    disassembly = _disassembly(_pio_usb_build_dir(), _pio_elf)
    reached = ("tuh_event_hook_cb", "__tuh_event_hook_cb_veneer")
    for caller in ("tuh_task_ext", "hcd_event_handler"):
        body = _function_disassembly(disassembly, caller)
        assert any(target in body for target in reached), (
            f"{caller} does not reach tuh_event_hook_cb in the linked image"
        )
