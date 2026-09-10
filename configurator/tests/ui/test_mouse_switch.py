"""The Mouse page: pick a trigger, then say which computer the mouse serves."""

from __future__ import annotations

import struct

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton

from duo_input.device.emulator import U1Emulator
from duo_input.device.qt_transport import SynchronousTransportLink
from duo_input.device.service import DeviceService
from duo_input.device.transactions import PeripheralPort
from duo_input.domain.models import Action, Binding, Trigger, TriggerSource
from duo_input.generated.protocol import (
    ActionKind,
    BindingMode,
    MouseRoute,
    TriggerKind,
)
from duo_input.ui.models.binding_table import (
    LEFT_CTRL,
    STANDARD_MOUSE_BUTTONS,
    MouseCapabilities,
)
from duo_input.ui.bindings import CaptureDialog
from duo_input.ui.models.project_session import (
    AddBinding,
    ProjectSession,
    UpdateBinding,
)
from duo_input.ui.mouse import MouseSwitchPage
from duo_input.ui import theme


#: The mouse U1 reports on its own bus in these tests. Test data, never a
#: production constant: nothing in the program may know this pair.
MOUSE_VID = 0x3434
MOUSE_PID = 0xD030

#: A "Right" arrow with Ctrl, on interface 2 of that mouse - the shape a mouse
#: whose side button is wired to a keyboard usage actually reports.
CAPTURED_SOURCE = TriggerSource(MOUSE_VID, MOUSE_PID, 2)
CAPTURED_KEY = 0x4F


def _attached_mouse() -> PeripheralPort:
    return PeripheralPort(
        attached=True,
        ready=True,
        kind="mouse",
        vendor_id=MOUSE_VID,
        product_id=MOUSE_PID,
        buttons=5,
        report_descriptor_bytes=0,
        descriptor_hash=None,
    )


def _capture_payload(kind: int, code: int, modifiers: int, source: TriggerSource) -> bytes:
    return struct.pack(
        "<BBBHHB",
        int(kind),
        code,
        modifiers,
        source.vendor_id,
        source.product_id,
        source.interface_number,
    )


def _switch(
    code: int, *, kind=TriggerKind.MOUSE_BUTTON, action=None, source=None
) -> Binding:
    return Binding(
        trigger=Trigger(kind, code, 0, source),
        mode=BindingMode.REPLACE,
        action=action or Action(ActionKind.TOGGLE_MOUSE_ROUTE, 0),
    )


@pytest.fixture
def page(qtbot) -> MouseSwitchPage:
    page = MouseSwitchPage()
    qtbot.addWidget(page)
    page.set_session(ProjectSession.new())
    page.set_capabilities(MouseCapabilities(advertised=True))
    return page


# ------------------------------------------------------------ the trigger first


def test_nothing_can_be_bound_before_a_trigger_kind_is_chosen(page):
    assert page.trigger_kind.currentData() is None
    assert page.key_combo.isEnabled() is False
    assert page.mouse_combo.isEnabled() is False
    assert page.action_combo.isEnabled() is False
    assert page.mode_combo.isEnabled() is False
    assert page.apply_button.isEnabled() is False


def test_choosing_a_keyboard_trigger_opens_the_rest_of_the_page(page):
    page.select_trigger_kind(TriggerKind.KEYBOARD_USAGE)

    assert page.key_combo.isEnabled() is True
    assert page.mouse_combo.isEnabled() is False
    assert page.action_combo.isEnabled() is True
    assert page.mode_combo.isEnabled() is True


def test_choosing_a_mouse_trigger_opens_the_button_chooser(page):
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)

    assert page.key_combo.isEnabled() is False
    assert page.mouse_combo.isEnabled() is True
    assert [page.mouse_combo.itemData(row) for row in range(page.mouse_combo.count())] == list(
        STANDARD_MOUSE_BUTTONS
    )


# --------------------------------------------------------------------- actions


