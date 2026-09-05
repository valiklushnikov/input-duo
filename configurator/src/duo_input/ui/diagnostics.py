"""The Diagnostics page: what the device reports, and how to send it on.

The export checkbox is off by default and stays unavailable until the project
has actually been saved somewhere, so including the operator's macro text is a
deliberate act naming a specific file rather than a box that happened to be
ticked.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QT_TRANSLATE_NOOP, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from duo_input.device.service import DeviceService
from duo_input.persistence.diagnostic_export import (
    UNKNOWN,
    DiagnosticSnapshot,
    build_diagnostic_zip,
)
from duo_input.ui.models.project_session import ProjectSession
from duo_input.ui.theme import (
    ROLE_BANNER,
    ROLE_MONO,
    SIGNAL_WARN,
    SPACE_LG,
    SPACE_MD,
    SPACE_SM,
    fact_form,
    field_label,
    mark_placeholder,
    monospace_font,
    page_header,
    set_role,
    set_signal,
)

ARCHIVE_FILTER = "Diagnostic report (*.zip)"

# The field table below spells out QT_TRANSLATE_NOOP("DiagnosticsPage", ...)
# for every label. lupdate reads those literals, and the widget calls tr() on
# them at runtime, so a language change retranslates the whole page.


class DiagnosticsPage(QWidget):
    """Device counters and versions, plus the privacy-safe export."""

    #: Field key to the label beside it. Keys are stable identifiers.
    _FIELDS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
        (
            QT_TRANSLATE_NOOP("DiagnosticsPage", "Versions"),
            (
                ("application_version", QT_TRANSLATE_NOOP("DiagnosticsPage", "Configurator")),
                ("protocol_version", QT_TRANSLATE_NOOP("DiagnosticsPage", "CDC protocol")),
                ("chip_id", QT_TRANSLATE_NOOP("DiagnosticsPage", "Chip ID")),
            ),
        ),
        (
            QT_TRANSLATE_NOOP("DiagnosticsPage", "Health"),
            (
                ("reset_reason", QT_TRANSLATE_NOOP("DiagnosticsPage", "Reset reason")),
                ("watchdog_count", QT_TRANSLATE_NOOP("DiagnosticsPage", "Watchdog resets")),
                ("endpoint_answering", QT_TRANSLATE_NOOP("DiagnosticsPage", "Second board answering")),
                ("endpoint_usb", QT_TRANSLATE_NOOP("DiagnosticsPage", "Second board USB")),
                ("spi_frames_sent", QT_TRANSLATE_NOOP("DiagnosticsPage", "SPI frames sent")),
                ("spi_crc_errors", QT_TRANSLATE_NOOP("DiagnosticsPage", "SPI CRC errors")),
                ("spi_echoed_frames", QT_TRANSLATE_NOOP("DiagnosticsPage", "SPI frames echoed back")),
                ("spi_timeouts", QT_TRANSLATE_NOOP("DiagnosticsPage", "SPI timeouts")),
                ("endpoint_drops", QT_TRANSLATE_NOOP("DiagnosticsPage", "Link drops seen by U2")),
                ("endpoint_release_ms", QT_TRANSLATE_NOOP("DiagnosticsPage", "U2 released after (ms)")),
                ("dropped_commands", QT_TRANSLATE_NOOP("DiagnosticsPage", "Input commands never delivered")),
            ),
        ),
        (
            QT_TRANSLATE_NOOP("DiagnosticsPage", "CDC counters"),
            (
                ("cdc_bad_crc", QT_TRANSLATE_NOOP("DiagnosticsPage", "Bad CRC")),
                ("cdc_bad_sequence", QT_TRANSLATE_NOOP("DiagnosticsPage", "Bad sequence")),
                ("cdc_timeout", QT_TRANSLATE_NOOP("DiagnosticsPage", "Timeouts")),
                ("cdc_disconnect", QT_TRANSLATE_NOOP("DiagnosticsPage", "Disconnects")),
                ("cdc_aborted_staging", QT_TRANSLATE_NOOP("DiagnosticsPage", "Aborted writes")),
            ),
        ),
        (
            QT_TRANSLATE_NOOP("DiagnosticsPage", "Configuration"),
            (
                ("device_generation", QT_TRANSLATE_NOOP("DiagnosticsPage", "Generation on device")),
                ("device_hash", QT_TRANSLATE_NOOP("DiagnosticsPage", "Hash on device")),
                ("advertised_capabilities", QT_TRANSLATE_NOOP("DiagnosticsPage", "Advertised capabilities")),
                # Backend-neutral, and named only here. "CH375 state" was a row
                # label that is wrong on half the builds this configurator
                # talks to; which host stack is running is a value, reported in
                # the one place that exists to report it - never in a mapping
                # screen, where an operator is choosing keys and the backend
                # cannot change what any of them do.
                ("input_backend", QT_TRANSLATE_NOOP("DiagnosticsPage", "Input backend")),
                ("input_backend_counters", QT_TRANSLATE_NOOP("DiagnosticsPage", "Input backend counters")),
                ("peripherals", QT_TRANSLATE_NOOP("DiagnosticsPage", "Peripherals")),
            ),
        ),
    )

    def __init__(self, service: DeviceService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._session = ProjectSession.new()
        self._values: dict[str, QLabel] = {}

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget(scroll)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_LG)
        layout.setSpacing(SPACE_LG)
        layout.addWidget(
            page_header(
                self.tr("Diagnostics"),
                self.tr(
                    "What the device reports about itself, and how to send it on."
                ),
                body,
            )
        )
        layout.addLayout(self._build_cards(body), 1)
        scroll.setWidget(body)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(scroll, 1)
        outer.addLayout(self._build_actions())

        # The page follows the link itself, so it is correct even when it is
        # not the page the operator happens to be looking at.
        self._service.state_changed.connect(lambda _state: self.refresh())
        self.refresh()

    # ---------------------------------------------------------------- layout

    def _build_cards(self, parent: QWidget) -> QHBoxLayout:
        """Two columns, because Health alone is as tall as the other three.

        Stacked in one column the page was two screens of scrolling for facts
        that are read together; side by side the versions, the configuration
        and the link counters fit beside the health table on one screen.
        """
        columns = QHBoxLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(SPACE_MD)

        left, right = QVBoxLayout(), QVBoxLayout()
        for side in (left, right):
            side.setContentsMargins(0, 0, 0, 0)
            side.setSpacing(SPACE_MD)
        tallest = max(self._FIELDS, key=lambda entry: len(entry[1]))
        for title, rows in self._FIELDS:
            side = right if (title, rows) == tallest else left
            side.addWidget(self._card(parent, title, rows))
        left.addStretch(1)
        right.addStretch(1)
        columns.addLayout(left, 1)
        columns.addLayout(right, 1)
        return columns

    def _card(self, parent: QWidget, title: str, rows: tuple[tuple[str, str], ...]) -> QGroupBox:
        box = QGroupBox(self.tr(title), parent)
        form = fact_form()
        box.setLayout(form)
        for key, label in rows:
            # Every value on this page is a version, a count or an identifier,
            # so all of them are set where a column of digits lines up.
            value = QLabel(UNKNOWN, box)
            value.setAccessibleName(self.tr(label))
            value.setWordWrap(True)
            value.setFont(monospace_font())
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
            self._values[key] = value
            form.addRow(field_label(self.tr(label) + ":", box), value)
        return box

    def _build_actions(self) -> QVBoxLayout:
        outer = QVBoxLayout()
        outer.setContentsMargins(SPACE_LG, 0, SPACE_LG, SPACE_LG)
        outer.setSpacing(SPACE_SM)

        # A checkbox does not wrap, so the reason to think twice lives beside
        # it in a label that does.
        self.privacy_label = QLabel(
            self.tr(
                "A report never contains your macro text. Including the saved "
                "project adds that file, and everything you typed into it."
            ),
            self,
        )
        self.privacy_label.setWordWrap(True)
        set_role(self.privacy_label, ROLE_BANNER)
        outer.addWidget(self.privacy_label)

        row = QHBoxLayout()
        row.setSpacing(SPACE_SM)

        self.refresh_button = QPushButton(self.tr("Refresh"), self)
        self.refresh_button.setAccessibleName(self.tr("Ask the device for its counters"))
        self.refresh_button.clicked.connect(self.request_counters)

        self.include_config_box = QCheckBox(self.tr("Include the saved project"), self)
        self.include_config_box.setAccessibleName(
            self.tr("Include the saved project in the report")
        )
        self.include_config_box.toggled.connect(self._refresh_privacy_note)

        self.export_button = QPushButton(self.tr("Export report..."), self)
        self.export_button.setAccessibleName(self.tr("Save a diagnostic report"))
        self.export_button.clicked.connect(self._on_export_clicked)

        row.addWidget(self.refresh_button)
        row.addWidget(self.include_config_box, 1)
        row.addWidget(self.export_button)
        outer.addLayout(row)
        return outer

    # ----------------------------------------------------------------- state

    @property
    def service(self) -> DeviceService:
        return self._service

    @property
    def session(self) -> ProjectSession:
        return self._session

    def set_session(self, session: ProjectSession) -> None:
        self._session = session
        self.refresh()

    def snapshot(self) -> DiagnosticSnapshot:
        return DiagnosticSnapshot.from_service(self._service)

    def value(self, key: str) -> str:
        """Current text of one field, by its stable key."""
        return self._values[key].text()

    def field_keys(self) -> tuple[str, ...]:
        """Every field this page reports, in the order it lists them."""
        return tuple(self._values)

    def role(self, key: str) -> str:
        """How one field currently reads: a counter, or the absence of one."""
        return str(self._values[key].property("role") or "")

    def refresh(self) -> None:
        """Repaint from whatever the service already knows."""
        snapshot = self.snapshot()
        for key, label in self._values.items():
            text = _as_text(getattr(snapshot, key, UNKNOWN))
            label.setText(text)
            # Nothing here is invented: a counter the device never sent looks
            # like an empty slot rather than like a reading of zero.
            mark_placeholder(label, text == UNKNOWN, ROLE_MONO)
        self.refresh_button.setEnabled(self._service.is_connected)
        self.include_config_box.setEnabled(self._session.path is not None)
        if self._session.path is None:
            self.include_config_box.setChecked(False)
        self._refresh_privacy_note()

    def _refresh_privacy_note(self, *_args: object) -> None:
        """The note only shouts once it describes what is about to happen."""
        set_signal(
            self.privacy_label,
            SIGNAL_WARN if self.include_config_box.isChecked() else None,
        )

    def request_counters(self) -> None:
        if self._service.is_connected:
            self._service.get_diagnostics()

    # ---------------------------------------------------------------- export

    def export_to(self, destination: str | Path) -> Path:
        """Write the report to ``destination`` and return where it landed."""
        return build_diagnostic_zip(
            destination,
            self.snapshot(),
            project=self._session,
            include_config=self.include_config_box.isChecked(),
        )

    def _on_export_clicked(self) -> None:
        name, _ = QFileDialog.getSaveFileName(
            self, self.tr("Export diagnostic report"), "duo-input-report.zip", ARCHIVE_FILTER
        )
        if not name:
            return
        try:
            written = self.export_to(name)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, self.tr("Export failed"), str(error))
            return
        QMessageBox.information(
            self, self.tr("Report saved"), self.tr("Saved to {0}").format(written)
        )


def _as_text(value: object) -> str:
    if isinstance(value, dict):
        # Only the counters that counted something. Twelve zeros in a row is
        # noise an operator has to read past to find the one that is not zero,
        # and a backend that publishes none of them sends an empty mapping -
        # which is an absence, not a row of zeros.
        counted = ", ".join(f"{name} {count}" for name, count in value.items() if count)
        if not value:
            return UNKNOWN
        return counted or "none counted"
    if isinstance(value, (tuple, list)):
        return ", ".join(str(item) for item in value) if value else UNKNOWN
    return str(value)


__all__ = ["ARCHIVE_FILTER", "DiagnosticsPage"]
