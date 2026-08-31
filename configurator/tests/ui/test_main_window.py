"""Application shell: three-state UX, write transaction and closing."""

from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtGui import QCloseEvent

from duo_input.device.emulator import U1Emulator
from duo_input.device.service import DeviceService, DeviceState
from duo_input.domain.text_compiler import compile_project_to_binary
from duo_input.generated.protocol import PROFILES
from duo_input.ui.main_window import DIRTY_MARKER, MainWindow
from duo_input.ui import theme
from duo_input.ui.models.project_session import (
    ProjectSession,
    RenameProfile,
    SetActiveProfile,
    default_project,
)


@pytest.fixture
def emulator() -> U1Emulator:
    emulator = U1Emulator()
    emulator.install_active(compile_project_to_binary(default_project()))
    return emulator


@pytest.fixture
def service(qtbot) -> DeviceService:
    return DeviceService(timeout_ms=5000)


@pytest.fixture
def settings(tmp_path) -> "QSettings":
    from PySide6.QtCore import QSettings

    store = QSettings(str(tmp_path / "duo-input.ini"), QSettings.Format.IniFormat)
    store.clear()
    return store


@pytest.fixture
def window(qtbot, service, settings) -> MainWindow:
    # The window attaches itself to whatever the factory offers. In the suite
    # that must be nothing at all: a factory left on its default would open
    # the operator's real device the moment any test built a window.
    window = MainWindow(service, transport_factory=lambda: None, settings=settings)
    qtbot.addWidget(window)
    return window


def _connect(qtbot, window: MainWindow, emulator: U1Emulator) -> None:
    # Only the first connect a window makes queues the read (see
    # MainWindow._startup_read_done), so only the first one has anything to
    # wait for. Asked before the connect, because the flag is set while it
    # runs; waiting on a later connect would simply burn the full timeout.
    expects_read = not window._startup_read_done
    with qtbot.waitSignal(window.service.operation_succeeded, timeout=5000):
        window.connect_device(emulator)
    assert window.service.state is DeviceState.READY
    if not expects_read:
        return
    # That first connect also queues the window's own read of what the device
    # is running (see read_device_project); the request is deferred to the next
    # tick and its answer a few more after that. A caller that goes straight
    # on to its own device operation must not race it, so every test that
    # uses this helper waits for it to land - successfully or not - first.
    # The listener is attached before anything pumps the event loop, so it
    # cannot miss a read that lands on the very first tick.
    settled = [False]

    def _mark_settled(result: object) -> None:
        if getattr(result, "operation", None) == "read_config":
            settled[0] = True

    window.service.operation_succeeded.connect(_mark_settled)
    window.service.operation_failed.connect(_mark_settled)
    try:
        qtbot.waitUntil(lambda: settled[0], timeout=5000)
    finally:
        window.service.operation_succeeded.disconnect(_mark_settled)
        window.service.operation_failed.disconnect(_mark_settled)


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


def test_the_shell_offers_no_connect_button(window):
    """Attaching to the device is not a decision the operator has to make."""
    assert not hasattr(window, "connect_button")


def test_the_window_attaches_itself_to_a_device_that_is_present(qtbot, service, emulator):
    """A device already plugged in is connected without being asked."""
    window = MainWindow(service, transport_factory=lambda: emulator)
    qtbot.addWidget(window)

    qtbot.waitUntil(lambda: service.state is DeviceState.READY, timeout=5000)


def test_the_window_attaches_to_a_device_that_arrives_later(qtbot, service, emulator):
    """Plugging the board in after startup must not require a restart."""
    offered: list[object] = [None]
    window = MainWindow(service, transport_factory=lambda: offered[0])
    qtbot.addWidget(window)
    assert service.is_connected is False

    offered[0] = emulator
    window.try_autoconnect()

    qtbot.waitUntil(lambda: service.is_connected, timeout=5000)