def test_toggle_asks_for_the_toggle_action(page, qtbot):
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)
    page.mouse_combo.setCurrentIndex(page.mouse_combo.findData(3))
    page.select_action(ActionKind.TOGGLE_MOUSE_ROUTE, 0)

    with qtbot.waitSignal(page.command_requested) as blocker:
        page.apply_button.click()

    command = blocker.args[0]
    assert isinstance(command, AddBinding)
    assert command.binding.trigger == Trigger(TriggerKind.MOUSE_BUTTON, 3, 0)
    assert command.binding.action == Action(ActionKind.TOGGLE_MOUSE_ROUTE, 0)


def test_pc2_asks_for_a_fixed_route(page, qtbot):
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)
    page.mouse_combo.setCurrentIndex(page.mouse_combo.findData(2))
    page.select_action(ActionKind.SET_MOUSE_ROUTE, MouseRoute.PC2)

    with qtbot.waitSignal(page.command_requested) as blocker:
        page.apply_button.click()

    assert blocker.args[0].binding.action == Action(ActionKind.SET_MOUSE_ROUTE, MouseRoute.PC2)


def test_the_page_offers_exactly_toggle_pc1_and_pc2(page):
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)

    offered = [page.action_combo.itemData(row) for row in range(page.action_combo.count())]

    assert offered == [
        Action(ActionKind.TOGGLE_MOUSE_ROUTE, 0),
        Action(ActionKind.SET_MOUSE_ROUTE, int(MouseRoute.PC1)),
        Action(ActionKind.SET_MOUSE_ROUTE, int(MouseRoute.PC2)),
    ]


def test_add_mode_reaches_the_command(page, qtbot):
    page.select_trigger_kind(TriggerKind.KEYBOARD_USAGE)
    page.key_combo.setCurrentIndex(page.key_combo.findData(0x3E))
    page.mode_combo.setCurrentIndex(page.mode_combo.findData(BindingMode.ADD))

    with qtbot.waitSignal(page.command_requested) as blocker:
        page.apply_button.click()

    assert blocker.args[0].binding.mode is BindingMode.ADD
    assert blocker.args[0].binding.trigger.code == 0x3E


def test_a_keyboard_trigger_may_carry_modifiers(page, qtbot):
    page.select_trigger_kind(TriggerKind.KEYBOARD_USAGE)
    page.key_combo.setCurrentIndex(page.key_combo.findData(0x3E))
    page.modifier_boxes["ctrl"].setChecked(True)

    with qtbot.waitSignal(page.command_requested) as blocker:
        page.apply_button.click()

    assert blocker.args[0].binding.trigger.modifiers == LEFT_CTRL


# ---------------------------------------------------------------- capabilities


def test_without_a_mouse_no_mouse_trigger_can_be_used(qtbot):
    page = MouseSwitchPage()
    qtbot.addWidget(page)
    page.set_session(ProjectSession.new())

    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)

    assert page.mouse_combo.count() == 0
    assert page.apply_button.isEnabled() is False
    assert page.warning_label.text()


def test_button_four_appears_only_after_the_device_reports_it(page):
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)
    assert page.mouse_combo.findData(4) < 0

    page.set_capabilities(MouseCapabilities(advertised=True).observing(4))

    assert page.mouse_combo.findData(4) >= 0


def test_mouse_buttons_have_names_a_person_can_recognise(page):
    page.set_capabilities(
        MouseCapabilities(advertised=True).observing(4).observing(5)
    )
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)

    assert [
        page.mouse_combo.itemText(row) for row in range(page.mouse_combo.count())
    ] == [
        "Left button",
        "Right button",
        "Middle button (wheel)",
        "Side button 1",
        "Side button 2",
    ]


# -------------------------------------------------------------------- capture


