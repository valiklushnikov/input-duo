"""Headless Qt defaults for the UI tests.

The theme is installed on the application the whole session shares, so the
size and clipping assertions elsewhere are made against the interface that
actually ships rather than against unstyled stock widgets.
"""

from __future__ import annotations

import os

import pytest

# Must be set before pytest-qt instantiates the QApplication.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session", autouse=True)
def themed_application(qapp):
    """Dress the shared application before the first widget is built."""
    from duo_input.ui.theme import apply_theme

    apply_theme(qapp)
    return qapp


@pytest.fixture(autouse=True)
def no_real_u2_link(monkeypatch):
    """Controller ruling: a real U1 and a real U2 are plugged into this
    machine - no test here may open a real serial port.

    ``_ClipboardRuntime._start`` (app.py) builds an ``EndpointService()``
    with no ``link_factory`` argument, which falls back to the module-level
    ``duo_input.device.endpoint_service.default_link_factory`` - the
    function that opens the first real U2 port found. ``EndpointService.
    __init__`` resolves that name from its own module's globals at
    construction time (``self._factory = link_factory or
    default_link_factory``), which happens inside a test function's body,
    *after* this autouse fixture has already patched the module attribute -
    so patching the module-level name here is enough; ``EndpointService``
    does not need to change to resolve it any later than that.
    """
    monkeypatch.setattr(
        "duo_input.device.endpoint_service.default_link_factory", lambda: None
    )