def test_an_absent_device_is_not_announced_over_and_over(qtbot, service):
    """The retry is silent: an empty socket is the normal state, not an event."""
    window = MainWindow(service, transport_factory=lambda: None)
    qtbot.addWidget(window)

    for _ in range(5):
        window.try_autoconnect()

    assert [line for line in window.overview.events() if "connect_device" in line] == []


def test_an_empty_start_says_where_a_configuration_comes_from(qtbot, service, settings):
    """With no board there is nothing to show, so say what to do about it."""
    window = MainWindow(service, transport_factory=lambda: None, settings=settings)
    qtbot.addWidget(window)
    window.try_autoconnect()

    message = window.statusBar().currentMessage()
    assert message
    assert "копи" in message.lower() or "copy" in message.lower()


def test_the_shell_offers_a_way_to_open_a_project(window):
    """Saving a file the program cannot open again is a one-way door."""
    assert window.open_button.isEnabled()
    assert window.open_button.accessibleName()


def test_saving_a_copy_remembers_nothing(qtbot, window, tmp_path, settings):
    """A copy is a copy: it does not become "the open file"."""
    target = tmp_path / "copy.duoinput.json"
    window.set_session(window.session.apply(RenameProfile(1, "Copied")))

    assert window.save_copy(target) is True

    assert target.is_file()
    assert settings.value("projects/last") is None


def test_loading_a_copy_replaces_the_configuration(qtbot, window, tmp_path):
    target = tmp_path / "copy.duoinput.json"
    window.set_session(window.session.apply(RenameProfile(1, "From a copy")))
    assert window.save_copy(target) is True
    window.set_session(ProjectSession.new())

    assert window.load_copy(target) is True

    assert window.session.active_profile.name == "From a copy"


def test_a_copy_that_cannot_be_read_is_reported_not_fatal(qtbot, window, tmp_path):
    broken = tmp_path / "broken.duoinput.json"
    broken.write_text("{ not json", encoding="utf-8")

    assert window.load_copy(broken) is False


def test_the_shell_no_longer_reopens_anything(window):
    assert not hasattr(window, "reopen_last_project")
    assert not hasattr(window, "last_project_path")


def test_a_loaded_copy_is_still_measured_against_the_board(qtbot, window, tmp_path):
    """Loading a backup must not make the program forget the board."""
    from duo_input.ui.models.project_session import RenameProfile

    window.set_session(window.session.with_device_hash(b"\x22" * 32))
    differs = tmp_path / "differs.duoinput.json"
    assert window.save_copy(differs) is True
    window.set_session(window.session.apply(RenameProfile(1, "Elsewhere")))

    assert window.load_copy(differs) is True

    assert window.session.device_hash == "22" * 32
    assert window.session.device_matches is False


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


def test_saving_a_copy_does_not_clear_the_dirty_marker_or_the_device_mismatch(
    qtbot, window, emulator, tmp_path
):
    _connect(qtbot, window, emulator)
    assert window.session.device_matches is True

    window.profile_selector.setCurrentIndex(1)
    assert window.session.dirty is True
    assert window.session.device_matches is False

    assert window.save_copy(tmp_path / "profile.duoinput.json") is True

    assert window.session.dirty is True
    assert DIRTY_MARKER in window.windowTitle()
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


def test_a_successful_write_reads_as_unchanged(qtbot, window, emulator):
    _connect(qtbot, window, emulator)
    window.set_session(window.session.apply(RenameProfile(1, "To write")))
    assert window.session.dirty is True

    with qtbot.waitSignal(window.service.operation_succeeded, timeout=5000):
        window.write_to_device()

    qtbot.waitUntil(lambda: window.session.dirty is False, timeout=5000)