def test_detecting_a_press_needs_the_device_and_a_chosen_trigger(qtbot):
    """The kind is not the gate any more.

    A press detected on the mouse may arrive as a keyboard usage, and the page
    selects that kind when it does. Gating Detect on "mouse button" meant the
    very capture this page now exists to allow switched the button off behind
    the operator who wanted to try another key.
    """
    service = DeviceService(timeout_ms=5000)
    page = MouseSwitchPage(service)
    qtbot.addWidget(page)

    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)
    assert page.capture_button.isEnabled() is False

    emulator = U1Emulator()
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(SynchronousTransportLink(emulator))
    page.set_capabilities(MouseCapabilities(advertised=True))
    assert page.capture_button.isEnabled() is True

    page.select_trigger_kind(TriggerKind.KEYBOARD_USAGE)
    assert page.capture_button.isEnabled() is True

    page.select_trigger_kind(None)
    assert page.capture_button.isEnabled() is False


def test_a_detected_side_button_is_selected(qtbot):
    service = DeviceService(timeout_ms=5000)
    emulator = U1Emulator()
    link = SynchronousTransportLink(emulator)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(link)
    page = MouseSwitchPage(service)
    qtbot.addWidget(page)
    page.set_capabilities(MouseCapabilities(advertised=True))
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)

    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        dialog = page.capture_mouse_button()
    assert isinstance(dialog, CaptureDialog)
    assert emulator.queue_capture_event(
        bytes((TriggerKind.MOUSE_BUTTON, 5, 0))
    )

    with qtbot.waitSignal(service.capture_received, timeout=5000):
        link.poll()

    assert page.mouse_combo.currentData() == 5
    assert page.mouse_combo.currentText() == "Side button 2"


def test_a_key_the_attached_mouse_sends_is_captured_and_selected(qtbot):
    """A mouse whose side button reports a keyboard usage is still the mouse.

    Driven through the real Detect button and a real capture payload, because
    a handler production never reaches is a handler a test must not reach
    either.
    """
    service = DeviceService(timeout_ms=5000)
    emulator = U1Emulator()
    link = SynchronousTransportLink(emulator)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(link)
    page = MouseSwitchPage(service)
    qtbot.addWidget(page)
    page.set_capabilities(
        MouseCapabilities(advertised=True).with_peripherals((_attached_mouse(),))
    )
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)

    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        QTest.mouseClick(page.capture_button, Qt.MouseButton.LeftButton)
    dialogs = page.findChildren(CaptureDialog)
    assert len(dialogs) == 1
    dialog = dialogs[0]
    assert emulator.queue_capture_event(
        _capture_payload(
            TriggerKind.KEYBOARD_USAGE, CAPTURED_KEY, LEFT_CTRL, CAPTURED_SOURCE
        )
    )

    with qtbot.waitSignal(service.capture_received, timeout=5000):
        link.poll()

    expected = Trigger(
        TriggerKind.KEYBOARD_USAGE, CAPTURED_KEY, LEFT_CTRL, CAPTURED_SOURCE
    )
    assert dialog.trigger == expected
    assert page.captured_trigger_summary.text() == "Additional mouse button"
    assert page.captured_trigger_summary.isVisibleTo(page) is True
    assert page.trigger_kind.isVisibleTo(page) is False
    assert page.key_combo.isVisibleTo(page) is False
    assert page.consumer_usage.isVisibleTo(page) is False
    assert page.mouse_combo.isVisibleTo(page) is False
    assert all(
        box.isVisibleTo(page) is False for box in page.modifier_boxes.values()
    )
    text_widgets = (
        page.findChildren(QLabel)
        + page.findChildren(QCheckBox)
        + page.findChildren(QPushButton)
    )
    visible_text = " ".join(
        widget.text() for widget in text_widgets if widget.isVisibleTo(page)
    )
    assert "Ctrl" not in visible_text
    assert "Right" not in visible_text
    assert "0x1" not in visible_text
    assert "3434:D030" not in visible_text
    assert "interface 2" not in visible_text
    assert page.current_trigger() == expected
    assert page.current_binding().trigger == expected
    assert page.capture_button.isEnabled() is True
    assert page.capture_button.isVisibleTo(page) is True
    assert page.action_combo.isEnabled() is True
    assert page.action_combo.isVisibleTo(page) is True
    assert page.mode_combo.isEnabled() is True
    assert page.mode_combo.isVisibleTo(page) is True
    assert page.apply_button.isEnabled() is True
    assert page.apply_button.isVisibleTo(page) is True
    assert page.warning_label.isVisibleTo(page) is True
    assert page.existing_list.isVisibleTo(page) is True


