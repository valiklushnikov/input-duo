"""Compile current input callers with each configured target's real ARM flags.

These frame ceilings catch report-set copies returning to persistent caller
stacks. Transitive path and IRQ budgets are documented separately. No ELF or
previous .su file can satisfy this check: every invocation recompiles source.
"""
from pathlib import Path
import os
import re
import shlex
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("preset,source,symbol,ceiling", [
    ("pico-release", "firmware/u1_main/main.cpp", "core1_entry", 256),
    ("pico-pio-usb-release", "firmware/u1_main/main.cpp", "core1_entry", 256),
    ("pico-pio-usb-reference-release", "firmware/u1_reference/main.cpp", "service_input", 512),
    ("pico-release", "firmware/u1_main/mapping/engine.cpp", "BindingEngine::handle", 256),
    ("pico-release", "firmware/u1_main/mapping/capture.cpp", "CaptureController::fill_source", 64),
    ("pico-release", "firmware/u1_main/ch375/descriptor_setup.cpp", "DescriptorSetup::poll", 256),
    ("pico-pio-usb-reference-release", "firmware/u1_reference/source_adapter.cpp", "ReferenceSourceAdapter::on_mount", 1100),
    ("pico-pio-usb-release", "firmware/u1_main/pio_usb/hid_setup.cpp", "classify_hid_layout", 128),
])
def test_current_arm_input_frame_stays_below_copy_budget(tmp_path, preset, source, symbol, ceiling):
    build = ROOT / "build" / preset
    cache = build / "CMakeCache.txt"
    if not cache.exists() or not shutil.which("arm-none-eabi-g++"):
        pytest.skip("configured ARM target and toolchain required")
    match = re.search(r"^CMAKE_MAKE_PROGRAM:[^=]+=(.+)$", cache.read_text(), re.M)
    assert match, "configured target must identify Ninja"
    commands = subprocess.check_output(
        [match[1], "-C", str(build), "-t", "commands"], text=True)
    candidates = [line for line in commands.splitlines()
                  if " -c " in line and line.replace("\\", "/").rstrip('"').endswith(source)]
    assert len(candidates) == 1, candidates
    command = candidates[0]
    # Keep all target definitions/includes/optimization flags. Only redirect
    # compiler artifacts to this invocation's isolated temporary directory.
    command = re.sub(r" -o \S+", ' -o "' + (tmp_path / "frame.o").as_posix() + '"', command)
    command = re.sub(r" -MF \S+", "", command)
    command = re.sub(r" -MT \S+", "", command)
    command += " -fstack-usage"
    subprocess.run(command if os.name == "nt" else shlex.split(command), cwd=build, check=True)
    frames = [(line.split("\t")[0], int(line.split("\t")[1]))
              for line in (tmp_path / "frame.su").read_text().splitlines()
              if symbol in line]
    assert frames, f"missing emitted {symbol} frame"
    for name, size in frames:
        assert size <= ceiling, f"{preset}: {name}: {size} bytes exceeds {ceiling}"