def test_an_edit_made_during_a_write_is_not_declared_written(qtbot, window, emulator):
    """The baseline after a write is what was sent, not what is on screen.

    A write is a long sequence - chunks, WRITE_COMMIT, then a read-back - and
    every step is a full event-loop turn during which the editor pages stay
    live. An edit that lands in one of those turns was compiled into nothing
    and sent nowhere, so afterwards the board has still never seen it and the
    session has to say so.
    """
    _connect(qtbot, window, emulator)
    window.set_session(window.session.apply(RenameProfile(1, "Sent to the board")))

    edited: list[bool] = []

    def _edit_mid_write(_percent: int) -> None:
        if edited:
            return
        edited.append(True)
        window.set_session(window.session.apply(RenameProfile(2, "Typed mid-write")))

    window.service.progress_changed.connect(_edit_mid_write)
    try:
        with qtbot.waitSignal(window.service.operation_succeeded, timeout=20000):
            window.write_to_device()
    finally:
        window.service.progress_changed.disconnect(_edit_mid_write)

    assert edited == [True]
    # The edit is still on screen - a write never touches the project.
    assert window.session.project.profiles[0].name == "Sent to the board"
    assert window.session.project.profiles[1].name == "Typed mid-write"
    # And it was never sent, so it is still pending.
    assert window.session.dirty is True
    assert DIRTY_MARKER in window.windowTitle()


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


# ---------------------------------------------------------- reading a device


def test_a_device_that_answers_supplies_the_project(qtbot, service, emulator, settings):
    """Opening the program answers "what is my device doing?" without being asked."""
    from duo_input.domain.text_compiler import compile_project_to_binary
    from duo_input.ui.models.project_session import RenameProfile, default_project

    wanted = ProjectSession.new().apply(RenameProfile(1, "On the board")).project
    emulator.install_active(compile_project_to_binary(wanted))

    window = MainWindow(service, transport_factory=lambda: emulator, settings=settings)
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: service.state is DeviceState.READY, timeout=5000)
    window.read_device_project()

    qtbot.waitUntil(
        lambda: window.session.active_profile.name == "On the board", timeout=5000
    )
    assert window.session.path is None


def test_a_project_read_from_the_device_reads_as_unchanged(qtbot, service, emulator, settings):
    window = MainWindow(service, transport_factory=lambda: emulator, settings=settings)
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: service.state is DeviceState.READY, timeout=5000)
    window.read_device_project()
    qtbot.waitUntil(lambda: window.session.dirty is False, timeout=5000)

    assert window.session.dirty is False


def test_a_read_that_lands_late_does_not_discard_unsaved_edits(
    qtbot, service, emulator, settings
):
    """The read is several chunks over a serial link. Work started meanwhile stays."""
    from duo_input.domain.text_compiler import compile_project_to_binary
    from duo_input.ui.models.project_session import RenameProfile

    on_board = ProjectSession.new().apply(RenameProfile(1, "From device")).project
    emulator.install_active(compile_project_to_binary(on_board))

    window = MainWindow(service, transport_factory=lambda: emulator, settings=settings)
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: service.state is DeviceState.READY, timeout=5000)

    window.set_session(window.session.apply(RenameProfile(1, "Being typed")))
    assert window.session.dirty

    window.read_device_project()
    qtbot.wait(300)

    assert window.session.active_profile.name == "Being typed"


def test_calling_read_device_project_twice_in_a_row_issues_one_read(
    qtbot, service, emulator, settings
):
    """A second call while the first is still in flight must be a no-op -
    not a second read whose BUSY failure gets mistaken for the real read's
    outcome and silently discards it. (Fix round 1 of task 3: this overlap
    is exactly what Task 4's startup sequence introduces alongside the
    connect-triggered auto-read - both ask for the same read.)
    """
    from duo_input.domain.text_compiler import compile_project_to_binary
    from duo_input.ui.models.project_session import RenameProfile

    wanted = ProjectSession.new().apply(RenameProfile(1, "On the board")).project
    emulator.install_active(compile_project_to_binary(wanted))

    window = MainWindow(service, transport_factory=lambda: emulator, settings=settings)
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: service.state is DeviceState.READY, timeout=5000)

    read_calls: list[None] = []
    original_read_config = service.read_config

    def _counting_read_config() -> None:
        read_calls.append(None)
        original_read_config()

    service.read_config = _counting_read_config

    # Two calls back to back, with no event-loop turn in between: nothing
    # queued elsewhere (the connect-triggered auto-read included) can have
    # run yet, so this counts exactly what these two calls themselves did.
    window.read_device_project()
    window.read_device_project()
    assert len(read_calls) == 1

    qtbot.waitUntil(
        lambda: window.session.active_profile.name == "On the board", timeout=5000
    )


