"""The Profiles page: the eight slots the device switches between.

The page renders a :class:`ProjectSession` and never edits one. Every control
emits a :class:`~duo_input.ui.models.project_session.ProjectCommand` through
``command_requested``; the shell decides what to do with it. That keeps undo a
matter of keeping the previous session and keeps this widget free of any
knowledge about files, devices or the binary format.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from duo_input.domain.models import Profile
from duo_input.ui.models.project_session import (
    ClearProfile,
    CopyProfile,
    ProjectSession,
    RenameProfile,
    SetActiveProfile,
    SetProfileColor,
)
from duo_input.ui.theme import (
    LINE_STRONG,
    ROLE_MONO,
    SPACE_LG,
    SPACE_MD,
    SPACE_SM,
    fact_form,
    field_label,
    monospace_font,
    page_header,
    set_role,
)

#: Side of the square that shows a profile's colour, in logical pixels.
SWATCH = 12

#: Longest profile name the binary format accepts, mirrored from validation.
NAME_MAX_LENGTH = 48


class ProfilesPage(QWidget):
    """Eight slots with a name, a colour, copy, clear and activate."""

    #: Item data role carrying the profile ID of a slot row.
    PROFILE_ID_ROLE = Qt.ItemDataRole.UserRole

    command_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._session = ProjectSession.new()
        self._selected_id = 1
        self._updating = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_LG)
        outer.setSpacing(SPACE_LG)
        outer.addWidget(
            page_header(
                self.tr("Profiles"),
                self.tr(
                    "Eight slots the device switches between. One of them is the "
                    "one it starts in."
                ),
                self,
            )
        )

        columns = QHBoxLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(SPACE_MD)
        outer.addLayout(columns, 1)

        self.slots = QListWidget(self)
        self.slots.setAccessibleName(self.tr("Profile slots"))
        self.slots.setMaximumWidth(320)
        self.slots.setMinimumWidth(240)
        self.slots.setIconSize(QSize(SWATCH, SWATCH))
        self.slots.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.slots.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.slots.currentRowChanged.connect(self._on_row_changed)
        columns.addWidget(self.slots)

        columns.addWidget(self._build_editor(), 1)
        self._rebuild_slots()
        self._refresh_editor()

    # ---------------------------------------------------------------- layout

    def _build_editor(self) -> QWidget:
        box = QGroupBox(self.tr("Profile"), self)
        # A form of three short fields has no business spanning a wide window;
        # a value the eye has to travel to is a value that gets missed.
        box.setMaximumWidth(560)
        layout = QVBoxLayout(box)
        layout.setSpacing(SPACE_MD)
        form = fact_form()

        self.name_edit = QLineEdit(box)
        self.name_edit.setAccessibleName(self.tr("Profile name"))
        self.name_edit.setMaxLength(NAME_MAX_LENGTH)
        self.name_edit.editingFinished.connect(self._on_name_edited)
        form.addRow(field_label(self.tr("Name:"), box), self.name_edit)

        self.color_button = QPushButton(box)
        self.color_button.setAccessibleName(self.tr("Profile colour"))
        self.color_button.setIconSize(QSize(SWATCH, SWATCH))
        self.color_button.setFont(monospace_font())
        self.color_button.clicked.connect(self._on_color_clicked)
        form.addRow(field_label(self.tr("Colour:"), box), self.color_button)

        self.routes_label = QLabel(box)
        self.routes_label.setAccessibleName(self.tr("Profile routes"))
        set_role(self.routes_label, ROLE_MONO)
        self.routes_label.setFont(monospace_font())
        form.addRow(field_label(self.tr("Routes:"), box), self.routes_label)
        layout.addLayout(form)

        copy_row = QHBoxLayout()
        copy_row.setSpacing(SPACE_SM)
        self.copy_target = QComboBox(box)
        self.copy_target.setAccessibleName(self.tr("Copy destination"))
        self.copy_button = QPushButton(self.tr("Copy into"), box)
        self.copy_button.setAccessibleName(self.tr("Copy this profile into another slot"))
        self.copy_button.clicked.connect(self._on_copy_clicked)
        copy_row.addWidget(self.copy_button)
        copy_row.addWidget(self.copy_target, 1)
        layout.addLayout(copy_row)

        actions = QHBoxLayout()
        actions.setSpacing(SPACE_SM)
        self.activate_button = QPushButton(self.tr("Make active"), box)
        self.activate_button.setAccessibleName(self.tr("Start the device in this profile"))
        self.activate_button.clicked.connect(self._on_activate_clicked)
        self.clear_button = QPushButton(self.tr("Clear"), box)
        self.clear_button.setAccessibleName(self.tr("Reset this profile slot"))
        self.clear_button.clicked.connect(self._on_clear_clicked)
        actions.addWidget(self.activate_button)
        actions.addWidget(self.clear_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        layout.addStretch(1)
        return box

    # ----------------------------------------------------------------- state

    @property
    def session(self) -> ProjectSession:
        return self._session

    @property
    def selected_profile_id(self) -> int:
        return self._selected_id

    def pending_edit_command(self) -> object | None:
        """The command a focus-out would send, for text still being typed.

        The name field turns text into a command on ``editingFinished``, so a
        name half typed is nowhere near the session and ``dirty`` cannot see
        it. Anything about to repaint this page from a session has to ask for
        this first: otherwise the field is simply overwritten and the work is
        gone with it, with nothing on screen to say so.

        It hands the command back rather than emitting it, because applying it
        would repaint every page - including one whose own field has not been
        asked yet.
        """
        return self._pending_name_command()

    def set_session(self, session: ProjectSession) -> None:
        """Render ``session``; the slot that was selected stays selected."""
        self._session = session
        self._rebuild_slots()
        self._refresh_editor()

    def select_profile(self, profile_id: int) -> None:
        for row in range(self.slots.count()):
            if self.slots.item(row).data(self.PROFILE_ID_ROLE) == profile_id:
                self.slots.setCurrentRow(row)
                return
        raise ValueError(f"no profile slot with ID {profile_id}")

    def set_color(self, color_rgb: tuple[int, int, int]) -> None:
        """Ask for a new colour on the selected slot."""
        self.command_requested.emit(SetProfileColor(self._selected_id, tuple(color_rgb)))

    # -------------------------------------------------------------- painting

    def _profile(self, profile_id: int) -> Profile:
        for profile in self._session.project.profiles:
            if profile.id == profile_id:
                return profile
        raise ValueError(f"no profile with ID {profile_id}")

    def _rebuild_slots(self) -> None:
        profiles = self._session.project.profiles
        active = self._session.project.active_profile_id
        self._updating = True
        try:
            if self.slots.count() != len(profiles):
                self.slots.clear()
                for profile in profiles:
                    item = QListWidgetItem("", self.slots)
                    item.setData(self.PROFILE_ID_ROLE, profile.id)
            for row, profile in enumerate(profiles):
                item = self.slots.item(row)
                item.setData(self.PROFILE_ID_ROLE, profile.id)
                item.setText(self._slot_text(profile, active))
                # The colour is the profile's, not the text's: a pale yellow
                # profile name on white is a name nobody can read.
                item.setIcon(_swatch(profile.color_rgb))
            if not any(profile.id == self._selected_id for profile in profiles):
                self._selected_id = profiles[0].id
            for row, profile in enumerate(profiles):
                if profile.id == self._selected_id:
                    self.slots.setCurrentRow(row)
                    break
        finally:
            self._updating = False

    def _slot_text(self, profile: Profile, active_id: int) -> str:
        marker = " *" if profile.id == active_id else ""
        return self.tr("{0} - {1} ({2} bindings, {3} macros){4}").format(
            profile.id, profile.name, len(profile.bindings), len(profile.macros), marker
        )

    def _refresh_editor(self) -> None:
        profile = self._profile(self._selected_id)
        self._updating = True
        try:
            self.name_edit.setText(profile.name)
            red, green, blue = profile.color_rgb
            self.color_button.setText(f"#{red:02X}{green:02X}{blue:02X}")
            self.color_button.setIcon(_swatch(profile.color_rgb))
            self.routes_label.setText(
                f"{profile.keyboard_route.name} / {profile.mouse_route.name}"
                f" / {profile.text_layout.name}"
            )
            self.copy_target.clear()
            for candidate in self._session.project.profiles:
                if candidate.id != profile.id:
                    self.copy_target.addItem(f"{candidate.id} - {candidate.name}", candidate.id)
        finally:
            self._updating = False
        self.activate_button.setEnabled(
            profile.id != self._session.project.active_profile_id
        )

    # ----------------------------------------------------------------- slots

    def _on_row_changed(self, row: int) -> None:
        if row < 0:
            return
        profile_id = self.slots.item(row).data(self.PROFILE_ID_ROLE)
        if profile_id is None:
            return
        self._selected_id = int(profile_id)
        if not self._updating:
            self._refresh_editor()

    def _on_name_edited(self) -> None:
        command = self._pending_name_command()
        if command is not None:
            self.command_requested.emit(command)

    def _pending_name_command(self) -> RenameProfile | None:
        if self._updating:
            return None
        name = self.name_edit.text()
        if name == self._profile(self._selected_id).name:
            return None
        return RenameProfile(self._selected_id, name)

    def _on_color_clicked(self) -> None:
        current = QColor(*self._profile(self._selected_id).color_rgb)
        chosen = QColorDialog.getColor(current, self, self.tr("Profile colour"))
        if chosen.isValid():
            self.set_color((chosen.red(), chosen.green(), chosen.blue()))

    def _on_copy_clicked(self) -> None:
        target = self.copy_target.currentData()
        if target is None:
            return
        self.command_requested.emit(CopyProfile(self._selected_id, int(target)))

    def _on_clear_clicked(self) -> None:
        self.command_requested.emit(ClearProfile(self._selected_id))

    def _on_activate_clicked(self) -> None:
        self.command_requested.emit(SetActiveProfile(self._selected_id))


def _swatch(color_rgb: tuple[int, int, int]) -> QIcon:
    """A small square of one profile's colour, outlined so white still shows."""
    pixmap = QPixmap(SWATCH, SWATCH)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(*color_rgb))
    painter.setPen(QColor(LINE_STRONG))
    painter.drawRoundedRect(0, 0, SWATCH - 1, SWATCH - 1, 2, 2)
    painter.end()
    return QIcon(pixmap)


__all__ = ["NAME_MAX_LENGTH", "SWATCH", "ProfilesPage"]
