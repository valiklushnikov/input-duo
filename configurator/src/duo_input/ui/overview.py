"""The Overview page.

Every value on this page is either read from :class:`DeviceService` or derived
from the project in memory. Nothing is invented: where protocol v1 simply does
not carry a field - the U1 and U2 *firmware* versions, the size of the
configuration the device is holding - the page says ``unknown`` and keeps
saying it while connected, rather than showing a plausible-looking number.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QGridLayout,
    QLabel,
    QListWidget,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from duo_input.device.service import DeviceService
from duo_input.generated.protocol import (
    BINARY_CONFIG_MAX_BYTES,
    Capability,
)
from duo_input.ui.models.project_session import ProjectSession

#: Shown wherever the device or the project genuinely does not supply a value.
UNKNOWN = "unknown"
IN_SYNC = "in sync"
OUT_OF_SYNC = "out of sync"

MAX_EVENTS = 50


class OverviewPage(QWidget):
    """Read-only summary of the device, the active profile and the three hashes."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: dict[str, QLabel] = {}

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget(scroll)
        grid = QGridLayout(body)
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setSpacing(12)

        grid.addWidget(
            self._card(
                self.tr("Device"),
                (
                    ("u1_protocol_version", self.tr("U1 protocol version")),
                    ("u1_firmware_version", self.tr("U1 firmware version")),
                    ("u2_firmware_version", self.tr("U2 firmware version")),
                    ("device_generation", self.tr("Configuration generation")),
                    ("device_active_profile", self.tr("Profile active on device")),
                ),
            ),
            0,
            0,
        )
        grid.addWidget(
            self._card(
                self.tr("Peripherals"),
                (
                    ("peripheral_keyboard", self.tr("Keyboard HID")),
                    ("peripheral_mouse", self.tr("Mouse HID")),
                    ("peripheral_consumer", self.tr("Consumer HID")),
                ),
            ),
            0,
            1,
        )
        grid.addWidget(
            self._card(
                self.tr("Active profile and routes"),
                (
                    ("active_profile", self.tr("Active profile")),
                    ("keyboard_route", self.tr("Keyboard route")),
                    ("mouse_route", self.tr("Mouse route")),
                    ("text_layout", self.tr("Text layout")),
                ),
            ),
            1,
            0,
        )
        grid.addWidget(
            self._card(
                self.tr("Memory usage"),
                (
                    ("project_config_size", self.tr("Project package")),
                    ("device_config_size", self.tr("Package on device")),
                ),
            ),
            1,
            1,
        )
        grid.addWidget(
            self._card(
                self.tr("Configuration state"),
                (
                    ("file_hash", self.tr("Saved file")),
                    ("compiled_hash", self.tr("Compiled project")),
                    ("device_hash", self.tr("On device")),
                    ("device_sync", self.tr("Device")),
                ),
            ),
            2,
            0,
            1,
            2,
        )

        events_box = QGroupBox(self.tr("Recent events"), body)
        events_layout = QVBoxLayout(events_box)
        self._events = QListWidget(events_box)
        self._events.setAccessibleName(self.tr("Recent device events"))
        self._events.setMinimumHeight(90)
        events_layout.addWidget(self._events)
        grid.addWidget(events_box, 3, 0, 1, 2)
        grid.setRowStretch(3, 1)

        scroll.setWidget(body)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self.clear()

    # --------------------------------------------------------------- building

    def _card(self, title: str, rows: tuple[tuple[str, str], ...]) -> QGroupBox:
        box = QGroupBox(title, self)
        box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        form = QFormLayout(box)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        for key, label in rows:
            value = QLabel(UNKNOWN, box)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
            value.setAccessibleName(label)
            value.setWordWrap(True)
            self._values[key] = value
            form.addRow(QLabel(label + ":", box), value)
        return box

    # ----------------------------------------------------------------- access

    def value(self, key: str) -> str:
        """Current text of one field, by its stable key."""
        return self._values[key].text()

    def events(self) -> tuple[str, ...]:
        """Recent events, newest first."""
        return tuple(self._events.item(row).text() for row in range(self._events.count()))

    def clear(self) -> None:
        for label in self._values.values():
            label.setText(UNKNOWN)

    def append_event(self, subject: str, detail: str = "") -> None:
        """Record one event. ``subject``/``detail`` are protocol identifiers."""
        text = f"{subject}: {detail}" if detail else subject
        self._events.insertItem(0, text)
        while self._events.count() > MAX_EVENTS:
            self._events.takeItem(self._events.count() - 1)

    # ---------------------------------------------------------------- refresh

    def update_from(self, session: ProjectSession, service: DeviceService | None) -> None:
        self._update_device(service)
        self._update_project(session)

    def _update_device(self, service: DeviceService | None) -> None:
        info = service.device_info if service is not None else None
        status = service.status if service is not None else None
        connected = bool(service is not None and service.is_connected and info is not None)

        # protocol v1 reports the CDC protocol version, and nothing else about
        # the firmware of either microcontroller.
        self._set(
            "u1_protocol_version",
            f"{info.protocol_major}.{info.protocol_minor}" if connected else UNKNOWN,
        )
        self._set("u1_firmware_version", UNKNOWN)
        self._set("u2_firmware_version", UNKNOWN)
        self._set("device_generation", str(info.active_generation) if connected else UNKNOWN)

        active = status.active_profile if connected and status is not None else None
        if active is None and connected:
            active = info.active_profile
        self._set("device_active_profile", str(active) if active is not None else UNKNOWN)

        for key, capability in (
            ("peripheral_keyboard", Capability.KEYBOARD_HID),
            ("peripheral_mouse", Capability.MOUSE_HID),
            ("peripheral_consumer", Capability.CONSUMER_HID),
        ):
            if not connected:
                self._set(key, UNKNOWN)
            elif info.capabilities & int(capability):
                self._set(key, self.tr("advertised"))
            else:
                self._set(key, self.tr("not advertised"))

        # The device never reports how large its stored package is outside a
        # read transaction, so this stays unknown instead of guessing.
        self._set("device_config_size", UNKNOWN)

    def _update_project(self, session: ProjectSession) -> None:
        profile = session.active_profile
        self._set("active_profile", f"{profile.id} - {profile.name}")
        self._set("keyboard_route", profile.keyboard_route.name)
        self._set("mouse_route", profile.mouse_route.name)
        self._set("text_layout", profile.text_layout.name)

        size = session.compiled_size
        if size is None:
            self._set("project_config_size", UNKNOWN)
        else:
            percent = size * 100.0 / BINARY_CONFIG_MAX_BYTES
            self._set(
                "project_config_size",
                self.tr("{0} of {1} bytes ({2:.1f}%)").format(
                    size, BINARY_CONFIG_MAX_BYTES, percent
                ),
            )

        self._set("file_hash", session.file_hash or UNKNOWN)
        self._set("compiled_hash", session.compiled_hash or UNKNOWN)
        self._set("device_hash", session.device_hash or UNKNOWN)
        if not session.device_hash:
            self._set("device_sync", UNKNOWN)
        else:
            self._set("device_sync", IN_SYNC if session.device_matches else OUT_OF_SYNC)

    def _set(self, key: str, text: str) -> None:
        self._values[key].setText(text)


__all__ = ["IN_SYNC", "MAX_EVENTS", "OUT_OF_SYNC", "UNKNOWN", "OverviewPage"]