def test_a_device_present_at_startup_still_supplies_the_project(
    qtbot, service, emulator, settings
):
    """The connect-triggered read is what makes startup work; nothing else asks.

    Unlike the test above, nothing here calls ``read_device_project``: the
    window has to ask on its own, which is the whole point of spec section 3
    ("At startup, a device that answers has its configuration read and
    shown").
    """
    from duo_input.ui.models.project_session import RenameProfile

    wanted = ProjectSession.new().apply(RenameProfile(1, "On the board")).project
    emulator.install_active(compile_project_to_binary(wanted))

    window = MainWindow(service, transport_factory=lambda: emulator, settings=settings)
    qtbot.addWidget(window)

    qtbot.waitUntil(
        lambda: window.session.active_profile.name == "On the board", timeout=5000
    )
    assert window.session.path is None


def test_a_device_plugged_in_after_startup_leaves_the_open_project_alone(
    qtbot, service, emulator, settings, tmp_path
):
    """The read belongs to startup, not to every connect.

    The autoconnect timer runs for the life of the window, so without this
    the harm is the program's primary workflow: the operator opens their
    project, edits it, saves a copy, and only then plugs the board in - in
    order to write that project to it. A read fired by that connect would
    replace their project with the board's and drop ``session.path``. The
    next Write then sends the board's own configuration back instead of
    theirs.
    """
    from duo_input.ui.models.project_session import RenameProfile

    on_board = ProjectSession.new().apply(RenameProfile(1, "On the board")).project
    emulator.install_active(compile_project_to_binary(on_board))

    # Startup, with a device attached: the board's configuration is adopted.
    window = MainWindow(service, transport_factory=lambda: emulator, settings=settings)
    qtbot.addWidget(window)
    qtbot.waitUntil(
        lambda: window.session.active_profile.name == "On the board", timeout=5000
    )

    # The operator's own project: edited and saved as a copy.
    saved = tmp_path / "mine.duoinput.json"
    window.set_session(window.session.apply(RenameProfile(1, "Mine")))
    assert window.save_copy(saved) is True
    assert window.session.path == saved

    # They unplug the board and plug it back in, to write their project to it.
    window.disconnect_device()
    later = U1Emulator()
    later.install_active(
        compile_project_to_binary(
            ProjectSession.new().apply(RenameProfile(1, "Still the board")).project
        )
    )
    operations: list[str] = []
    window.service.operation_succeeded.connect(
        lambda result: operations.append(result.operation)
    )
    with qtbot.waitSignal(window.service.operation_succeeded, timeout=5000):
        window.connect_device(later)
    qtbot.wait(500)

    assert window.session.active_profile.name == "Mine"
    assert window.session.path == saved
    assert "read_config" not in operations


