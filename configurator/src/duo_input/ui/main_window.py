"""The application shell: navigation, profile selector, save and write.

Editing is entirely local. The device is only ever changed by pressing Write,
which sends one transactional package through :class:`DeviceService`; the shell
merely mirrors whatever that service reports back.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QSettings, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
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
from duo_input.domain.config_reader import binary_to_project
from duo_input.domain.project_store import ProjectError
from duo_input.domain.text_compiler import compile_project_to_binary
from duo_input.domain.validation import ValidationIssue
from duo_input.i18n import TranslationManager
from duo_input.persistence.autosave import AutosaveService, Recovery
from duo_input.ui.bindings import BindingsPage
from duo_input.ui.diagnostics import DiagnosticsPage
from duo_input.ui.macros import MacrosPage
from duo_input.ui.models.binding_table import MouseCapabilities
from duo_input.ui.models.project_session import ProjectSession, SetActiveProfile
from duo_input.ui import motion
from duo_input.ui.mouse import MouseSwitchPage
from duo_input.ui.overview import OverviewPage
from duo_input.ui.profiles import ProfilesPage
from duo_input.ui.settings import SettingsPage
from duo_input.ui.theme import (
    NAME_RAIL,
    NAME_STATE_STRIP,
    NAME_TOOLBAR,
    ROLE_BANNER,
    ROLE_CHIP,
    ROLE_DESTRUCTIVE,
    ROLE_FIELD_LABEL,
    ROLE_PRIMARY,
    SIGNAL_ERROR,
    SIGNAL_MUTED,
    SIGNAL_OK,
    SIGNAL_WARN,
    SPACE_LG,
    SPACE_MD,
    SPACE_SM,
    set_role,
    set_signal,
)

APPLICATION_NAME = "Duo Input"
DIRTY_MARKER = "*"
PROJECT_FILTER = "Duo Input project (*.duoinput.json)"
MINIMUM_WIDTH = 1024
MINIMUM_HEIGHT = 700

TransportFactory = Callable[[], object | None]


#: Where the path of the last project is kept between runs, so the program
#: opens what the operator was working on rather than an empty one.
LAST_PROJECT_KEY = "projects/last"

#: How often the shell looks for a device that is not attached yet. Short
#: enough that plugging a board in feels immediate, long enough that the
#: retry costs nothing while the socket stays empty.
AUTOCONNECT_INTERVAL_MS = 2000


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
    (
        PAGE_OVERVIEW,
        PAGE_PROFILES,
        PAGE_BINDINGS,
        PAGE_MACROS,
        PAGE_MOUSE,
        PAGE_DIAGNOSTICS,
        PAGE_SETTINGS,
    ) = range(7)
    PAGE_ORDER = (
        PAGE_OVERVIEW,
        PAGE_PROFILES,
        PAGE_BINDINGS,
        PAGE_MACROS,
        PAGE_MOUSE,
        PAGE_DIAGNOSTICS,
        PAGE_SETTINGS,
    )

    def __init__(
        self,
        service: DeviceService,
        session: ProjectSession | None = None,
        transport_factory: TransportFactory | None = None,
        translations: TranslationManager | None = None,
        settings: QSettings | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self.translations = translations or TranslationManager()
        self._session = session if session is not None else ProjectSession.new()
        # Injected so a test can point it at a temporary file. A default
        # QSettings silently stores nothing until the application has been
        # given an organisation name, which only app.main() does.
        self._settings = settings if settings is not None else QSettings()
        #: Mouse buttons the attached device has actually reported.
        self._observed_buttons: frozenset[int] = frozenset()
        self.transport_factory = transport_factory or default_transport_factory
        #: Set while a device-initiated read is in flight, so its answer -
        #: which arrives on ``operation_succeeded`` several chunks later - is
        #: only adopted when it is actually the read this window asked for.
        self._reading_device = False
        #: Set once the connect hook has queued its read. The device wins at
        #: startup and only at startup: the autoconnect timer runs for the
        #: life of the window, so a read on every connect would replace the
        #: operator's open project with the board's the moment they plug a
        #: board in to write that project to it - saved, so the dirty guard
        #: below lets it through - and drop the file path with it.
        self._startup_read_done = False
        self.autosave = AutosaveService(parent=self)
        self.autosave.timer.timeout.connect(self.autosave_now)
        self.autosave.timer.start()
        # The device attaches itself. The operator plugs a board in and the
        # program notices; there is nothing for them to decide, so there is no
        # button to press.
        self._autoconnect = QTimer(self)
        self._autoconnect.setInterval(AUTOCONNECT_INTERVAL_MS)
        self._autoconnect.timeout.connect(self.try_autoconnect)
        self._autoconnect.start()
        # Attach at once as well: a board already plugged in should be there
        # by the time the window is on screen, not two seconds afterwards.
        QTimer.singleShot(0, self.try_autoconnect)
        self._updating_selector = False

        self.setMinimumSize(MINIMUM_WIDTH, MINIMUM_HEIGHT)
        self._build_ui()
        self._connect_service()
        self.set_session(self._session)

    # ----------------------------------------------------------------- layout

    def _build_ui(self) -> None:
        central = QWidget(self)
        outer = QVBoxLayout(central)
        # The chrome runs edge to edge; every inset below is a decision the
        # rows and the pages make for themselves.
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_toolbar(central))
        outer.addWidget(self._build_state_strip(central))

        splitter = QSplitter(Qt.Orientation.Horizontal, central)
        splitter.setHandleWidth(0)
        self.nav = QListWidget(splitter)
        self.nav.setObjectName(NAME_RAIL)
        self.nav.setAccessibleName(self.tr("Sections"))
        self.nav.setFrameShape(QListWidget.Shape.NoFrame)
        self.nav.setMaximumWidth(240)
        self.nav.setMinimumWidth(160)

        self.pages = QStackedWidget(splitter)
        self.overview = OverviewPage(self.pages)
        self.profiles = ProfilesPage(self.pages)
        self.bindings = BindingsPage(self._service, self.pages)
        self.macros = MacrosPage(self._service, self.pages)
        self.mouse = MouseSwitchPage(self._service, self.pages)
        self.diagnostics = DiagnosticsPage(self._service, self.pages)
        self.settings = SettingsPage(self.translations, parent=self.pages)
        sections = (
            (self.tr("Overview"), self.overview),
            (self.tr("Profiles"), self.profiles),
            (self.tr("Bindings"), self.bindings),
            (self.tr("Macros"), self.macros),
            (self.tr("Mouse"), self.mouse),
            (self.tr("Diagnostics"), self.diagnostics),
            (self.tr("Settings"), self.settings),
        )
        for title, page in sections:
            self.nav.addItem(title)
            self.pages.addWidget(page)
        for page in self._editor_pages():
            page.command_requested.connect(self.apply_command)
        for page in (self.bindings, self.mouse):
            page.button_observed.connect(self._on_button_observed)
        self.nav.setCurrentRow(self.PAGE_OVERVIEW)
        self.nav.currentRowChanged.connect(self._show_page_index)

        splitter.addWidget(self.nav)
        splitter.addWidget(self.pages)
        splitter.setStretchFactor(1, 1)
        outer.addWidget(splitter, 1)

        outer.addWidget(self._build_footer(central))

        self.setCentralWidget(central)
        self.statusBar().showMessage(self.tr("Ready"))

        # Tab order follows the visual order: the toolbar row first, then the
        # section list and finally the page it selects.
        self.setTabOrder(self.profile_selector, self.open_button)
        self.setTabOrder(self.open_button, self.save_button)
        self.setTabOrder(self.save_button, self.write_button)
        self.setTabOrder(self.write_button, self.nav)
        self.setTabOrder(self.nav, self.pages)

    def _build_toolbar(self, parent: QWidget) -> QWidget:
        bar = QFrame(parent)
        bar.setObjectName(NAME_TOOLBAR)
        row = QHBoxLayout(bar)
        row.setContentsMargins(SPACE_LG, SPACE_SM, SPACE_LG, SPACE_SM)
        row.setSpacing(SPACE_SM)

        profile_label = QLabel(self.tr("Profile:"), bar)
        set_role(profile_label, ROLE_FIELD_LABEL)
        self.profile_selector = QComboBox(bar)
        self.profile_selector.setAccessibleName(self.tr("Active profile"))
        self.profile_selector.setMinimumWidth(220)
        profile_label.setBuddy(self.profile_selector)
        self.profile_selector.currentIndexChanged.connect(self._on_profile_selected)

        self.connection_label = QLabel(bar)
        self.connection_label.setAccessibleName(self.tr("Device connection"))
        set_role(self.connection_label, ROLE_CHIP)
        set_signal(self.connection_label, SIGNAL_MUTED)

        self.open_button = QPushButton(self.tr("Open"), bar)
        self.open_button.setAccessibleName(self.tr("Open a project file"))
        self.open_button.clicked.connect(self._on_open_clicked)

        self.save_button = QPushButton(self.tr("Save"), bar)
        self.save_button.setAccessibleName(self.tr("Save the project file"))
        set_role(self.save_button, ROLE_PRIMARY)
        self.save_button.clicked.connect(self._on_save_clicked)

        # Save writes a file that can be thrown away; this one overwrites what
        # the hardware is running. The weight is the warning.
        self.write_button = QPushButton(self.tr("Write to device"), bar)
        self.write_button.setAccessibleName(self.tr("Write the configuration to the device"))
        set_role(self.write_button, ROLE_DESTRUCTIVE)
        self.write_button.clicked.connect(self.write_to_device)

        row.addWidget(profile_label)
        row.addWidget(self.profile_selector)
        row.addStretch(1)
        row.addWidget(self.connection_label)
        row.addSpacing(SPACE_MD)
        row.addWidget(self.open_button)
        row.addWidget(self.save_button)
        row.addWidget(self.write_button)
        return bar

    def _build_state_strip(self, parent: QWidget) -> QWidget:
        """The three states of specification section 18.5, always all three.

        They sit side by side on purpose. Any one of them alone invites the
        wrong conclusion - a saved file says nothing about what the device is
        holding - so the strip never hides the two that happen to be settled.
        """
        strip = QFrame(parent)
        strip.setObjectName(NAME_STATE_STRIP)
        row = QHBoxLayout(strip)
        row.setContentsMargins(SPACE_LG, SPACE_SM, SPACE_LG, SPACE_SM)
        row.setSpacing(SPACE_SM)

        self.state_chips: dict[str, QLabel] = {}
        for key, name in (
            ("changes", self.tr("Local changes")),
            ("file", self.tr("Project file")),
            ("device", self.tr("Configuration on the device")),
        ):
            chip = QLabel(strip)
            chip.setAccessibleName(name)
            set_role(chip, ROLE_CHIP)
            set_signal(chip, SIGNAL_MUTED)
            self.state_chips[key] = chip
            row.addWidget(chip)
        row.addStretch(1)
        return strip

    def _build_footer(self, parent: QWidget) -> QWidget:
        """What only appears when something is wrong, or something is running."""
        holder = QWidget(parent)
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(SPACE_LG, SPACE_SM, SPACE_LG, SPACE_SM)
        layout.setSpacing(SPACE_SM)

        self.issues_banner = QLabel(
            self.tr("Fix these before writing to the device."), holder
        )
        set_role(self.issues_banner, ROLE_BANNER)
        set_signal(self.issues_banner, SIGNAL_ERROR)
        self.issues_banner.setWordWrap(True)
        self.issues_banner.setVisible(False)
        layout.addWidget(self.issues_banner)

        self.issues_list = QListWidget(holder)
        self.issues_list.setAccessibleName(self.tr("Problems that block a write"))
        self.issues_list.setMaximumHeight(110)
        self.issues_list.setVisible(False)
        self.issues_list.currentRowChanged.connect(self.open_issue)
        self.issues_list.itemActivated.connect(
            lambda item: self.open_issue(self.issues_list.row(item))
        )
        layout.addWidget(self.issues_list)

        self.progress = QProgressBar(holder)
        self.progress.setAccessibleName(self.tr("Transfer progress"))
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        return holder

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
        self.diagnostics.set_session(session)
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

    def _show_page_index(self, index: int) -> None:
        """Switch pages, bringing the new one up rather than snapping to it."""
        self.pages.setCurrentIndex(index)
        page = self.pages.currentWidget()
        if page is not None:
            motion.fade_in(page)

    def _editor_pages(self) -> tuple[QWidget, ...]:
        """Pages that both render a session and ask for changes to it."""
        return (self.profiles, self.bindings, self.macros, self.mouse)

    # ------------------------------------------------------------- recovery

    def autosave_now(self) -> bool:
        """Keep a recovery copy of the current session. Never raises.

        A full disk must not take the editor down mid-sentence, so a failed
        autosave is reported alongside the device events and the operator
        keeps typing.
        """
        try:
            return bool(self.autosave.save(self._session))
        except (ProjectError, OSError) as error:
            self.overview.append_event("autosave", type(error).__name__)
            return False

    def offer_recovery(self) -> bool:
        """Ask about unsaved work from a previous run. Returns whether it was taken.

        A recovery is only ever offered when the autosave is newer than the
        project file, so accepting it can never move the operator backwards.
        """
        try:
            recovery = self.autosave.recovery()
        except (ProjectError, OSError) as error:
            # Startup continues either way: a broken autosave is a thing to
            # report, not a reason to refuse to open the program.
            self.overview.append_event("autosave", type(error).__name__)
            return False
        if recovery is None:
            return False
        if self._ask_recovery(recovery) != QMessageBox.StandardButton.Yes:
            self.autosave.discard()
            return False
        self.set_session(recovery.session)
        self.overview.append_event("autosave", "recovered")
        return True

    def _ask_recovery(self, recovery: Recovery) -> QMessageBox.StandardButton:
        name = recovery.project_path.name if recovery.project_path else self.tr("a new project")
        return QMessageBox.question(
            self,
            self.tr("Unsaved work was found"),
            self.tr(
                "Duo Input closed with unsaved changes to {0} on {1}. Recover them?"
            ).format(name, recovery.saved_at.isoformat(timespec="seconds")),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )

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
        self.issues_banner.setVisible(bool(issues))

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
        # The state itself is a protocol identifier and stays in one language,
        # so a screenshot means the same thing to whoever reads it next.
        self.connection_label.setText(self.tr("Device: {0}").format(state.value))
        set_signal(
            self.connection_label,
            SIGNAL_OK if self._service.is_connected else SIGNAL_MUTED,
        )
        self._refresh_state_strip()

    def _refresh_state_strip(self) -> None:
        """Say where the project stands, in each of the three places it exists."""
        session = self._session

        changes = self.state_chips["changes"]
        changes.setText(
            self.tr("Local changes: unsaved")
            if session.dirty
            else self.tr("Local changes: none")
        )
        set_signal(changes, SIGNAL_WARN if session.dirty else SIGNAL_OK)

        stored = self.state_chips["file"]
        if session.path is None:
            stored.setText(self.tr("Project file: not created yet"))
            set_signal(stored, SIGNAL_MUTED)
        else:
            stored.setText(self.tr("Project file: {0}").format(session.path.name))
            set_signal(stored, SIGNAL_OK)

        device = self.state_chips["device"]
        if not session.device_hash:
            device.setText(self.tr("Written to device: no link"))
            set_signal(device, SIGNAL_MUTED)
        elif session.device_matches:
            device.setText(self.tr("Written to device: matches the project"))
            set_signal(device, SIGNAL_OK)
        else:
            device.setText(self.tr("Written to device: differs from the project"))
            set_signal(device, SIGNAL_WARN)

    def _on_button_observed(self, button: int) -> None:
        """Remember a button the device reported and tell both editors."""
        self._observed_buttons = self._observed_buttons | {int(button)}
        self._sync_device_state()

    def _sync_device_state(self) -> None:
        connected = self._service.is_connected
        # A stale hash from a device that is no longer attached would be a lie.
        device_hash = self._service.device_hash if connected else b""
        if not connected:
            # A reconnect may be a different mouse; its extra buttons have to
            # be seen again before they are offered.
            self._observed_buttons = frozenset()
        capabilities = (
            MouseCapabilities.from_device_info(self._service.device_info)
            if connected
            else MouseCapabilities()
        )
        # Protocol v1 never advertises buttons 4 and 5, so an observation is
        # the only evidence they exist.  It has to outlive this re-read, which
        # runs after every successful operation, a write included.
        for button in sorted(self._observed_buttons):
            capabilities = capabilities.observing(button)
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
        self._remember_project(saved.path)
        # The file on disk now holds everything the autosave was protecting.
        self.autosave.discard()
        self.statusBar().showMessage(self.tr("Project saved"))
        return True

    def _on_save_clicked(self) -> None:
        self.save_project()

    def _ask_open_path(self) -> Path | None:
        name, _ = QFileDialog.getOpenFileName(
            self, self.tr("Open project"), "", PROJECT_FILTER
        )
        return Path(name) if name else None

    def _on_open_clicked(self) -> None:
        path = self._ask_open_path()
        if path is not None:
            self.open_project(path)

    def open_project(self, path: str | Path) -> bool:
        """Read a project file into the session. False when it could not be read."""
        try:
            session = ProjectSession.load(path)
        except (ProjectError, OSError) as error:
            self.overview.append_event("open_project", type(error).__name__)
            self.statusBar().showMessage(
                self.tr("That project could not be opened: {0}").format(error)
            )
            return False
        self.set_session(session)
        self._remember_project(session.path)
        # What is on screen is now what is on disk; the autosave was guarding
        # the session this one replaced.
        self.autosave.discard()
        self.statusBar().showMessage(self.tr("Project opened"))
        return True

    def last_project_path(self) -> Path | None:
        """The file this program had open when it was last used, if it is still there."""
        stored = self._settings.value(LAST_PROJECT_KEY)
        if not stored:
            return None
        path = Path(str(stored))
        return path if path.is_file() else None

    def _remember_project(self, path: Path | None) -> None:
        if path is None:
            return
        self._settings.setValue(LAST_PROJECT_KEY, str(path))
        self._settings.sync()

    def reopen_last_project(self) -> bool:
        """Open what was open last time, silently. False when there is nothing to open.

        A file that has been moved or deleted since is not an error worth a
        dialog: the operator gets an empty project and a line in the status
        bar, which is what they would get from a first run anyway.
        """
        path = self.last_project_path()
        if path is None:
            return False
        return self.open_project(path)

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

    def try_autoconnect(self) -> None:
        """Attach to the device if one is there, and say nothing if it is not.

        This runs on a timer, so an empty socket is the ordinary state rather
        than an event worth reporting: announcing it would fill the log with a
        line every two seconds and tell the operator nothing they cannot see
        in the connection label.
        """
        if self._service.is_connected:
            return
        link = self.transport_factory()
        if link is None:
            return
        self._service.connect_device(link)

    def read_device_project(self) -> None:
        """Ask the device for the configuration it is running.

        The answer arrives later, on ``operation_succeeded``; see
        ``_on_operation_succeeded``.
        """
        if not self._service.is_connected:
            return
        if self._reading_device:
            # The startup sequence and the connect signal can both ask for
            # this same read - DeviceService.read_config is not reentrant,
            # so a second call here would fail BUSY, and that failure would
            # be indistinguishable from the real read's outcome. Make the
            # second call a no-op instead of something to recover from.
            return
        self._reading_device = True
        self._service.read_config()

    def disconnect_device(self) -> None:
        # DeviceService.disconnect_device clears its operation before tearing
        # the link down, so it deliberately reports no failure - which means
        # _on_operation_failed never runs and never clears the flag below. A
        # read that was in flight would otherwise stay "in flight" forever,
        # and read_device_project would refuse every later read for the life
        # of the window, silently and permanently.
        self._reading_device = False
        self._service.disconnect_device()
        self._sync_device_state()

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
        self.diagnostics.refresh()

    def _on_progress_changed(self, percent: int) -> None:
        self.progress.setValue(percent)

    def _on_operation_succeeded(self, result: object) -> None:
        if result.operation == "read_config" and self._reading_device:
            self._reading_device = False
            self._adopt_device_project(result.value)
        elif result.operation == "connect_device" and not self._startup_read_done:
            # The device attaches itself, so it also answers "what is it
            # running?" itself: the operator never has to ask. Only the first
            # connect this window makes does so - that is the startup the
            # specification grants the device ("At startup, a device that
            # answers has its configuration read and shown"). Later connects
            # queue nothing: by then the session is the operator's, and a
            # board plugged in to receive it must not overwrite it instead.
            # The flag is set on queueing rather than on the answer, so a
            # read that fails is not retried on the next reconnect either.
            #
            # The read is deferred to the next tick: this handler runs inside
            # DeviceService's own unwind of "connect_device", and starting a
            # second operation synchronously here would re-enter the service
            # mid-transaction.
            self._startup_read_done = True
            QTimer.singleShot(0, self.read_device_project)
        self.overview.append_event(result.operation, "ok")
        self.diagnostics.refresh()
        self.progress.setVisible(False)
        self._sync_device_state()

    def _adopt_device_project(self, package: bytes | None) -> None:
        """Show what the device is running, unless the operator is mid-edit.

        The read takes several chunks over a serial link. Somebody who started
        typing while it was in flight must not have that thrown away by an
        answer that arrives afterwards - the device is authoritative at
        startup, not at every moment.
        """
        if package is None:
            return
        if self._session.dirty:
            self.statusBar().showMessage(
                self.tr("The device has a different configuration; your edits were kept.")
            )
            return
        try:
            project = binary_to_project(package)
        except ProjectError as error:
            self.overview.append_event("read_config", type(error).__name__)
            self.statusBar().showMessage(
                self.tr("The device's configuration could not be read: {0}").format(error)
            )
            return
        self.set_session(ProjectSession(project=project))
        self.statusBar().showMessage(self.tr("Configuration read from the device"))

    def _on_operation_failed(self, failure: object) -> None:
        if failure.operation == "read_config" and self._reading_device:
            self._reading_device = False
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