def test_a_mouse_origin_consumer_control_uses_the_same_friendly_summary(page):
    page.set_capabilities(
        MouseCapabilities(advertised=True).with_peripherals((_attached_mouse(),))
    )
    captured = Trigger(
        TriggerKind.CONSUMER_USAGE, 0xE9, 0, CAPTURED_SOURCE
    )

    page.apply_captured_trigger(captured)

    assert page.captured_trigger_summary.text() == "Additional mouse button"
    assert page.consumer_usage.isVisibleTo(page) is False
    assert page.current_trigger() == captured


def test_editing_a_friendly_capture_restores_the_manual_trigger_editor(qtbot, page):
    page.set_capabilities(
        MouseCapabilities(advertised=True).with_peripherals((_attached_mouse(),))
    )
    page.apply_captured_trigger(
        Trigger(TriggerKind.KEYBOARD_USAGE, CAPTURED_KEY, LEFT_CTRL, CAPTURED_SOURCE)
    )

    qtbot.mouseClick(page.edit_trigger_button, Qt.MouseButton.LeftButton)

    assert page.captured_trigger_summary.isVisibleTo(page) is False
    assert page.trigger_kind.isVisibleTo(page) is True
    assert page.key_combo.isVisibleTo(page) is True
    assert page.modifier_boxes["ctrl"].isVisibleTo(page) is True
    page.key_combo.setCurrentIndex(page.key_combo.findData(0x04))
    assert page.current_trigger() == Trigger(
        TriggerKind.KEYBOARD_USAGE, 0x04, LEFT_CTRL, None
    )


@pytest.mark.parametrize("kind, code, modifiers", [(1, 0x68, 0xF1), (3, 0x1B1, 0)])
def test_detect_apply_preserves_extended_key_and_all_modifiers_and_clears_provenance(qtbot, kind, code, modifiers):
    service = DeviceService(timeout_ms=5000)
    emulator = U1Emulator()
    link = SynchronousTransportLink(emulator)
    with qtbot.waitSignal(service.operation_succeeded):
        service.connect_device(link)
    page = MouseSwitchPage(service)
    qtbot.addWidget(page)
    page.set_capabilities(MouseCapabilities(advertised=True).with_peripherals((_attached_mouse(),)))
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)
    with qtbot.waitSignal(service.operation_succeeded):
        qtbot.mouseClick(page.capture_button, Qt.MouseButton.LeftButton)
    assert emulator.queue_capture_event(_capture_payload(kind, code & 0xFF, code >> 8 if kind == 3 else modifiers, CAPTURED_SOURCE))
    with qtbot.waitSignal(service.capture_received):
        link.poll()
    if kind == 3:
        assert page.consumer_usage.value() == code
    else:
        assert page.key_combo.currentData() == code
    page.select_action(ActionKind.SET_MOUSE_ROUTE, MouseRoute.PC2)
    with qtbot.waitSignal(page.command_requested) as changed:
        qtbot.mouseClick(page.apply_button, Qt.MouseButton.LeftButton)
    assert changed.args[0].binding.trigger == Trigger(TriggerKind(kind), code, modifiers, CAPTURED_SOURCE)
    if kind == 3:
        page.consumer_usage.setValue(0xE9)
        page.consumer_usage.setValue(code)
        assert page.current_trigger().source is None
        return
    page.key_combo.setCurrentIndex(page.key_combo.findData(0x4F))
    page.key_combo.setCurrentIndex(page.key_combo.findData(0x68))
    assert page.current_trigger().source is None
    page.apply_captured_trigger(Trigger(TriggerKind.KEYBOARD_USAGE, 0x68, 0xF1, CAPTURED_SOURCE))
    page.set_session(ProjectSession.new())
    assert page.current_trigger().source is None