def test_a_device_plugged_in_after_a_deviceless_startup_leaves_the_open_project_alone(
    qtbot, window, emulator, tmp_path
):
    """The surviving half of the defect: startup with nothing attached.

    ``_startup_read_done`` is only spent once a connect actually *succeeds*.
    The test above starts with a device present, which spends it before the
    operator ever opens anything. The operator's normal habit is the other
    order: start with no device, work for a while, save a copy, and only
    then plug a board in - to write that project to it. Nothing here has
    spent the flag yet, so the connect below is still this window's startup
    connect and its read *is* issued - which is harmless, because the guard
    that matters refuses the answer: ``_adopt_device_project`` will not
    discard edits the operator has not written anywhere. This is the
    reproduction from the defect report, and the read is deliberately
    asserted to happen so that reinstating a gate in front of it - the one
    that lost the board's own configuration for anybody who exported a
    template - would fail here.
    """
    assert window._startup_read_done is False

    saved = tmp_path / "mine.duoinput.json"
    window.set_session(window.session.apply(RenameProfile(1, "Моя работа")))
    assert window.save_copy(saved) is True
    assert window.session.path == saved

    on_board = ProjectSession.new().apply(RenameProfile(1, "Конфигурация платы")).project
    emulator.install_active(compile_project_to_binary(on_board))
    window.transport_factory = lambda: emulator

    operations: list[str] = []
    window.service.operation_succeeded.connect(
        lambda result: operations.append(result.operation)
    )
    with qtbot.waitSignal(window.service.operation_succeeded, timeout=5000):
        window.try_autoconnect()
    qtbot.waitUntil(lambda: "read_config" in operations, timeout=5000)
    qtbot.wait(500)

    # The board answered, and the answer was refused rather than never asked.
    assert window.session.active_profile.name == "Моя работа"
    assert window.session.path == saved
    assert "your edits were kept" in window.statusBar().currentMessage()
    assert window.session.device_matches is False


def test_a_device_that_turns_up_clears_the_no_device_message(qtbot, window, emulator):
    """The status bar must not contradict the connection chip.

    "No device found..." is posted with no timeout, so it stays on screen
    until something replaces it - and on any connect after the first, nothing
    does: the startup read has already been spent, and no other step on the
    connect path writes to the status bar. The window then says the device is
    ready in one place and absent in another, on what the code's own comments
    call the primary workflow.
    """
    # Nothing attached: the message is posted, and posted without a timeout.
    window.try_autoconnect()
    assert window._said_no_device is True
    assert "No device found" in window.statusBar().currentMessage()

    # A board turns up. The handshake takes several event-loop turns, so the
    # message has to go as the attempt begins - not be left standing behind
    # whatever some later step happens to say, on the connects where no later
    # step says anything at all.
    window.transport_factory = lambda: emulator
    window.try_autoconnect()

    assert window._said_no_device is False
    assert "No device found" not in window.statusBar().currentMessage()

    with qtbot.waitSignal(window.service.operation_succeeded, timeout=5000):
        pass
    assert window.service.is_connected is True
    assert "No device found" not in window.statusBar().currentMessage()


def test_a_template_saved_before_the_board_arrives_still_lets_the_board_be_read(
    qtbot, window, emulator, tmp_path
):
    """Saving a copy must not silence the board that turns up afterwards.

    An operator with no board attached exports the untouched default
    configuration as a template, then plugs the board in. A gate on
    ``session.path`` - which nothing but ``save_copy`` sets, and which has no
    UI surface at all now the file name has left the title - meant the board
    was never read. The chip then said "differs from the project", Write was
    enabled, and pressing it overwrote the board's real configuration, which
    the spec (section 6) calls unrecoverable.
    """
    assert window.session.dirty is False
    assert window.save_copy(tmp_path / "template.duoinput.json") is True
    assert window.session.path is not None

    on_board = ProjectSession.new().apply(RenameProfile(1, "Конфигурация платы")).project
    emulator.install_active(compile_project_to_binary(on_board))
    window.transport_factory = lambda: emulator

    window.try_autoconnect()

    qtbot.waitUntil(
        lambda: window.session.active_profile.name == "Конфигурация платы", timeout=5000
    )
    # And so the board is not about to be overwritten with the template.
    assert window.session.device_matches is True


