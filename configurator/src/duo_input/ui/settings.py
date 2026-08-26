"""The Settings page: interface language, where projects live, how much is logged.

A language change is remembered immediately and applied on the next start. The
page says so out loud rather than half-retranslating the window and leaving the
operator to wonder which labels are stale.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSettings, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from duo_input.i18n import LANGUAGES, TranslationManager
from duo_input.persistence.locations import LOGGER_NAME

#: How each shipped language names itself, in that language.
LANGUAGE_NAMES = {"ru": "Русский", "en": "English"}

#: Log levels the operator may choose, quietest last.
LOG_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR")


class SettingsPage(QWidget):
    """Preferences that belong to the person, not to the project."""

    PROJECT_DIRECTORY_KEY = "projects/directory"
    LOG_LEVEL_KEY = "logging/level"

    settings_changed = Signal()

    def __init__(
        self,
        translations: TranslationManager,
        settings: QSettings | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._translations = translations
        self._settings = settings if settings is not None else translations.settings
        self._updating = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)
        outer.addWidget(self._build_interface())
        outer.addWidget(self._build_projects())
        outer.addWidget(self._build_logging())
        outer.addStretch(1)
        self._load()

    # ---------------------------------------------------------------- layout

    def _build_interface(self) -> QWidget:
        box = QGroupBox(self.tr("Interface"), self)
        form = QFormLayout(box)

        self.language_combo = QComboBox(box)
        self.language_combo.setAccessibleName(self.tr("Interface language"))
        for language in LANGUAGES:
            self.language_combo.addItem(LANGUAGE_NAMES.get(language, language), language)
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        form.addRow(QLabel(self.tr("Language:"), box), self.language_combo)

        self.restart_label = QLabel(box)
        self.restart_label.setAccessibleName(self.tr("What happens after a language change"))
        self.restart_label.setWordWrap(True)
        form.addRow(self.restart_label)
        return box

    def _build_projects(self) -> QWidget:
        box = QGroupBox(self.tr("Projects"), self)
        form = QFormLayout(box)

        row = QHBoxLayout()
        self.project_directory_edit = QLineEdit(box)
        self.project_directory_edit.setAccessibleName(self.tr("Default project folder"))
        self.project_directory_edit.editingFinished.connect(self._on_directory_edited)
        self.browse_button = QPushButton(self.tr("Browse..."), box)
        self.browse_button.setAccessibleName(self.tr("Choose the default project folder"))
        self.browse_button.clicked.connect(self._on_browse_clicked)
        row.addWidget(self.project_directory_edit, 1)
        row.addWidget(self.browse_button)
        holder = QWidget(box)
        holder.setLayout(row)
        form.addRow(QLabel(self.tr("Folder:"), box), holder)
        return box

    def _build_logging(self) -> QWidget:
        box = QGroupBox(self.tr("Logging"), self)
        form = QFormLayout(box)

        self.log_level_combo = QComboBox(box)
        self.log_level_combo.setAccessibleName(self.tr("How much is written to the log"))
        for level in LOG_LEVELS:
            self.log_level_combo.addItem(level, level)
        self.log_level_combo.currentIndexChanged.connect(self._on_log_level_changed)
        form.addRow(QLabel(self.tr("Level:"), box), self.log_level_combo)

        note = QLabel(
            self.tr("Macro text is never written to the log, at any level."), box
        )
        note.setWordWrap(True)
        form.addRow(note)
        return box

    # ----------------------------------------------------------------- state

    def _load(self) -> None:
        self._updating = True
        try:
            language = self._translations.saved_language()
            self.language_combo.setCurrentIndex(self.language_combo.findData(language))
            self.project_directory_edit.setText(str(self.project_directory()))
            level = str(self._settings.value(self.LOG_LEVEL_KEY) or "INFO")
            index = self.log_level_combo.findData(level if level in LOG_LEVELS else "INFO")
            self.log_level_combo.setCurrentIndex(index)
        finally:
            self._updating = False
        self.restart_label.setText("")

    def project_directory(self) -> Path:
        """Where the Save dialog starts. Defaults to the documents folder."""
        stored = self._settings.value(self.PROJECT_DIRECTORY_KEY)
        if stored:
            return Path(str(stored))
        return Path.home() / "Documents"

    def set_project_directory(self, directory: str | Path) -> None:
        path = Path(directory)
        self._settings.setValue(self.PROJECT_DIRECTORY_KEY, str(path))
        self._settings.sync()
        self._updating = True
        try:
            self.project_directory_edit.setText(str(path))
        finally:
            self._updating = False
        self.settings_changed.emit()

    def select_language(self, language: str) -> None:
        self.language_combo.setCurrentIndex(self.language_combo.findData(language))
        if self._updating:
            return
        self._apply_language(language)

    def select_log_level(self, level: str) -> None:
        self.log_level_combo.setCurrentIndex(self.log_level_combo.findData(level))
        self._apply_log_level(level)

    # ----------------------------------------------------------------- slots

    def _on_language_changed(self, _index: int) -> None:
        if self._updating:
            return
        language = self.language_combo.currentData()
        if language is not None:
            self._apply_language(str(language))

    def _apply_language(self, language: str) -> None:
        if language == self._translations.current_language:
            return
        self._translations.set_language(language)
        self.restart_label.setText(
            self.tr("The interface language changes the next time Duo Input starts.")
        )
        self.settings_changed.emit()

    def _on_directory_edited(self) -> None:
        if not self._updating:
            self.set_project_directory(self.project_directory_edit.text())

    def _on_browse_clicked(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, self.tr("Default project folder"), str(self.project_directory())
        )
        if chosen:
            self.set_project_directory(chosen)

    def _on_log_level_changed(self, _index: int) -> None:
        if self._updating:
            return
        level = self.log_level_combo.currentData()
        if level is not None:
            self._apply_log_level(str(level))

    def _apply_log_level(self, level: str) -> None:
        self._settings.setValue(self.LOG_LEVEL_KEY, level)
        self._settings.sync()
        logging.getLogger(LOGGER_NAME).setLevel(getattr(logging, level))
        self.settings_changed.emit()


__all__ = ["LANGUAGE_NAMES", "LOG_LEVELS", "SettingsPage"]
