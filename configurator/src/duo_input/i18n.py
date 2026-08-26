"""Which language the interface speaks, and where the catalogues live.

Russian is the default: this is a tool for Russian-speaking operators, and an
English interface on first run would be a worse guess than a Russian one. The
choice is remembered in ``QSettings`` and applied at startup.

Protocol identifiers are never routed through here. ``TOGGLE_MOUSE_ROUTE``,
``write_config`` and the numeric error codes read the same in every language,
because a screenshot or a diagnostic report has to mean the same thing to
whoever reads it next.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, QSettings, QTranslator, Signal

#: Languages this build ships, in the order the settings page offers them.
LANGUAGES: tuple[str, ...] = ("ru", "en")

#: What a first run gets.
DEFAULT_LANGUAGE = "ru"

SETTINGS_LANGUAGE_KEY = "interface/language"

_CATALOGUE_STEM = "duo_input_"


def translations_directory() -> Path:
    return Path(__file__).resolve().parent / "resources" / "translations"


def catalogue_path(language: str) -> Path:
    """The compiled ``.qm`` for ``language``."""
    return translations_directory() / f"{_CATALOGUE_STEM}{language}.qm"


class TranslationManager(QObject):
    """Installs one catalogue at a time on the running application."""

    language_changed = Signal(str)

    def __init__(
        self,
        application: QCoreApplication | None = None,
        settings: QSettings | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._application = application or QCoreApplication.instance()
        self._settings = settings if settings is not None else QSettings()
        self._translator: QTranslator | None = None
        self._language = DEFAULT_LANGUAGE

    @property
    def current_language(self) -> str:
        return self._language

    @property
    def settings(self) -> QSettings:
        return self._settings

    def saved_language(self) -> str:
        """The remembered choice, or the default when it is not one we ship."""
        stored = self._settings.value(SETTINGS_LANGUAGE_KEY)
        text = str(stored) if stored is not None else ""
        return text if text in LANGUAGES else DEFAULT_LANGUAGE

    def load_saved(self) -> str:
        """Apply the remembered language and return which one that was."""
        language = self.saved_language()
        self.set_language(language, remember=False)
        return language

    def set_language(self, language: str, remember: bool = True) -> bool:
        """Install ``language``. Returns whether a catalogue was actually loaded.

        A missing catalogue is not an error the operator can fix, and it must
        not leave the program without an interface: the source English is used
        and the choice is still remembered.
        """
        if language not in LANGUAGES:
            raise ValueError(f"{language!r} is not a language this build ships")
        if remember:
            self._settings.setValue(SETTINGS_LANGUAGE_KEY, language)
            self._settings.sync()
        self._language = language

        if self._application is None:
            return False
        if self._translator is not None:
            self._application.removeTranslator(self._translator)
            self._translator = None

        catalogue = catalogue_path(language)
        translator = QTranslator(self)
        if not catalogue.is_file() or not translator.load(str(catalogue)):
            self.language_changed.emit(language)
            return False
        self._application.installTranslator(translator)
        self._translator = translator
        self.language_changed.emit(language)
        return True


__all__ = [
    "DEFAULT_LANGUAGE",
    "LANGUAGES",
    "SETTINGS_LANGUAGE_KEY",
    "TranslationManager",
    "catalogue_path",
    "translations_directory",
]