def test_a_device_plugged_in_after_startup_into_an_untouched_session_is_adopted(
    qtbot, window, emulator
):
    """The case the operator's decision keeps: nothing of theirs to lose yet.

    No file has been opened or saved, and there are no edits, so a device
    that answers only after startup is still trusted - same as one that
    answers during it. What matters is the session, not the clock.
    """
    assert window.session.path is None
    assert window.session.dirty is False

    on_board = ProjectSession.new().apply(RenameProfile(1, "Конфигурация платы")).project
    emulator.install_active(compile_project_to_binary(on_board))
    window.transport_factory = lambda: emulator

    window.try_autoconnect()

    qtbot.waitUntil(
        lambda: window.session.active_profile.name == "Конфигурация платы", timeout=5000
    )
    assert window.session.path is None


def test_a_disconnect_during_a_read_does_not_disable_reading_for_good(
    qtbot, window, emulator
):
    """A read in flight when the link goes away must not wedge the window.

    ``DeviceService.disconnect_device`` clears its operation before tearing
    the link down, deliberately reporting no failure - so nothing on the
    service side ever clears the window's own in-flight flag. Left set, it
    makes ``read_device_project`` refuse every later read for the life of the
    window, silently and permanently.
    """
    from duo_input.ui.models.project_session import RenameProfile

    _connect(qtbot, window, emulator)
    window.read_device_project()
    assert window._reading_device is True

    window.disconnect_device()

    assert window._reading_device is False

    later = U1Emulator()
    later.install_active(
        compile_project_to_binary(
            ProjectSession.new().apply(RenameProfile(1, "Second board")).project
        )
    )
    _connect(qtbot, window, later)
    window.read_device_project()

    qtbot.waitUntil(
        lambda: window.session.active_profile.name == "Second board", timeout=5000
    )


# --------------------------------------------------------------- close flow


def test_closing_never_asks_about_saving(window):
    """There is no document to lose: the configuration lives on the board."""
    window.set_session(window.session.apply(RenameProfile(1, "Unwritten")))
    assert window.session.dirty is True
    assert not hasattr(window, "_confirm_close")

    assert _close(window) is True


# ------------------------------------------------- the three project states


def test_the_strip_states_the_device_once(window):
    """Two chips saying the same thing differently is how the confusion began."""
    assert set(window.state_chips) == {"device"}


def test_the_title_names_the_program_not_a_file(qtbot, window, tmp_path):
    window.save_copy(tmp_path / "copy.duoinput.json")

    assert "copy" not in window.windowTitle()
    assert "duoinput" not in window.windowTitle()


def test_an_edit_turns_the_device_chip_to_its_warning_state(qtbot, window, emulator):
    """An unwritten edit is exactly what "differs from the device" means now."""
    _connect(qtbot, window, emulator)
    assert window.state_chips["device"].property("signal") == theme.SIGNAL_OK

    window.apply_command(SetActiveProfile(4))

    assert window.state_chips["device"].property("signal") == theme.SIGNAL_WARN


def test_a_window_that_has_seen_no_board_says_so_and_says_something(window):
    """The first thing an operator sees: no link yet, stated in words."""
    chip = window.state_chips["device"]

    assert chip.text()
    assert chip.property("signal") == theme.SIGNAL_MUTED


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


def test_changing_the_page_still_shows_the_page(qtbot, window):
    """The fade must never leave a page stranded behind a half-applied effect."""
    window.show_page(MainWindow.PAGE_MOUSE)

    assert window.pages.currentWidget() is window.mouse
    assert window.mouse.graphicsEffect() is None

    window.show_page(MainWindow.PAGE_OVERVIEW)

    assert window.pages.currentWidget() is window.overview
    assert window.overview.graphicsEffect() is None


def test_the_shell_keeps_no_autosave(window):
    """Autosave guarded edits between runs of a document that no longer exists."""
    assert not hasattr(window, "autosave")
    assert not hasattr(window, "offer_recovery")
    assert not hasattr(window, "autosave_now")
