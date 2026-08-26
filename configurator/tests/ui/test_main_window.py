"""Application shell: three-state UX, write transaction and close confirmation."""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMessageBox

from duo_input.device.emulator import U1Emulator
from duo_input.device.service import DeviceService, DeviceState
from duo_input.domain.text_compiler import compile_project_to_binary
from duo_input.generated.protocol import PROFILES
from duo_input.ui.main_window import DIRTY_MARKER, MainWindow
from duo_input.ui.models.project_session import default_project


@pytest.fixture
def emulator() -> U1Emulator:
    emulator = U1Emulator()
    emulator.install_active(compile_project_to_binary(default_project()))
    return emulator


@pytest.fixture
def service(qtbot) -> DeviceService:
    return DeviceService(timeout_ms=5000)


def _discard_on_teardown(window: MainWindow) -> None:
    """Answer the close prompt for pytest-qt.

    pytest-qt closes every registered widget during teardown, before fixture
    finalizers run. A test that leaves the session dirty would open the real
    modal save prompt there with no event loop left to answer it, blocking the
    run. The prompt itself is exercised by the close-flow tests below, which
    install their own answer.
    """
    window._confirm_close = lambda: QMessageBox.StandardButton.Discard  # type: ignore[method-assign]


@pytest.fixture
def window(qtbot, service) -> MainWindow:
    window = MainWindow(service)
    qtbot.addWidget(window, before_close_func=_discard_on_teardown)
    return window


def _connect(qtbot, window: MainWindow, emulator: U1Emulator) -> None:
    with qtbot.waitSignal(window.service.operation_succeeded, timeout=5000):
        window.connect_device(emulator)
    assert window.service.state is DeviceState.READY


def _close(window: MainWindow) -> bool:
    """Send a close event the way the window manager would; True when accepted."""
    event = QCloseEvent()
    window.closeEvent(event)
    return event.isAccepted()


# --------------------------------------------------------------------- shell


def test_window_starts_clean_with_the_default_project(window):
    assert window.session.dirty is False
    assert DIRTY_MARKER not in window.windowTitle()
    assert window.profile_selector.count() == PROFILES
    assert window.nav.count() >= 1
    assert window.save_button.isEnabled() is True
    assert window.write_button.isEnabled() is False


def test_minimum_window_size_shows_every_control(qtbot, window):
    window.resize(1024, 700)
    window.show()
    qtbot.waitExposed(window)

    assert window.minimumWidth() == 1024
    assert window.minimumHeight() == 700
    central = window.centralWidget()
    assert central.minimumSizeHint().width() <= central.width()
    assert central.minimumSizeHint().height() <= central.height()
    for widget in (
        window.nav,
        window.profile_selector,
        window.connection_label,
        window.save_button,
        window.write_button,
    ):
        assert widget.isVisible()
        assert widget.width() >= widget.minimumSizeHint().width()
        assert widget.height() >= widget.minimumSizeHint().height()


def test_primary_controls_expose_translated_accessible_names(window):
    for widget in (
        window.nav,
        window.profile_selector,
        window.connection_label,
        window.save_button,
        window.write_button,
    ):
        assert widget.accessibleName()


def test_tab_order_follows_the_visual_order(qtbot, window):
    window.show()
    qtbot.waitExposed(window)

    wanted = [
        window.profile_selector,
        window.connect_button,
        window.save_button,
        window.write_button,
        window.nav,
    ]
    seen: list[object] = []
    widget = window.profile_selector
    for _ in range(200):
        if widget in wanted and widget not in seen:
            seen.append(widget)
        widget = widget.nextInFocusChain()
        if len(seen) == len(wanted):
            break

    assert seen == wanted


# ------------------------------------------------------------- dirty marker


def test_editing_the_active_profile_marks_the_title_dirty(window):
    window.profile_selector.setCurrentIndex(2)

    assert window.session.project.active_profile_id == 3
    assert window.session.dirty is True
    assert window.windowTitle().endswith(DIRTY_MARKER)


def test_save_clears_the_dirty_marker_but_not_a_device_mismatch(qtbot, window, emulator, tmp_path):
    _connect(qtbot, window, emulator)
    assert window.session.device_matches is True

    window.profile_selector.setCurrentIndex(1)
    assert window.session.dirty is True
    assert window.session.device_matches is False

    assert window.save_project(tmp_path / "profile.duoinput.json") is True

    assert window.session.dirty is False
    assert DIRTY_MARKER not in window.windowTitle()
    assert window.session.device_matches is False
    assert window.session.device_hash == window.service.device_hash.hex()


