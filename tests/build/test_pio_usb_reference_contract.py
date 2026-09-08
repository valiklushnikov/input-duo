"""Build contract for the frozen Pico-PIO-USB golden reference target."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
REFERENCE_BUILD = ROOT / "build" / "pico-pio-usb-reference-release"
PIO_USB_BUILD = ROOT / "build" / "pico-pio-usb-release"
REFERENCE_ELF = (
    REFERENCE_BUILD / "firmware" / "u1_reference" / "duo_u1_reference.elf"
)
REFERENCE_UF2 = (
    REFERENCE_BUILD / "firmware" / "u1_reference" / "duo_u1_reference.uf2"
)
U2_RELATIVE_ARTIFACTS = (
    Path("firmware/u2_endpoint/duo_u2_endpoint.elf"),
    Path("firmware/u2_endpoint/duo_u2_endpoint.uf2"),
)
PICO_PIO_USB_ROOT = ROOT / ".deps" / "pico-pio-usb"
UPSTREAM_REFERENCE = (
    PICO_PIO_USB_ROOT / "examples" / "host_hid_to_device_cdc"
)
REFERENCE_PRESET = "pico-pio-usb-reference-release"
PIO_USB_PRESET = "pico-pio-usb-release"
FIXED_SOURCE_DATE_EPOCH = "1788691431"
# The clone is built one commit past upstream, with patches/pico-pio-usb/
# applied - see cmake/pio_usb_toolchain_lock.cmake. The examples/ directory the
# reference copies come from is untouched by that patch, so the upstream blob
# hashes below still hold.
PINNED_PICO_PIO_USB_REVISION = "ce67882de7c6e75734087e3181caeb2511f48c46"
REVIEWED_REFERENCE_SHA256 = {
    "main.c": "e8539134690e597be9254ee179f72a2b5cc93becf355e033f994d08955ea8ea1",
    "tusb_config.h": "4ce4ff7a45fc93b5695ddc9375c091995ce19ab078fc32a23d3f4299ee95594c",
    "usb_descriptors.c": "18745d895aff262c81f7a1a7b70887af4670e16100cb4a566782ea8601cf9143",
}

sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests" / "build"))

from reference_build_support import (
    firmware_artifact_lock,
    rebuild_reference_u1_artifacts,
    rebuild_target_artifacts,
)


#: The reference is a maintained copy of the upstream example, and every
#: departure from it has to be visible rather than hidden behind a new hash.
#: Listing the exact before/after lines means an accidental second edit fails
#: this test just as loudly as an unreviewed first one would.
#:
#: CFG_TUH_HID: upstream sizes this for one simple keyboard and one simple
#: mouse. A single composite 2.4 GHz receiver claims three HID instances, so
#: two receivers cannot fit in four and the second one's interfaces are
#: refused with "is not supported". Measured 2026-09-06; see
#: docs/superpowers/records/2026-09-03-pio-usb-hub-v1-record.md.
DOCUMENTED_REFERENCE_DEVIATIONS = {
    "tusb_config.h": [
        (
            "#define CFG_TUH_HID                  4",
            "#define CFG_TUH_HID                  8",
        ),
    ],
}


def _assert_only_documented_deviations(
    maintained: Path, upstream: Path, name: str
) -> None:
    maintained_lines = maintained.read_text(encoding="utf-8").splitlines()
    upstream_lines = upstream.read_text(encoding="utf-8").splitlines()
    assert len(maintained_lines) == len(upstream_lines), (
        f"{name} has gained or lost lines relative to the upstream example"
    )

    expected = dict(DOCUMENTED_REFERENCE_DEVIATIONS[name])
    actual = {
        before: after
        for before, after in zip(upstream_lines, maintained_lines)
        if before != after
    }
    assert actual == expected, (
        f"{name} deviates from the upstream example in ways that are not "
        f"documented in DOCUMENTED_REFERENCE_DEVIATIONS: expected {expected}, "
        f"found {actual}"
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _reference_symbols(elf: Path) -> dict:
    from dump_usb_descriptors import Elf32

    assert elf.is_file(), (
        f"missing reference ELF: {elf}; build "
        "pico-pio-usb-reference-release first"
    )
    return Elf32(elf.read_bytes()).symbols()


def _configured_make_program() -> str:
    cache = (REFERENCE_BUILD / "CMakeCache.txt").read_text(
        encoding="utf-8", errors="replace"
    )
    match = re.search(r"^CMAKE_MAKE_PROGRAM:[^=]+=(.+)$", cache, re.MULTILINE)
    assert match, (
        f"CMAKE_MAKE_PROGRAM is absent from {REFERENCE_BUILD / 'CMakeCache.txt'}"
    )
    return match.group(1).strip()


def _configure_and_rebuild_u2(
    *, preset: str, build_dir: Path, backup_dir: Path, env: dict[str, str]
) -> tuple[Path, Path]:
    make_program = _configured_make_program()
    picotool_dir = REFERENCE_BUILD / "_deps" / "picotool"
    assert (picotool_dir / "picotoolConfig.cmake").is_file(), (
        f"picotool package is absent from {picotool_dir}; build "
        f"{REFERENCE_PRESET} first"
    )

    configure = subprocess.run(
        [
            "cmake",
            "--preset",
            preset,
            "-B",
            str(build_dir),
            f"-DCMAKE_MAKE_PROGRAM={make_program}",
            f"-Dpicotool_DIR={picotool_dir}",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert configure.returncode == 0, configure.stdout + configure.stderr

    artifacts = rebuild_target_artifacts(
        root=ROOT,
        build_dir=build_dir,
        backup_dir=backup_dir,
        target="duo_u2_endpoint",
        relative_artifacts=U2_RELATIVE_ARTIFACTS,
        env=env,
        lock_held=True,
    )
    return artifacts[0], artifacts[1]


@pytest.fixture
def fresh_reference_artifacts(tmp_path):
    return rebuild_reference_u1_artifacts(
        root=ROOT,
        build_dir=REFERENCE_BUILD,
        backup_dir=tmp_path / "prior-reference-artifacts",
    )


def test_reference_preset_selects_only_the_reference_backend():
    presets = json.loads((ROOT / "CMakePresets.json").read_text(encoding="utf-8"))
    selected = next(
        preset
        for preset in presets["configurePresets"]
        if preset["name"] == "pico-pio-usb-reference-release"
    )

    assert selected["cacheVariables"]["DUO_INPUT_BACKEND"] == "PIO_USB_REFERENCE"
    assert selected["binaryDir"] == "${sourceDir}/build/pico-pio-usb-reference-release"

    build_selected = next(
        preset
        for preset in presets["buildPresets"]
        if preset["name"] == REFERENCE_PRESET
    )
    assert build_selected["configurePreset"] == REFERENCE_PRESET


def test_invalid_backend_configuration_is_rejected_by_cmake(tmp_path):
    result = subprocess.run(
        [
            "cmake",
            "--preset",
            REFERENCE_PRESET,
            "-B",
            str(tmp_path / "invalid-backend"),
            "-DDUO_INPUT_BACKEND=NOT_A_DUO_BACKEND",
            f"-DCMAKE_MAKE_PROGRAM={_configured_make_program()}",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "Unknown DUO_INPUT_BACKEND 'NOT_A_DUO_BACKEND'" in output


def test_failed_reference_rebuild_restores_exact_prior_artifacts(tmp_path):
    build_dir = tmp_path / "build"
    elf = build_dir / "firmware" / "u1_reference" / "duo_u1_reference.elf"
    uf2 = build_dir / "firmware" / "u1_reference" / "duo_u1_reference.uf2"
    elf.parent.mkdir(parents=True)
    elf.write_bytes(b"prior ELF")
    uf2.write_bytes(b"prior UF2")

    def fail_build(*_args, **_kwargs):
        elf.write_bytes(b"partial new ELF")
        return subprocess.CompletedProcess([], 1, "partial stdout", "build failed")

    with pytest.raises(AssertionError, match="build failed"):
        rebuild_reference_u1_artifacts(
            root=ROOT,
            build_dir=build_dir,
            backup_dir=tmp_path / "backup",
            run_build=fail_build,
        )

    assert elf.read_bytes() == b"prior ELF"
    assert uf2.read_bytes() == b"prior UF2"


def test_backup_copy_failure_preserves_all_prior_artifacts(tmp_path):
    root = tmp_path / "repo"
    build_dir = root / "build" / "reference"
    elf = build_dir / "firmware" / "u1_reference" / "duo_u1_reference.elf"
    uf2 = build_dir / "firmware" / "u1_reference" / "duo_u1_reference.uf2"
    elf.parent.mkdir(parents=True)
    elf.write_bytes(b"prior ELF")
    uf2.write_bytes(b"prior UF2")

    def fail_second_copy(source, destination):
        if Path(source) == uf2:
            raise OSError("injected second backup copy failure")
        Path(destination).write_bytes(Path(source).read_bytes())

    def unexpected_build(*_args, **_kwargs):
        pytest.fail("build must not start before every backup is verified")

    with pytest.raises(OSError, match="injected second backup copy failure"):
        rebuild_reference_u1_artifacts(
            root=root,
            build_dir=build_dir,
            backup_dir=tmp_path / "backup",
            run_build=unexpected_build,
            copy_artifact=fail_second_copy,
        )

    assert elf.read_bytes() == b"prior ELF"
    assert uf2.read_bytes() == b"prior UF2"


def test_incomplete_u2_rebuild_rejects_stale_uf2_and_restores_prior_artifacts(
    tmp_path,
):
    root = tmp_path / "repo"
    build_dir = root / "build" / "pio"
    relative_artifacts = (
        Path("firmware/u2_endpoint/duo_u2_endpoint.elf"),
        Path("firmware/u2_endpoint/duo_u2_endpoint.uf2"),
    )
    elf, uf2 = (build_dir / relative for relative in relative_artifacts)
    elf.parent.mkdir(parents=True)
    elf.write_bytes(b"prior ELF")
    uf2.write_bytes(b"stale matching UF2")

    def link_without_uf2(*_args, **_kwargs):
        elf.write_bytes(b"fresh current-graph ELF")
        return subprocess.CompletedProcess([], 0, "linked ELF only", "")

    with pytest.raises(AssertionError, match=r"did not recreate: .*\.uf2"):
        rebuild_target_artifacts(
            root=root,
            build_dir=build_dir,
            backup_dir=tmp_path / "backup",
            target="duo_u2_endpoint",
            relative_artifacts=relative_artifacts,
            run_build=link_without_uf2,
        )

    assert elf.read_bytes() == b"prior ELF"
    assert uf2.read_bytes() == b"stale matching UF2"


def test_reference_sources_are_maintained_outside_build_output():
    for name in ("main.cpp", "tusb_config.h", "callback_queue.cpp",
                 "host_callbacks.cpp", "source_adapter.cpp"):
        path = ROOT / "firmware" / "u1_reference" / name
        assert path.is_file(), f"missing maintained reference source: {path}"


def test_reference_sources_are_byte_for_byte_the_pinned_upstream_example():
    # Nothing is compared whole any more, and each departure was made by a
    # task that had to justify it. Task 2 moved the callbacks out of main.c, so
    # a whole-file hash there only recorded that an intended change happened.
    # Task 3 replaced the example's CDC-only descriptors with the ones this
    # firmware presents to PC1, and gave tusb_config.h the device classes that
    # needs. What still has to hold is asserted directly below: the upstream
    # lifecycle and its order, and every host setting in tusb_config.h.
    copies = {}

    revision = subprocess.run(
        ["git", "-C", str(PICO_PIO_USB_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert revision == PINNED_PICO_PIO_USB_REVISION

    checkout_status = subprocess.run(
        [
            "git",
            "-C",
            str(PICO_PIO_USB_ROOT),
            "status",
            "--porcelain",
            "--untracked-files=no",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert checkout_status == "", "pinned Pico-PIO-USB checkout is dirty"

    for maintained_name, upstream_name in copies.items():
        maintained = ROOT / "firmware" / "u1_reference" / maintained_name
        upstream = UPSTREAM_REFERENCE / upstream_name
        reviewed_hash = REVIEWED_REFERENCE_SHA256[maintained_name]
        assert _sha256(upstream) == reviewed_hash, (
            f"pinned blob checkout {upstream} does not have its reviewed SHA-256"
        )
        if maintained_name in DOCUMENTED_REFERENCE_DEVIATIONS:
            _assert_only_documented_deviations(maintained, upstream, maintained_name)
        else:
            assert _sha256(maintained) == reviewed_hash, (
                f"{maintained} does not have its immutable reviewed SHA-256"
            )



def test_the_reference_keeps_the_upstream_host_lifecycle_in_order():
    """The one thing about main.c that must never drift.

    Every failure this migration has chased came back to who owns the host
    stack and when it is started: the clock has to be final before anything
    USB, Core 1 has to be launched before it, tuh_init has to run *on* Core 1,
    and the device stack has to come up on Core 0. Hashing the file stopped
    being able to say that the moment the callbacks moved out of it, so assert
    the sequence itself.
    """
    source = (ROOT / "firmware" / "u1_reference" / "main.cpp").read_text(encoding="utf-8")

    core1_ordered = [
        "pio_usb_configuration_t",
        "tuh_configure(1, TUH_CFGID_RPI_PIO_USB_CONFIGURATION",
        "tuh_init(1)",
        "tuh_task()",
    ]
    main_ordered = [
        "set_sys_clock_khz(120000, true)",
        "multicore_reset_core1()",
        "multicore_launch_core1(core1_main)",
        # UsbService::begin() is tud_init(0) with the reason written down: the
        # argument-less tusb_init() brings up BOTH stacks on this TinyUSB, which
        # started the host on Core 0 at the wrong clock once already.
        "g_usb.begin()",
        "g_usb.task()",
    ]

    # core1_main is written above main, so the two sequences are checked inside
    # their own function bodies rather than across the whole file.
    core1_body = source[source.index("void core1_main"):source.index("int main(")]
    main_body = source[source.index("int main("):]

    for body, ordered, where in (
        (core1_body, core1_ordered, "core1_main"),
        (main_body, main_ordered, "main"),
    ):
        position = -1
        for fragment in ordered:
            found = body.find(fragment)
            assert found != -1, f"{where} no longer contains {fragment!r}"
            assert found > position, (
                f"in {where}, {fragment!r} appears before something that must "
                "precede it; the upstream host lifecycle order has changed"
            )
            position = found

    # The host stack must come up on Core 1 and the device stack on Core 0.
    # Running the host on the wrong core is a failure this project has already
    # paid for once.
    assert "tud_init" not in core1_body
    assert "tuh_init" not in main_body
    # Never the argument-less form, on either core. Checked against code
    # rather than the whole file: main.cpp explains in a comment why that call
    # must not appear, and a naive search would fail on the explanation.
    code = " ".join(line.split("//", 1)[0] for line in source.splitlines())
    assert "tusb_init(" not in code, (
        "tusb_init() brings up both stacks on this TinyUSB; the device stack "
        "must be started with tud_init(0) alone"
    )


def test_the_reference_callbacks_left_main_but_not_the_build():
    """Callbacks moved out; they did not quietly disappear."""
    reference = ROOT / "firmware" / "u1_reference"
    main_source = (reference / "main.cpp").read_text(encoding="utf-8")
    callbacks = (reference / "host_callbacks.cpp").read_text(encoding="utf-8")

    for callback in (
        "tuh_hid_mount_cb",
        "tuh_hid_umount_cb",
        "tuh_hid_report_received_cb",
        "tud_cdc_rx_cb",
    ):
        assert callback not in main_source, (
            f"{callback} is still defined in main.cpp; the point of Task 2 is "
            "that callbacks do no work on the host core"
        )
        assert callback in callbacks, f"{callback} was lost, not moved"

    # The stack delivers nothing more until the report is re-armed, so the one
    # piece of work upstream does in a callback has to survive the move.
    assert "tuh_hid_receive_report" in callbacks


def test_no_formatting_or_cdc_write_happens_in_a_host_callback():
    """What the callbacks must *not* do, asserted where it can be checked.

    Formatting inside tuh_hid_report_received_cb is what the upstream example
    does and what this task exists to remove: it spends the host core's time on
    text while the bus waits.
    """
    source = (ROOT / "firmware" / "u1_reference" / "host_callbacks.cpp").read_text(
        encoding="utf-8"
    )
    body_start = source.index("void tuh_hid_report_received_cb")
    body_end = source.index("void tud_cdc_rx_cb")
    report_callback = source[body_start:body_end]

    for forbidden in ("snprintf", "sprintf", "tud_cdc_write"):
        assert forbidden not in report_callback, (
            f"{forbidden} is called from the report callback, on the core that "
            "drives tuh_task"
        )


def test_reference_elf_contains_only_the_upstream_host_device_path(
    fresh_reference_artifacts,
):
    freshly_built_elf, freshly_built_uf2 = fresh_reference_artifacts
    assert freshly_built_uf2.is_file()
    symbols = _reference_symbols(freshly_built_elf)

    for required in ("tuh_task", "tuh_hid_receive_report", "tud_task"):
        assert any(required in name for name in symbols), (
            f"{REFERENCE_ELF} contains no {required}"
        )

    # tud_cdc_write() is an always-inline TinyUSB wrapper; the controller's
    # ruling requires exact membership for its out-of-line implementation so
    # tud_cdc_n_write_flush cannot accidentally satisfy this data-write gate.
    assert "tud_cdc_n_write" in symbols, (
        f"{REFERENCE_ELF} contains no exact tud_cdc_n_write symbol"
    )

    # The input path is now entered through the per-interface source table.
    # What stays out is the old host backend and CH375.
    for required_symbol in ("SourceTable8on_event", "OutputRuntime",
                            "Core1Runtime", "ReferenceSourceAdapter"):
        assert any(required_symbol in name for name in symbols), (
            f"{REFERENCE_ELF} contains no {required_symbol}"
        )

    # SpiMaster left this list in Task 4, which admits the link to U2; what it
    # must now contain is asserted in test_reference_routing_contract.py.
    #
    # Task 5 admits configuration, storage and diagnostics: ConfigService,
    # its handoff to Core 1 and the bridge function that pumps requests and
    # answers across it are all now required rather than excluded. Mangled
    # method substrings rather than the bare class names, the same standard
    # test_reference_routing_contract.py applies - a bare name is satisfied
    # by a vtable or a debug string and proves nothing was actually called.
    for required_symbol in (
        "ConfigService12on_cdc_bytes",
        "ConfigHandoff4post",
        "ConfigHandoff8complete",
        # A free function template, not a class - no method to scope it to,
        # and this is the name the linker actually emits for its
        # instantiation. See below for why "CoreBridge" itself never
        # appears anywhere in this project.
        "pump_core_bridge",
    ):
        assert any(required_symbol in name for name in symbols), (
            f"{REFERENCE_ELF} contains no {required_symbol}"
        )

    # "CoreBridge" is not excluded here because no such symbol exists
    # anywhere in this project - the core bridge is the free function
    # template and handoff class required above, not a class of its own.
    # Ch375Device and PioUsbBackend are never coming back to this target.
    for excluded in (
        "Ch375Device4tick",
        "PioUsbBackend4task",
    ):
        assert not any(excluded in name for name in symbols), (
            f"{REFERENCE_ELF} unexpectedly contains {excluded}"
        )


def test_reference_u2_matches_the_same_toolchain_pio_usb_u2(tmp_path):
    env = os.environ.copy()
    env["SOURCE_DATE_EPOCH"] = FIXED_SOURCE_DATE_EPOCH
    isolated_pio_usb_build = tmp_path / "pico-pio-usb-release"
    isolated_reference_build = tmp_path / "pico-pio-usb-reference-release"

    # Use private build graphs: the fixed comparison epoch must never poison
    # the persistent release build directories consumed by later tests.
    with firmware_artifact_lock(ROOT):
        _, pio_usb_u2_uf2 = _configure_and_rebuild_u2(
            preset=PIO_USB_PRESET,
            build_dir=isolated_pio_usb_build,
            backup_dir=tmp_path / "pio-usb-u2-backup",
            env=env,
        )
        _, reference_u2_uf2 = _configure_and_rebuild_u2(
            preset=REFERENCE_PRESET,
            build_dir=isolated_reference_build,
            backup_dir=tmp_path / "reference-u2-backup",
            env=env,
        )

        assert reference_u2_uf2.read_bytes() == pio_usb_u2_uf2.read_bytes()


def test_a_refused_capture_is_surfaced_and_not_merely_counted():
    """Input the firmware dropped has to be visible, not just countable.

    The gate for this task is that no capture is ever refused. That is only
    checkable if a refusal reaches the trace at all - a counter nobody prints
    would let the gate pass by saying nothing.
    """
    source = (ROOT / "firmware" / "u1_reference" / "host_callbacks.cpp").read_text(
        encoding="utf-8"
    )
    drain_start = source.index("void reference_drain_one_callback")
    drain = source[drain_start:source.index("void reference_service_one_cdc")]

    assert "reference_overflows()" in drain, (
        "the drain never looks at the overflow count, so a refused capture "
        "can never appear in the trace"
    )
    assert "ReferenceCallbackKind::Overflow" in drain, (
        "an overflow is not turned into a trace entry"
    )


def test_post_mount_descriptor_retry_uses_the_pinned_async_api_safely():
    source = (ROOT / "firmware" / "u1_reference" / "main.cpp").read_text(
        encoding="utf-8"
    )

    compact = re.sub(r"\s+", " ", source)
    exact_call = re.compile(
        r"tuh_descriptor_get_hid_report\( "
        r"descriptor_request\.dev_addr, info\.desc\.bInterfaceNumber, "
        r"HID_DESC_TYPE_REPORT, 0, g_post_mount_descriptor\.data\(\), "
        r"descriptor_request\.length, post_mount_descriptor_complete, "
        r"g_descriptor_diagnostic\.lifetime_token\(\)\)"
    )
    assert "tuh_hid_itf_get_info" in source
    assert exact_call.search(compact), (
        "tuh_descriptor_get_hid_report takes bInterfaceNumber, not HID instance"
    )
    assert re.search(
        r"g_descriptor_diagnostic\.complete\([^;]+xfer->actual_len",
        compact,
    ), "an async completion must compare only the bytes actually transferred"
    assert "static std::array<std::uint8_t" in source, (
        "the async descriptor buffer must outlive the initiating stack frame"
    )


def test_desc64_measurement_requests_and_compares_one_packet_only():
    source = (ROOT / "firmware" / "u1_reference" / "main.cpp").read_text(
        encoding="utf-8"
    )
    adapter = (ROOT / "firmware" / "u1_reference" / "source_adapter.cpp").read_text(
        encoding="utf-8"
    )

    assert "kAulaKeyboardDescriptorLength = 64" in adapter
    assert "g_descriptor_diagnostic.complete(" in source, (
        "the TinyUSB completion must enter the tested diagnostic coordinator"
    )
    assert "xfer->actual_len" in source
    callbacks = (ROOT / "firmware" / "u1_reference" / "host_callbacks.cpp").read_text(
        encoding="utf-8"
    )
    assert "reference_service_cdc(writer)" in callbacks, (
        "the TinyUSB CDC wrapper must use the tested priority service"
    )
    assert "reference_service_one_cdc();" in source


def test_the_trace_stops_the_instant_a_configurator_frame_decodes():
    """Hardware-confirmed regression (Task 5 fix round 2).

    The reference target's plain-text trace (LINK/REPORT/MOUNT/CTRL_* lines)
    and the configurator's own COBS-framed replies share one physical CDC
    endpoint. A COBS decoder finds its frame boundary at the next zero byte
    regardless of what sits between two replies, so a single trace line
    landing inside or between frames turns the configurator's next reply into
    "malformed COBS frame" - measured on real hardware the first time a
    configurator actually talked to this target after ConfigService was
    linked, not caught by any of 1190 software tests because the emulator
    never exercises real CDC bytes and emits no trace at all.

    A bare ``"reference_service_one_cdc();" in source`` check (see the test
    above this one) is satisfied by an unconditional call and would not have
    caught the regression - the call still exists, just never gated. This
    asserts the actual gate: the call must sit inside
    ``if (!g_config.conversation_active())``, so once ConfigService has
    decoded one real frame the trace stops going out until the host
    genuinely disconnects (see ConfigService::conversation_active's own
    comment in config_service.hpp for why that is the only "gone" this
    device can reliably measure).

    firmware/u1_main's own ConfigService::conversation_active() behaviour -
    that a valid frame sets it and on_disconnect() clears it - is covered
    natively in tests/firmware_native/test_config_service.cpp, which drives
    the same on_cdc_bytes() path this device's tud_cdc_read()/on_cdc_bytes()
    call does. What only this source check can cover is that main.cpp
    actually reads that flag before writing to the shared endpoint - the
    half of the bug native tests cannot reach.
    """
    source = (ROOT / "firmware" / "u1_reference" / "main.cpp").read_text(
        encoding="utf-8"
    )
    compact = re.sub(r"\s+", " ", source)

    assert re.search(
        r"if\s*\(\s*!g_config\.conversation_active\(\)\s*\)\s*\{\s*"
        r"reference_service_one_cdc\(\);",
        compact,
    ), (
        "reference_service_one_cdc() must be called only inside "
        "'if (!g_config.conversation_active())', or a trace line can land "
        "inside a configurator's own CDC session and corrupt its framing"
    )


def test_starting_a_conversation_discards_whatever_trace_was_already_queued():
    """Hardware-confirmed regression (Task 5 fix round 3).

    Fix round 2's gate stops the trace only from the instant a conversation
    begins - it cannot un-send bytes the sink already had queued for
    transmission. On real hardware the device had been streaming trace text
    since power-on, so the very first reply of a conversation was still
    preceded by whatever was already in the CDC TX FIFO: the configurator's
    frame decoder read that leftover as the start of the reply and reported
    "invalid CDC magic" - a different, later failure than fix round 2's
    "malformed COBS frame", which is exactly what a fix at the right layer
    of the same bug looks like.

    ConfigService::handle_frame calls sink_.clear_pending() exactly once per
    conversation - covered natively in
    tests/firmware_native/test_config_service.cpp, which drives the same
    on_cdc_bytes() path this device's tud_cdc_read()/on_cdc_bytes() call
    does. What only this source check can cover is that the reference
    target's own CdcSink actually discards anything when asked:
    CdcSink::clear_pending() defaults to doing nothing, so a target that
    never overrides it would pass every native ConfigService test while
    still shipping the exact bytes that produced this measurement.
    """
    source = (ROOT / "firmware" / "u1_reference" / "main.cpp").read_text(
        encoding="utf-8"
    )
    compact = re.sub(r"\s+", " ", source)

    assert re.search(
        r"void\s+clear_pending\(\)\s+override\s*\{\s*tud_cdc_write_clear\(\);",
        compact,
    ), (
        "CdcWriter::clear_pending() must call tud_cdc_write_clear(), or "
        "trace text already queued before a conversation begins can still "
        "precede that conversation's first reply"
    )

    # Fix round 5. Clearing the TX FIFO says nothing about the trace bytes
    # that already left it: USB ships whole packets, so the host's buffer can
    # end mid-line with no terminator of its own, and the reply written next
    # fuses with that fragment exactly as an unterminated line used to. One
    # zero byte after the clear closes whatever is already in flight into its
    # own candidate. Only a source check can cover this: tud_cdc_write is a
    # device-stack call with no native implementation, and main.cpp is not
    # desktop-buildable.
    assert re.search(
        r"void\s+clear_pending\(\)\s+override\s*\{\s*tud_cdc_write_clear\(\);"
        r"\s*const\s+std::uint8_t\s+terminator\s*=\s*0;"
        r"\s*tud_cdc_write\(&terminator,\s*1\);",
        compact,
    ), (
        "CdcWriter::clear_pending() must write one zero byte after clearing, "
        "or a half-transmitted trace line can still fuse with the first reply "
        "of a conversation"
    )


def test_desc64_completion_pins_the_lifetime_token_it_was_handed():
    """The token is the only thing separating a stale callback from a live one.

    A mutation replacing ``xfer->user_data`` with ``0`` or with the
    coordinator's always-current token cannot be caught natively, because the
    call site is the TinyUSB wrapper. Pin it here.
    """
    source = (ROOT / "firmware" / "u1_reference" / "main.cpp").read_text(
        encoding="utf-8"
    )
    compact = re.sub(r"\s+", " ", source)

    assert re.search(
        r"g_descriptor_diagnostic\.complete\([^;]*xfer->actual_len[^;]*"
        r"xfer->user_data\)",
        compact,
    ), (
        "the completion must carry the token TinyUSB handed back, not 0 and "
        "not the coordinator's current token"
    )


def test_the_desc64_request_buffer_is_poisoned_before_every_attempt():
    """A reused buffer turns a transfer that writes nothing into a MATCH."""
    source = (ROOT / "firmware" / "u1_reference" / "main.cpp").read_text(
        encoding="utf-8"
    )
    compact = re.sub(r"\s+", " ", source)

    assert re.search(
        r"poison_descriptor_buffer\(g_post_mount_descriptor\); "
        r"const bool accepted = tuh_descriptor_get_hid_report\(",
        compact,
    ), (
        "the request buffer must be poisoned immediately before every on-wire "
        "attempt, with nothing between the two"
    )


def test_a_desc64_give_up_is_reported_rather_than_leaving_silence():
    """Silence is indistinguishable from a board that was never flashed."""
    source = (ROOT / "firmware" / "u1_reference" / "main.cpp").read_text(
        encoding="utf-8"
    )

    assert "g_adapter.take_descriptor_giveup(" in source
    assert "g_descriptor_diagnostic.skipped(" in source
    assert "ReferenceDescriptorReason::NoInterface" in source, (
        "a failed tuh_hid_itf_get_info() must name itself rather than drain "
        "the offer budget in silence"
    )
    assert "ReferenceDescriptorReason::Unmounted" in source


def test_stale_control_work_cannot_hide_an_unmount_forever():
    source = (ROOT / "firmware" / "u1_reference" / "main.cpp").read_text(
        encoding="utf-8"
    )
    compact = re.sub(r"\s+", " ", source)

    assert re.search(
        r"protocol_request_held\.action\(tuh_hid_mounted\(\s*"
        r"protocol_request\.dev_addr, protocol_request\.instance\)\)",
        compact,
    )
    assert "g_descriptor_diagnostic.abandon_if_unmounted(" in source
    callbacks = (ROOT / "firmware" / "u1_reference" / "host_callbacks.cpp").read_text(
        encoding="utf-8"
    )
    umount = callbacks[
        callbacks.index("void tuh_hid_umount_cb"):
        callbacks.index("void tuh_hid_report_received_cb")
    ]
    assert (
        "reference_descriptor_unmounted(dev_addr, instance, time_us_32())"
        in umount
    )


def test_the_control_trace_is_drained_and_printed_only_from_core_0():
    """Nothing about the packet trace may run on the host core.

    The ring is filled inside Pico-PIO-USB's transaction path; everything on
    this side of it - draining, formatting, CDC - belongs to Core 0's device
    loop, on the far side of the same boundary the report trace already
    respects.
    """
    callbacks = (
        ROOT / "firmware" / "u1_reference" / "host_callbacks.cpp"
    ).read_text(encoding="utf-8")

    assert "pio_usb_host_ctrl_trace_take" in callbacks, (
        "nothing drains the Pico-PIO-USB control trace ring"
    )
    assert "pio_usb_host_ctrl_trace_lost" in callbacks, (
        "the ring's overflow count is never read, so a dropped packet would "
        "leave a hole in the trace that reads like a packet never sent"
    )
    assert "reference_set_control_trace_source" in callbacks

    service = callbacks[callbacks.index("void reference_service_one_cdc") :]
    assert "reference_set_control_trace_source" in service, (
        "the trace source is not installed from the Core 0 CDC service"
    )

    # Core 1's drain must not touch it.
    core1_drain = callbacks[
        callbacks.index("void reference_drain_one_callback") : callbacks.index(
            "void reference_service_one_cdc"
        )
    ]
    for forbidden in ("pio_usb_host_ctrl_trace_take", "reference_service_cdc"):
        assert forbidden not in core1_drain, (
            f"{forbidden} is called from the Core 1 drain"
        )

    # And no host callback may format or write it.
    for callback in ("tuh_hid_mount_cb", "tuh_hid_report_received_cb"):
        start = callbacks.index(f"void {callback}")
        body = callbacks[start : start + 1200]
        for forbidden in ("snprintf", "tud_cdc_write", "pio_usb_host_ctrl_trace"):
            assert forbidden not in body, (
                f"{forbidden} is called from {callback}, on the core that "
                "drives tuh_task"
            )


def test_both_descriptor_experiments_run_from_one_boot():
    """Two boots would compare two different device states.

    The 64-byte read and the 77-byte read have to happen in the same session,
    against the same enumeration, or the answer to one says nothing about the
    other.
    """
    adapter = (ROOT / "firmware" / "u1_reference" / "source_adapter.cpp").read_text(
        encoding="utf-8"
    )
    source = (ROOT / "firmware" / "u1_reference" / "main.cpp").read_text(
        encoding="utf-8"
    )

    assert "kAulaKeyboardDescriptorLength = 64" in adapter
    assert "kAulaKeyboardFollowupLength = 77" in adapter, (
        "the follow-up must request the whole 77-byte document, not another "
        "single packet"
    )
    assert "g_descriptor_diagnostic.take_completed()" in source, (
        "nothing notices that the first attempt completed, so the follow-up "
        "is never armed"
    )
    assert "g_adapter.schedule_descriptor_followup(" in source


def test_descriptor_completion_uses_the_transfer_result_and_current_mount_state():
    source = (ROOT / "firmware" / "u1_reference" / "main.cpp").read_text(
        encoding="utf-8"
    )
    compact = re.sub(r"\s+", " ", source)
    assert re.search(
        r"g_descriptor_diagnostic\.complete\( xfer->daddr, "
        r"xfer->result == XFER_RESULT_SUCCESS, xfer->actual_len, "
        r"tuh_hid_mounted\(dev_addr, instance\)",
        compact,
    )


def test_give_up_is_drained_before_a_completion_arms_the_follow_up():
    source = (ROOT / "firmware" / "u1_reference" / "main.cpp").read_text(
        encoding="utf-8"
    )
    give_up = source.index("g_adapter.take_descriptor_giveup(")
    completion = source.index("g_descriptor_diagnostic.take_completed()")
    assert give_up < completion


def test_the_follow_up_reuses_the_poisoned_request_path():
    """One request path, one poison, one on-wire attempt per experiment.

    A second code path for the second measurement is a second place for the
    poison to be forgotten, and a buffer that still holds the first attempt's
    bytes turns a transfer that writes nothing into a MATCH.
    """
    source = (ROOT / "firmware" / "u1_reference" / "main.cpp").read_text(
        encoding="utf-8"
    )
    assert source.count("tuh_descriptor_get_hid_report(") == 1, (
        "the follow-up must reuse the single, poisoned request path"
    )
    assert source.count("poison_descriptor_buffer(") == 1
    assert re.search(
        r"g_descriptor_diagnostic\.start\(\s*descriptor_request\.dev_addr,"
        r"\s*descriptor_request\.instance,\s*descriptor_request\.length\)",
        source,
    ), (
        "the diagnostic must be told the length that was actually requested, "
        "so DESC64_ and DESC77_ cannot be confused"
    )
