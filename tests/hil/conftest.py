"""Make the runner and the payload parser importable without installing either.

The HIL runner reads GET_DIAGNOSTICS through the configurator's own parser
rather than a second copy of it. A second copy would agree with the first right
up to the moment somebody edited one of them, and the disagreement would be a
report attributing samples to the wrong bucket - which is the failure this whole
acceptance path exists to catch.

The parser is pure Python and pulls in no Qt, so putting the configurator's
source on the path is enough for every test here to run in either environment.
Skipping them where the configurator is not installed would be the alternative,
and a suite that quietly shrinks in one environment is how a check stops being
run without anyone deciding that it should not be.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]

for entry in (_HERE, _ROOT / "configurator" / "src"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
