"""The application shell: navigation, profile selector, save and write.

Editing is entirely local. The device is only ever changed by pressing Write,
which sends one transactional package through :class:`DeviceService`; the shell
merely mirrors whatever that service reports back.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from duo_input.device.service import DeviceService, DeviceState
from duo_input.domain.project_store import ProjectError
from duo_input.domain.text_compiler import compile_project_to_binary
from duo_input.domain.validation import ValidationIssue
from duo_input.ui.bindings import BindingsPage
from duo_input.ui.macros import MacrosPage
from duo_input.ui.models.binding_table import MouseCapabilities
from duo_input.ui.models.project_session import ProjectSession, SetActiveProfile
from duo_input.ui.mouse import MouseSwitchPage
from duo_input.ui.overview import OverviewPage
from duo_input.ui.profiles import ProfilesPage

APPLICATION_NAME = "Duo Input"
DIRTY_MARKER = "*"
PROJECT_FILTER = "Duo Input project (*.duoinput.json)"
MINIMUM_WIDTH = 1024
MINIMUM_HEIGHT = 700

TransportFactory = Callable[[], object | None]


def default_transport_factory() -> object | None:
    """Open the first port that presents the U1 identity, if there is one."""
    from duo_input.device.discovery import find_u1_ports
    from duo_input.device.qt_transport import QSerialPortTransport

    ports = find_u1_ports()
    if not ports:
        return None
    return QSerialPortTransport(ports[0].port_name)


class MainWindow(QMainWindow):
    """Owns the current :class:`ProjectSession` and the pages that render it."""

    session_changed = Signal(object)

    #: Navigation rows, in the order the sections appear.
    PAGE_OVERVIEW, PAGE_PROFILES, PAGE_BINDINGS, PAGE_MACROS, PAGE_MOUSE = range(5)
    PAGE_ORDER = (PAGE_OVERVIEW, PAGE_PROFILES, PAGE_BINDINGS, PAGE_MACROS, PAGE_MOUSE)

    def __init__(
        self,
        service: DeviceService,
        session: ProjectSession | None = None,
        transport_factory: TransportFactory | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._session = session if session is not None else ProjectSession.new()
        self.transport_factory = transport_factory or default_transport_factory
        self._updating_selector = False

        self.setMinimumSize(MINIMUM_WIDTH, MINIMUM_HEIGHT)
        self._build_ui()
        self._connect_service()
        self.set_session(self._session)

    # ----------------------------------------------------------------- layout

    def _build_ui(self) -> None:
        central = QWidget(self)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        outer.addLayout(self._build_toolbar(central))

        splitter = QSplitter(Qt.Orientation.Horizontal, central)
        self.nav = QListWidget(splitter)
        self.nav.setAccessibleName(self.tr("Sections"))
        self.nav.setMaximumWidth(240)
        self.nav.setMinimumWidth(160)

        self.pages = QStackedWidget(splitter)
        self.overview = OverviewPage(self.pages)
        self.profiles = ProfilesPage(self.pages)
        self.bindings = BindingsPage(self._service, self.pages)
        self.macros = MacrosPage(self._service, self.pages)
        self.mouse = MouseSwitchPage(self.pages)
        sections = (
            (self.tr("Overview"), self.overview),
            (self.tr("Profiles"), self.profiles),
            (self.tr("Bindings"), self.bindings),
            (self.tr("Macros"), self.macros),
            (self.tr("Mouse"), self.mouse),
        )
        for title, page in sections:
            self.nav.addItem(title)
            self.pages.addWidget(page)
        for page in self._editor_pages():
            page.command_requested.connect(self.apply_command)
        self.nav.setCurrentRow(self.PAGE_OVERVIEW)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)

        splitter.addWidget(self.nav)
        splitter.addWidget(self.pages)
        splitter.setStretchFactor(1, 1)
        outer.addWidget(splitter, 1)

        self.issues_list = QListWidget(central)
        self.issues_list.setAccessibleName(self.tr("Problems that block a write"))
        self.issues_list.setMaximumHeight(110)
        self.issues_list.setVisible(False)
        self.issues_list.currentRowChanged.connect(self.open_issue)
        self.issues_list.itemActivated.connect(
            lambda item: self.open_issue(self.issues_list.row(item))
        )
        outer.addWidget(self.issues_list)

        self.progress = QProgressBar(central)
        self.progress.setAccessibleName(self.tr("Transfer progress"))
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        outer.addWidget(self.progress)

        self.setCentralWidget(central)
        self.statusBar().showMessage(self.tr("Ready"))

        # Tab order follows the visual order: the toolbar row first, then the
        # section list and finally the page it selects.
        self.setTabOrder(self.profile_selector, self.connect_button)
        self.setTabOrder(self.connect_button, self.save_button)
        self.setTabOrder(self.save_button, self.write_button)
        self.setTabOrder(self.write_button, self.nav)
        self.setTabOrder(self.nav, self.pages)

    def _build_toolbar(self, parent: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)

        profile_label = QLabel(self.tr("Profile:"), parent)
        self.profile_selector = QComboBox(parent)
        self.profile_selector.setAccessibleName(self.tr("Active profile"))
        self.profile_selector.setMinimumWidth(220)
        profile_label.setBuddy(self.profile_selector)
        self.profile_selector.currentIndexChanged.connect(self._on_profile_selected)

        self.connection_label = QLabel(parent)
        self.connection_label.setAccessibleName(self.tr("Device connection"))

        self.connect_button = QPushButton(self.tr("Connect"), parent)
        self.connect_button.setAccessibleName(self.tr("Connect or disconnect the device"))
        self.connect_button.clicked.connect(self._on_connect_clicked)

        self.save_button = QPushButton(self.tr("Save"), parent)
        self.save_button.setAccessibleName(self.tr("Save the project file"))
        self.save_button.clicked.connect(self._on_save_clicked)

        self.write_button = QPushButton(self.tr("Write to device"), parent)
        self.write_button.setAccessibleName(self.tr("Write the configuration to the device"))
        self.write_button.clicked.connect(self.write_to_device)

        row.addWidget(profile_label)
        row.addWidget(self.profile_selector)
        row.addStretch(1)
        row.addWidget(self.connection_label)
        row.addWidget(self.connect_button)
        row.addWidget(self.save_button)
        row.addWidget(self.write_button)
        return row

    def _connect_service(self) -> None:
        self._service.state_changed.connect(self._on_state_changed)
        self._service.status_changed.connect(self._on_status_changed)
        self._service.progress_changed.connect(self._on_progress_changed)
        self._service.operation_succeeded.connect(self._on_operation_succeeded)
        self._service.operation_failed.connect(self._on_operation_failed)

    # ------------------------------------------------------------------ state

    @property
    def service(self) -> DeviceService:
        return self._service

    @property
    def session(self) -> ProjectSession:
        return self._session

    def set_session(self, session: ProjectSession) -> None:
        """Adopt ``session`` and repaint everything that depends on it."""
        self._session = session
        self._refresh_profile_selector()
        self._refresh_title()
        self._refresh_actions()
        self.overview.update_from(session, self._service)
        for page in self._editor_pages():
            page.set_session(session)
        self._refresh_issues()
        self.session_changed.emit(session)

    def apply_command(self, command: object) -> bool:
        """Apply one editing command. A refused command changes nothing.

        The commands validate themselves - a duplicate trigger, a full profile,
        a slot that does not exist - so the shell reports the refusal instead of
        letting a page guess whether its request was legal.
        """
        try:
            session = self._session.apply(command)
        except (TypeError, ValueError) as error:
            name = _command_name(command)
            self.overview.append_event(name, type(error).__name__)
            self.statusBar().showMessage(f"{name}: {error}")
            return False
        self.set_session(session)
        return True

    def show_page(self, page: int) -> None:
        self.nav.setCurrentRow(page)

    def _editor_pages(self) -> tuple[QWidget, ...]:
        return (self.profiles, self.bindings, self.macros, self.mouse)

    # ------------------------------------------------------------ validation

    def issues(self) -> tuple[ValidationIssue, ...]:
        return self._session.issues

    def open_issue(self, row: int) -> None:
        """Reveal the control one validation issue is about."""
        issues = self._session.issues
        if not 0 <= row < len(issues):
            return
        self._navigate_to(issues[row].path)

    def _navigate_to(self, path: str) -> None:
        parts = [part for part in path.split("/") if part]
        if len(parts) < 2 or parts[0] != "profiles" or not parts[1].isdigit():
            self.show_page(self.PAGE_OVERVIEW)
            return
        profile_index = int(parts[1])
        profiles = self._session.project.profiles
        if not 0 <= profile_index < len(profiles):
            self.show_page(self.PAGE_OVERVIEW)
            return
        profile = profiles[profile_index]
        if profile.id != self._session.project.active_profile_id:
            self.set_session(self._session.apply(SetActiveProfile(profile.id)))
        if len(parts) >= 4 and parts[2] == "bindings" and parts[3].isdigit():
            self.show_page(self.PAGE_BINDINGS)
            self.bindings.select_binding_row(int(parts[3]))
            return
        if len(parts) >= 4 and parts[2] == "macros" and parts[3].isdigit():
            self.show_page(self.PAGE_MACROS)
            self.macros.select_macro_row(int(parts[3]))
            return
        self.profiles.select_profile(profile.id)
        self.show_page(self.PAGE_PROFILES)

    def _refresh_issues(self) -> None:
        issues = self._session.issues
        self.issues_list.blockSignals(True)
        try:
            self.issues_list.clear()
            for issue in issues:
                self.issues_list.addItem(f"{issue.path}: {issue.message}")
        finally:
            self.issues_list.blockSignals(False)
        self.issues_list.setVisible(bool(issues))

    def _refresh_profile_selector(self) -> None:
        self._updating_selector = True
        try:
            profiles = self._session.project.profiles
            if self.profile_selector.count() != len(profiles):
                self.profile_selector.clear()
                for profile in profiles:
                    self.profile_selector.addItem(f"{profile.id} - {profile.name}", profile.id)
            else:
                for index, profile in enumerate(profiles):
                    self.profile_selector.setItemText(index, f"{profile.id} - {profile.name}")
                    self.profile_selector.setItemData(index, profile.id)
            for index, profile in enumerate(profiles):
                if profile.id == self._session.project.active_profile_id:
                    self.profile_selector.setCurrentIndex(index)
                    break
        finally:
            self._updating_selector = False

    def _refresh_title(self) -> None:
        name = self._session.path.name if self._session.path else self.tr("Untitled project")
        marker = f" {DIRTY_MARKER}" if self._session.dirty else ""
        self.setWindowTitle(f"{APPLICATION_NAME} - {name}{marker}")

    def _refresh_actions(self) -> None:
        self.write_button.setEnabled(self._session.can_write)
        state = self._service.state
        self.connection_label.setText(self.tr("Device: {0}").format(state.value))
        self.connect_button.setText(
            self.tr("Disconnect") if self._service.is_connected else self.tr("Connect")
        )

    def _sync_device_state(self) -> None:
        connected = self._service.is_connected
        # A stale hash from a device that is no longer attached would be a lie.
        device_hash = self._service.device_hash if connected else b""
        capabilities = (
            MouseCapabilities.from_device_info(self._service.device_info)
            if connected
            else MouseCapabilities()
        )
        self.bindings.set_capabilities(capabilities)
        self.mouse.set_capabilities(capabilities)
        self.set_session(
            self._session.with_connection(connected).with_device_hash(device_hash)
        )

    # ---------------------------------------------------------------- editing

    def _on_profile_selected(self, index: int) -> None:
        if self._updating_selector or index < 0:
            return
        profile_id = self.profile_selector.itemData(index)
        if profile_id is None or profile_id == self._session.project.active_profile_id:
            return
        self.set_session(self._session.apply(SetActiveProfile(int(profile_id))))

    # ------------------------------------------------------------------- file

    def _ask_save_path(self) -> Path | None:
        name, _ = QFileDialog.getSaveFileName(
            self, self.tr("Save project"), "", PROJECT_FILTER
        )
        if not name:
            return None
        path = Path(name)
        if not path.name.endswith(".duoinput.json"):
            path = path.with_name(path.stem + ".duoinput.json")
        return path

    def save_project(self, path: str | Path | None = None) -> bool:
        """Save the project. Returns False when the operator cancels or it fails."""
        target = Path(path) if path is not None else self._session.path
        if target is None:
            target = self._ask_save_path()
            if target is None:
                return False
        try:
            saved = self._session.save(target)
        except (ProjectError, OSError) as error:
            self.overview.append_event("save_project", type(error).__name__)
            QMessageBox.critical(self, self.tr("Save failed"), str(error))
            return False
        self.set_session(saved)
        self.statusBar().showMessage(self.tr("Project saved"))
        return True

    def _on_save_clicked(self) -> None:
        self.save_project()

    # ----------------------------------------------------------------- device

    def connect_device(self, transport: object | None = None) -> None:
        """Open a link. Without ``transport`` the discovery factory supplies one."""
        if self._service.is_connected:
            return
        link = transport if transport is not None else self.transport_factory()
        if link is None:
            self.overview.append_event("connect_device", "no_device_found")
            self.statusBar().showMessage(self.tr("No Duo Input device was found"))
            return
        self._service.connect_device(link)

    def disconnect_device(self) -> None:
        self._service.disconnect_device()
        self._sync_device_state()

    def _on_connect_clicked(self) -> None:
        if self._service.is_connected:
            self.disconnect_device()
        else:
            self.connect_device()

    def write_to_device(self) -> None:
        """Send the compiled project in one transaction; never edits it."""
        if not self._session.can_write:
            return
        try:
            package = compile_project_to_binary(self._session.project)
        except ValueError as error:
            self.overview.append_event("write_config", type(error).__name__)
            return
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self._service.write_config(package)

    # ------------------------------------------------------------ service slots

    def _on_state_changed(self, state: DeviceState) -> None:
        self.overview.append_event("device_state", state.value)
        self._sync_device_state()

    def _on_status_changed(self, status: object) -> None:
        self.overview.update_from(self._session, self._service)

    def _on_progress_changed(self, percent: int) -> None:
        self.progress.setValue(percent)

    def _on_operation_succeeded(self, result: object) -> None:
        self.overview.append_event(result.operation, "ok")
        self.progress.setVisible(False)
        self._sync_device_state()

    def _on_operation_failed(self, failure: object) -> None:
        detail = failure.reason.value
        if failure.error_code is not None:
            detail = f"{detail} ({failure.error_code.name})"
        self.overview.append_event(failure.operation, detail)
        self.progress.setVisible(False)
        self.statusBar().showMessage(f"{failure.operation}: {failure.reason.value}")
        self._sync_device_state()

    # ------------------------------------------------------------------ close

    def _confirm_close(self) -> QMessageBox.StandardButton:
        return QMessageBox.warning(
            self,
            self.tr("Unsaved changes"),
            self.tr("The project has unsaved changes. Save them before closing?"),
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        if not self._session.dirty:
            event.accept()
            return
        answer = self._confirm_close()
        if answer == QMessageBox.StandardButton.Cancel:
            event.ignore()
            return
        if answer == QMessageBox.StandardButton.Save and not self.save_project():
            event.ignore()
            return
        event.accept()


def _command_name(command: object) -> str:
    """Snake-case name of a command class, for the event log."""
    name = type(command).__name__
    return "".join(
        f"_{letter.lower()}" if letter.isupper() and index else letter.lower()
        for index, letter in enumerate(name)
    )


__all__ = [
    "APPLICATION_NAME",
    "DIRTY_MARKER",
    "MINIMUM_HEIGHT",
    "MINIMUM_WIDTH",
    "MainWindow",
    "default_transport_factory",
]
