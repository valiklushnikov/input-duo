"""Emit a vector the main interoperability ones cannot distinguish.

valid_full.bin carries consumer usage 0x00E9, which reads the same whether
the parser takes two bytes or one, and its macro sits at a slot whose number
happens to match its identifier. Neither can fail against a broken reader.
This one uses a usage with a nonzero high byte and macro 200.
"""

import sys
from pathlib import Path
ROOT = Path(".").resolve()
sys.path.insert(0, str(ROOT / "configurator" / "src"))
from duo_input.domain.config_binary import compile_device_config
from duo_input.domain.models import (Action, ActionKind, Binding, BindingMode, DeviceConfig,
                                     KeyboardRoute, Macro, MacroStep, MouseRoute, Profile,
                                     TargetMode, TextLayout, Trigger, TriggerKind)
from duo_input.generated.protocol import MacroStepType

def empty(i):
    return Profile(i, "P%d" % i, (i, i, i), KeyboardRoute.PC1, MouseRoute.PC1, TextLayout.US, (), ())

# A consumer usage whose high byte is not zero, and a macro numbered high
# enough that a wire identifier cannot be used as an owner slot.
steps = (MacroStep(MacroStepType.CONSUMER_TAP, b"\xb1\x01"),)
macros = (Macro(9, "low", TargetMode.INHERIT, ()), Macro(200, "high", TargetMode.INHERIT, steps))
bindings = (Binding(Trigger(TriggerKind.KEYBOARD_USAGE, 6), BindingMode.REPLACE,
                    Action(ActionKind.RUN_MACRO, 200)),)
profiles = [empty(i) for i in range(1, 9)]
from dataclasses import replace
profiles[0] = replace(profiles[0], bindings=bindings, macros=macros)
out = ROOT / "tests" / "vectors" / "config_vectors" / "valid_wide_usages.bin"
out.write_bytes(compile_device_config(DeviceConfig(1, tuple(profiles))))
print("wrote", out, out.stat().st_size, "bytes")
