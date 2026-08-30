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
from duo_input.ui import theme
from duo_input.ui.models.project_session import SetActiveProfile, default_project


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


def test_the_mouse_button_row_fits_the_minimum_window(qtbot, window, emulator):
    """The detect button shares the row with the chooser and must still fit."""
    from duo_input.generated.protocol import TriggerKind

    _connect(qtbot, window, emulator)
    window.resize(1024, 700)
    window.show()
    qtbot.waitExposed(window)
    window.show_page(MainWindow.PAGE_MOUSE)
    window.mouse.select_trigger_kind(TriggerKind.MOUSE_BUTTON)
    qtbot.waitUntil(lambda: window.mouse.capture_button.isVisible(), timeout=5000)

    for widget in (window.mouse.mouse_combo, window.mouse.capture_button):
        assert widget.isVisible()
        assert widget.width() >= widget.minimumSizeHint().width()
    assert window.mouse.minimumSizeHint().width() <= window.mouse.width()


def _capture_side_button(qtbot, window, button: int = 4) -> None:
    """Bind a side button the way the detect dialog does."""
    from duo_input.domain.models import Trigger
    from duo_input.generated.protocol import ActionKind, TriggerKind

    window.mouse.select_trigger_kind(TriggerKind.MOUSE_BUTTON)
    window.mouse.select_action(ActionKind.TOGGLE_MOUSE_ROUTE, 0)
    window.mouse.apply_captured_trigger(Trigger(TriggerKind.MOUSE_BUTTON, button, 0))
    assert window.mouse.apply_button.isEnabled()
    window.mouse.apply_button.click()


def test_a_captured_side_button_survives_a_device_operation(qtbot, window, emulator):
    """Writing to the device must not un-see a button the mouse reported.

    Every successful operation re-reads what the device advertises, and the
    protocol never advertises button 4. Losing the capture there left the page
    warning that a button it was listing had never been reported.
    """
    _connect(qtbot, window, emulator)
    _capture_side_button(qtbot, window)
    assert window.mouse.existing_list.count() == 1

    window._sync_device_state()

    assert window.mouse.warning_label.text() == ""
    assert window.mouse.mouse_combo.findData(4) >= 0


def test_a_capture_on_one_page_is_seen_by_the_other(qtbot, window, emulator):
    """One mouse is attached, so both editors must offer the same buttons."""
    from duo_input.generated.protocol import TriggerKind

    _connect(qtbot, window, emulator)
    _capture_side_button(qtbot, window)

    # The Bindings chooser fills itself when a mouse trigger is selected.
    window.bindings.select_trigger_kind(TriggerKind.MOUSE_BUTTON)

    assert window.bindings.mouse_combo.findData(4) >= 0


def test_a_reconnect_has_to_see_the_side_button_again(qtbot, window, emulator):
    """A different mouse may be on the other end, so observations do not carry."""
    _connect(qtbot, window, emulator)
    _capture_side_button(qtbot, window)

    window.disconnect_device()
    replacement = U1Emulator()
    replacement.install_active(compile_project_to_binary(default_project()))
    _connect(qtbot, window, replacement)

    assert window.mouse.mouse_combo.findData(4) < 0


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


# ------------------------------------------------- the three project states


def test_the_strip_shows_all_three_states_of_the_specification(window):
    """Section 18.5 names three; the shell shows three, always, side by side."""
    assert set(window.state_chips) == {"changes", "file", "device"}
    for chip in window.state_chips.values():
        assert chip.text()
        assert chip.property("signal")


def test_a_clean_untouched_project_says_so_in_all_three(window):
    changes, file_chip, device = (window.state_chips[key] for key in ("changes", "file", "device"))

    assert changes.property("signal") == theme.SIGNAL_OK
    assert file_chip.property("signal") == theme.SIGNAL_MUTED
    assert device.property("signal") == theme.SIGNAL_MUTED


def test_an_edit_turns_the_changes_chip_and_nothing_else(window):
    before = window.state_chips["file"].text()

    window.apply_command(SetActiveProfile(4))

    assert window.state_chips["changes"].property("signal") == theme.SIGNAL_WARN
    assert window.state_chips["file"].text() == before


def test_saving_names_the_file_and_settles_the_changes_chip(window, tmp_path):
    window.apply_command(SetActiveProfile(4))

    assert window.save_project(tmp_path / "work.duoinput.json") is True

    assert window.state_chips["changes"].property("signal") == theme.SIGNAL_OK
    assert window.state_chips["file"].property("signal") == theme.SIGNAL_OK
    assert "work.duoinput.json" in window.state_chips["file"].text()


def test_a_device_holding_the_same_package_reads_as_agreement(qtbot, window, emulator):
    _connect(qtbot, window, emulator)

    assert window.session.device_matches is True
    assert window.state_chips["device"].property("signal") == theme.SIGNAL_OK


def test_a_device_holding_something_else_reads_as_divergence(qtbot, window, emulator):
    _connect(qtbot, window, emulator)

    window.apply_command(SetActiveProfile(4))

    assert window.session.device_matches is False
    assert window.state_chips["device"].property("signal") == theme.SIGNAL_WARN


def test_losing_the_link_leaves_the_device_chip_with_nothing_to_claim(qtbot, window, emulator):
    _connect(qtbot, window, emulator)

    window.disconnect_device()

    assert window.state_chips["device"].property("signal") == theme.SIGNAL_MUTED


def test_the_save_and_write_buttons_carry_different_weight(window):
    """Write overwrites the device; Save writes a file. They may not look alike."""
    assert window.save_button.property("role") == theme.ROLE_PRIMARY
    assert window.write_button.property("role") == theme.ROLE_DESTRUCTIVE


def test_the_connection_indicator_is_a_chip_that_changes_signal(qtbot, window, emulator):
    assert window.connection_label.property("role") == theme.ROLE_CHIP
    assert window.connection_label.property("signal") == theme.SIGNAL_MUTED

    _connect(qtbot, window, emulator)

    assert window.connection_label.property("signal") == theme.SIGNAL_OK


def test_the_navigation_rail_is_the_one_the_stylesheet_dresses(window):
    assert window.nav.objectName() == theme.NAME_RAIL


def test_the_issue_banner_appears_with_the_issues_and_leaves_with_them(window):
    assert window.issues_banner.isVisibleTo(window) is False

    broken = replace(window.session.project, active_profile_id=99)
    window.set_session(replace(window.session, project=broken))

    assert window.issues_banner.isVisibleTo(window) is True
    assert window.issues_banner.property("signal") == theme.SIGNAL_ERROR