# ------------------------------------------------------------------- write


def test_write_button_is_disabled_without_a_device(window):
    assert window.session.can_write is False
    assert window.write_button.isEnabled() is False


def test_write_button_is_disabled_when_validation_reports_issues(qtbot, window, emulator):
    _connect(qtbot, window, emulator)
    assert window.write_button.isEnabled() is True

    broken = replace(window.session.project, active_profile_id=99)
    window.set_session(replace(window.session, project=broken))

    assert window.session.issues != ()
    assert window.session.can_write is False
    assert window.write_button.isEnabled() is False


def test_successful_write_aligns_the_device_hash(qtbot, window, emulator):
    _connect(qtbot, window, emulator)
    window.profile_selector.setCurrentIndex(4)
    assert window.session.device_matches is False

    with qtbot.waitSignal(window.service.operation_succeeded, timeout=20000) as blocker:
        window.write_to_device()

    assert blocker.args[0].operation == "write_config"
    assert window.session.device_hash == window.session.compiled_hash
    assert window.session.device_matches is True
    assert emulator.active_hash.hex() == window.session.compiled_hash


def test_failed_write_leaves_the_mismatch_visible(qtbot, window, emulator):
    _connect(qtbot, window, emulator)
    before = window.session.device_hash
    window.profile_selector.setCurrentIndex(4)
    emulator.inject_bad_crc_response()

    with qtbot.waitSignal(window.service.operation_failed, timeout=20000) as blocker:
        window.write_to_device()

    failure = blocker.args[0]
    assert failure.operation == "write_config"
    assert window.session.device_hash == before
    assert window.session.device_matches is False
    assert any(failure.reason.value in event for event in window.overview.events())


def test_write_is_a_no_op_when_it_is_not_allowed(window):
    calls: list[bytes] = []
    window.service.write_config = calls.append  # type: ignore[method-assign]

    window.write_to_device()

    assert calls == []


# --------------------------------------------------------------- connection


def test_connection_indicator_reports_the_device_state(qtbot, window, emulator):
    assert DeviceState.DISCONNECTED.value in window.connection_label.text()

    _connect(qtbot, window, emulator)

    assert DeviceState.READY.value in window.connection_label.text()
    assert window.session.device_hash == emulator.active_hash.hex()

    window.disconnect_device()

    assert DeviceState.DISCONNECTED.value in window.connection_label.text()
    assert window.session.device_hash == ""
    assert window.session.can_write is False


def test_connect_without_a_device_reports_it_and_stays_disconnected(window):
    window.transport_factory = lambda: None

    window.connect_device()

    assert window.service.state is DeviceState.DISCONNECTED
    assert window.overview.events()


# --------------------------------------------------------------- close flow


def test_closing_a_clean_project_never_asks(window):
    asked: list[int] = []
    window._confirm_close = lambda: asked.append(1)  # type: ignore[method-assign]

    assert _close(window) is True
    assert asked == []


def test_close_with_unsaved_changes_can_be_cancelled(window):
    window.profile_selector.setCurrentIndex(2)
    window._confirm_close = lambda: QMessageBox.StandardButton.Cancel  # type: ignore[method-assign]

    assert _close(window) is False
    assert window.session.dirty is True


def test_close_with_unsaved_changes_can_discard(window):
    window.profile_selector.setCurrentIndex(2)
    window._confirm_close = lambda: QMessageBox.StandardButton.Discard  # type: ignore[method-assign]

    assert _close(window) is True
    assert window.session.dirty is True  # discarded, not written anywhere


def test_close_with_unsaved_changes_can_save_first(window, tmp_path):
    path = tmp_path / "profile.duoinput.json"
    window.set_session(replace(window.session, path=path))
    window.profile_selector.setCurrentIndex(2)
    window._confirm_close = lambda: QMessageBox.StandardButton.Save  # type: ignore[method-assign]

    assert _close(window) is True
    assert window.session.dirty is False
    assert path.exists()
    assert window.session.file_hash == hashlib.sha256(path.read_bytes()).hexdigest()


def test_close_is_cancelled_when_saving_is_cancelled(window):
    window.profile_selector.setCurrentIndex(2)
    window._confirm_close = lambda: QMessageBox.StandardButton.Save  # type: ignore[method-assign]
    window._ask_save_path = lambda: None  # type: ignore[method-assign]

    assert _close(window) is False
    assert window.session.dirty is True
