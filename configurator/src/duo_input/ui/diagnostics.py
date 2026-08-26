"""The Diagnostics page: what the device reports, and how to send it on.

The export checkbox is off by default and stays unavailable until the project
has actually been saved somewhere, so including the operator's macro text is a
deliberate act naming a specific file rather than a box that happened to be
ticked.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
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

ARCHIVE_FILTER = "Diagnostic report (*.zip)"


class DiagnosticsPage(QWidget):
    """Device counters and versions, plus the privacy-safe export."""

    #: Field key to the label beside it. Keys are stable identifiers.
    _FIELDS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
        (
            "Versions",
            (
                ("application_version", "Configurator"),
                ("protocol_version", "CDC protocol"),
                ("u1_firmware_version", "U1 firmware"),
                ("u2_firmware_version", "U2 firmware"),
                ("chip_id", "Chip ID"),
            ),
        ),
        (
            "Health",
            (
                ("reset_reason", "Reset reason"),
                ("watchdog_count", "Watchdog resets"),
                ("ch375_state", "CH375 state"),
                ("spi_crc_errors", "SPI CRC errors"),
                ("spi_timeouts", "SPI timeouts"),
            ),
        ),
        (
            "CDC counters",
            (
                ("cdc_bad_crc", "Bad CRC"),
                ("cdc_bad_sequence", "Bad sequence"),
                ("cdc_timeout", "Timeouts"),
                ("cdc_disconnect", "Disconnects"),
                ("cdc_aborted_staging", "Aborted writes"),
            ),
        ),
        (
            "Configuration",
            (
                ("device_generation", "Generation on device"),
                ("device_hash", "Hash on device"),
                ("advertised_capabilities", "Advertised capabilities"),
                ("peripherals", "Peripherals"),
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
        layout.setContentsMargins(12, 12, 12, 12)
        for title, rows in self._FIELDS:
            layout.addWidget(self._card(body, title, rows))
        layout.addStretch(1)
        scroll.setWidget(body)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll, 1)
        outer.addLayout(self._build_actions())

        # The page follows the link itself, so it is correct even when it is
        # not the page the operator happens to be looking at.
        self._service.state_changed.connect(lambda _state: self.refresh())
        self.refresh()

    # ---------------------------------------------------------------- layout

    def _card(self, parent: QWidget, title: str, rows: tuple[tuple[str, str], ...]) -> QGroupBox:
        box = QGroupBox(self.tr(title), parent)
        form = QFormLayout(box)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        for key, label in rows:
            value = QLabel(UNKNOWN, box)
            value.setAccessibleName(self.tr(label))
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
            self._values[key] = value
            form.addRow(QLabel(self.tr(label) + ":", box), value)
        return box

    def _build_actions(self) -> QVBoxLayout:
        outer = QVBoxLayout()
        outer.setContentsMargins(12, 0, 12, 12)

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
        outer.addWidget(self.privacy_label)

        row = QHBoxLayout()

        self.refresh_button = QPushButton(self.tr("Refresh"), self)
        self.refresh_button.setAccessibleName(self.tr("Ask the device for its counters"))
        self.refresh_button.clicked.connect(self.request_counters)

        self.include_config_box = QCheckBox(self.tr("Include the saved project"), self)
        self.include_config_box.setAccessibleName(
            self.tr("Include the saved project in the report")
        )

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

    def refresh(self) -> None:
        """Repaint from whatever the service already knows."""
        snapshot = self.snapshot()
        for key, label in self._values.items():
            label.setText(_as_text(getattr(snapshot, key, UNKNOWN)))
        self.refresh_button.setEnabled(self._service.is_connected)
        self.include_config_box.setEnabled(self._session.path is not None)
        if self._session.path is None:
            self.include_config_box.setChecked(False)

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
    if isinstance(value, (tuple, list)):
        return ", ".join(str(item) for item in value) if value else UNKNOWN
    return str(value)


__all__ = ["ARCHIVE_FILTER", "DiagnosticsPage"]