def test_a_key_from_another_device_is_refused_and_the_window_stays_open(qtbot):
    """The page is about one mouse: a press on the keyboard is not its press."""
    service = DeviceService(timeout_ms=5000)
    emulator = U1Emulator()
    link = SynchronousTransportLink(emulator)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(link)
    page = MouseSwitchPage(service)
    qtbot.addWidget(page)
    page.set_capabilities(
        MouseCapabilities(advertised=True).with_peripherals((_attached_mouse(),))
    )
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)

    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        QTest.mouseClick(page.capture_button, Qt.MouseButton.LeftButton)
    dialog = page.findChildren(CaptureDialog)[0]
    assert emulator.queue_capture_event(
        _capture_payload(
            TriggerKind.KEYBOARD_USAGE,
            CAPTURED_KEY,
            LEFT_CTRL,
            TriggerSource(0x1234, 0x5678, 0),
        )
    )

    with qtbot.waitSignal(service.capture_received, timeout=5000):
        link.poll()

    assert dialog.trigger is None
    assert page.trigger_kind.currentData() is TriggerKind.MOUSE_BUTTON
    assert "received=1 decision=filtered" in service.capture_observation
    assert "34 12 78 56 00" in service.capture_observation


def test_a_key_the_operator_then_changes_loses_the_device_it_named(qtbot):
    """The source describes the press that was captured, not the editor."""
    service = DeviceService(timeout_ms=5000)
    emulator = U1Emulator()
    link = SynchronousTransportLink(emulator)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(link)
    page = MouseSwitchPage(service)
    qtbot.addWidget(page)
    page.set_capabilities(
        MouseCapabilities(advertised=True).with_peripherals((_attached_mouse(),))
    )
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        QTest.mouseClick(page.capture_button, Qt.MouseButton.LeftButton)
    assert emulator.queue_capture_event(
        _capture_payload(
            TriggerKind.KEYBOARD_USAGE, CAPTURED_KEY, LEFT_CTRL, CAPTURED_SOURCE
        )
    )
    with qtbot.waitSignal(service.capture_received, timeout=5000):
        link.poll()

    page.key_combo.setCurrentIndex(page.key_combo.findData(0x04))

    assert page.current_trigger() == Trigger(
        TriggerKind.KEYBOARD_USAGE, 0x04, LEFT_CTRL, None
    )


def test_detect_control_describes_button_or_key_capture(page):
    assert "button or key" in page.capture_button.text().lower()
    assert "button or key" in page.capture_button.accessibleName().lower()


def test_detect_can_be_pressed_again_after_a_key_was_detected(qtbot):
    """The operator who detected one key must be able to try another."""
    service = DeviceService(timeout_ms=5000)
    emulator = U1Emulator()
    link = SynchronousTransportLink(emulator)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(link)
    page = MouseSwitchPage(service)
    qtbot.addWidget(page)
    page.set_capabilities(
        MouseCapabilities(advertised=True).with_peripherals((_attached_mouse(),))
    )
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        QTest.mouseClick(page.capture_button, Qt.MouseButton.LeftButton)
    assert emulator.queue_capture_event(
        _capture_payload(
            TriggerKind.KEYBOARD_USAGE, CAPTURED_KEY, LEFT_CTRL, CAPTURED_SOURCE
        )
    )
    with qtbot.waitSignal(service.capture_received, timeout=5000):
        link.poll()
    assert page.trigger_kind.currentData() is TriggerKind.KEYBOARD_USAGE

    assert page.capture_button.isEnabled() is True
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        QTest.mouseClick(page.capture_button, Qt.MouseButton.LeftButton)

    dialogs = page.findChildren(CaptureDialog)
    assert len(dialogs) == 2
    assert dialogs[-1].isVisible() is True


