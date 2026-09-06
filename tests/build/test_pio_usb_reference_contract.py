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
PINNED_PICO_PIO_USB_REVISION = "a2a076497ab6f373ae1c9e98777bf3a0c6f4a40e"
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

    configure = subprocess.run(
        [
            "cmake",
            "--preset",
            preset,
            f"-DCMAKE_MAKE_PROGRAM={make_program}",
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

    # Task 3 admits the input path, so the pipeline is now required rather
    # than forbidden. What stays out is the old host backend, CH375, and
    # everything Task 4 and Task 5 have yet to admit.
    for required_symbol in ("InputPipeline8on_event", "OutputRuntime",
                            "Core1Runtime", "ReferenceSourceAdapter"):
        assert any(required_symbol in name for name in symbols), (
            f"{REFERENCE_ELF} contains no {required_symbol}"
        )

    for excluded in (
        "Ch375Device4tick",
        "PioUsbBackend4task",
        "SpiMaster",
        "ConfigService",
        "CoreBridge",
    ):
        assert not any(excluded in name for name in symbols), (
            f"{REFERENCE_ELF} unexpectedly contains {excluded}"
        )


def test_reference_u2_matches_the_same_toolchain_pio_usb_u2(tmp_path):
    env = os.environ.copy()
    env["SOURCE_DATE_EPOCH"] = FIXED_SOURCE_DATE_EPOCH

    # Hold the same repository-wide interprocess lock used by U1 freshness
    # while both shared U2 graphs are configured, rebuilt, and compared.
    with firmware_artifact_lock(ROOT):
        _, pio_usb_u2_uf2 = _configure_and_rebuild_u2(
            preset=PIO_USB_PRESET,
            build_dir=PIO_USB_BUILD,
            backup_dir=tmp_path / "pio-usb-u2-backup",
            env=env,
        )
        _, reference_u2_uf2 = _configure_and_rebuild_u2(
            preset=REFERENCE_PRESET,
            build_dir=REFERENCE_BUILD,
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