def test_a_mouse_button_from_another_device_is_refused(qtbot):
    """A button is not this page's press just because it is a button.

    Only a press with no source at all is taken on trust - that is what
    firmware predating the source table sends. One that names a device is
    judged on the device, whatever kind of press it is.
    """
    service = DeviceService(timeout_ms=5000)
    emulator = U1Emulator()
    link = SynchronousTransportLink(emulator)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(link)
    page = MouseSwitchPage(service)
    qtbot.addWidget(page)
    page.set_capabilities(
        MouseCapabilities(advertised=True).with_peripherals((_attached_mouse(),))
    )
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        QTest.mouseClick(page.capture_button, Qt.MouseButton.LeftButton)
    dialog = page.findChildren(CaptureDialog)[0]
    assert emulator.queue_capture_event(
        _capture_payload(
            TriggerKind.MOUSE_BUTTON, 5, 0, TriggerSource(0x1234, 0x5678, 0)
        )
    )

    with qtbot.waitSignal(service.capture_received, timeout=5000):
        link.poll()

    assert dialog.trigger is None
    assert page.mouse_combo.findData(5) < 0


def test_a_mouse_button_that_names_no_device_is_still_accepted(qtbot):
    """Firmware predating the source table must keep working exactly as before."""
    service = DeviceService(timeout_ms=5000)
    emulator = U1Emulator()
    link = SynchronousTransportLink(emulator)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(link)
    page = MouseSwitchPage(service)
    qtbot.addWidget(page)
    page.set_capabilities(MouseCapabilities(advertised=True))
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        QTest.mouseClick(page.capture_button, Qt.MouseButton.LeftButton)
    assert emulator.queue_capture_event(bytes((TriggerKind.MOUSE_BUTTON, 5, 0)))

    with qtbot.waitSignal(service.capture_received, timeout=5000):
        link.poll()

    assert page.mouse_combo.currentData() == 5
    assert page.current_trigger() == Trigger(TriggerKind.MOUSE_BUTTON, 5, 0)


def test_binding_a_new_button_replaces_the_old_mouse_switch(page):
    old = _switch(4)
    page.set_session(page.session.apply(AddBinding(1, old)))
    page.apply_captured_trigger(Trigger(TriggerKind.MOUSE_BUTTON, 5, 0))
    commands = []
    page.command_requested.connect(commands.append)

    page.apply_button.click()

    assert len(commands) == 1
    assert isinstance(commands[0], UpdateBinding)
    assert commands[0].uuid == old.uuid
    assert commands[0].binding.trigger == Trigger(
        TriggerKind.MOUSE_BUTTON, 5, 0
    )


# ------------------------------------------------------ existing switch bindings


def test_the_page_lists_the_switch_bindings_of_the_active_profile(page):
    page.set_session(page.session.apply(AddBinding(1, _switch(3))))

    assert page.existing_list.count() == 1
    assert "Middle button (wheel)" in page.existing_list.item(0).text()


def test_a_listed_mouse_origin_key_is_presented_as_an_additional_mouse_button(page):
    page.set_session(
        page.session.apply(
            AddBinding(
                1,
                _switch(
                    CAPTURED_KEY,
                    kind=TriggerKind.KEYBOARD_USAGE,
                    source=CAPTURED_SOURCE,
                ),
            )
        )
    )
    page.set_capabilities(
        MouseCapabilities(advertised=True).with_peripherals((_attached_mouse(),))
    )

    label = page.existing_list.item(0).text()
    assert label.startswith("Additional mouse button ->")
    assert "Right" not in label
    assert "Ctrl" not in label
    assert "3434:D030" not in label
    assert "interface 2" not in label


@pytest.mark.parametrize(
    "button, expected",
    (
        (1, "Left button"),
        (2, "Right button"),
        (3, "Middle button (wheel)"),
        (4, "Side button 1"),
        (5, "Side button 2"),
        (6, "Button 6"),
    ),
)
def test_listed_standard_mouse_buttons_keep_their_exact_names_without_source(
    page, button, expected
):
    page.set_capabilities(MouseCapabilities(advertised=True).observing(4).observing(5))
    page.set_session(
        page.session.apply(AddBinding(1, _switch(button, source=CAPTURED_SOURCE)))
    )

    label = page.existing_list.item(0).text()
    assert label.startswith(f"{expected} ->")
    assert "3434:D030" not in label
    assert "interface 2" not in label


def test_a_non_mouse_keyboard_trigger_is_not_relabelled_or_source_qualified(page):
    keyboard_source = TriggerSource(0x1234, 0x5678, 0)
    page.set_session(
        page.session.apply(
            AddBinding(
                1,
                _switch(
                    CAPTURED_KEY,
                    kind=TriggerKind.KEYBOARD_USAGE,
                    source=keyboard_source,
                ),
            )
        )
    )
    page.set_capabilities(
        MouseCapabilities(advertised=True).with_peripherals((_attached_mouse(),))
    )

    label = page.existing_list.item(0).text()
    assert label.startswith("Right ->")
    assert "Additional mouse button" not in label
    assert "1234:5678" not in label


def test_a_binding_that_is_not_about_the_mouse_route_is_not_listed(page):
    other = Binding(
        trigger=Trigger(TriggerKind.KEYBOARD_USAGE, 0x04, 0),
        mode=BindingMode.REPLACE,
        action=Action(ActionKind.TOGGLE_KEYBOARD_ROUTE, 0),
    )
    page.set_session(page.session.apply(AddBinding(1, other)))

    assert page.existing_list.count() == 0


def test_a_button_the_current_mouse_lacks_is_reported_as_unavailable(page):
    page.set_session(page.session.apply(AddBinding(1, _switch(4))))

    assert "Side button 1" in page.warning_label.text()


def test_the_warning_clears_once_the_device_reports_that_button(page):
    page.set_session(page.session.apply(AddBinding(1, _switch(4))))
    assert page.warning_label.text()

    page.set_capabilities(MouseCapabilities(advertised=True).observing(4))

    assert page.warning_label.text() == ""


def test_a_keyboard_switch_binding_is_never_reported_as_unavailable(page):
    page.set_session(
        page.session.apply(AddBinding(1, _switch(0x3E, kind=TriggerKind.KEYBOARD_USAGE)))
    )

    assert page.warning_label.text() == ""


# ------------------------------------------------------------------- conflicts


def test_an_existing_mouse_switch_can_be_replaced(page):
    old = _switch(3)
    page.set_session(page.session.apply(AddBinding(1, old)))
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)
    page.mouse_combo.setCurrentIndex(page.mouse_combo.findData(3))
    commands = []
    page.command_requested.connect(commands.append)

    assert page.apply_button.isEnabled() is True
    page.apply_button.click()
    assert isinstance(commands[0], UpdateBinding)
    assert commands[0].uuid == old.uuid


def test_the_page_never_edits_the_session_itself(page):
    before = page.session
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)
    page.mouse_combo.setCurrentIndex(page.mouse_combo.findData(3))

    page.apply_button.click()

    assert page.session is before


def test_every_control_carries_an_accessible_name(page):
    for widget in (
        page.trigger_kind,
        page.key_combo,
        page.mouse_combo,
        page.capture_button,
        page.action_combo,
        page.mode_combo,
        page.apply_button,
        page.existing_list,
    ):
        assert widget.accessibleName()


def test_the_page_uses_the_shared_visual_hierarchy(page):
    titles = [
        label.text()
        for label in page.findChildren(QLabel)
        if label.property("role") == theme.ROLE_PAGE_TITLE
    ]

    assert titles == ["Mouse"]
    assert page.warning_label.property("role") == theme.ROLE_BANNER
    assert page.apply_button.property("role") == theme.ROLE_PRIMARY


def test_a_conflict_is_shown_as_an_error_not_a_warning(page):
    other = Binding(
        trigger=Trigger(TriggerKind.KEYBOARD_USAGE, 0x04, 0),
        mode=BindingMode.REPLACE,
        action=Action(ActionKind.TOGGLE_KEYBOARD_ROUTE, 0),
    )
    page.set_session(
        page.session.apply(
            AddBinding(
                page.session.project.active_profile_id,
                other,
            )
        )
    )
    page.select_trigger_kind(TriggerKind.KEYBOARD_USAGE)

    assert page.warning_label.text()
    assert page.warning_label.property("signal") == theme.SIGNAL_ERROR
