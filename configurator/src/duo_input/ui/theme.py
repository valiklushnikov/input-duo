"""The visual language of the configurator: colour, type, spacing, states.

This module is presentation and nothing else. It knows about pixels, points
and hex triples; it has never heard of a profile, a binding or a device, and
nothing here may import from :mod:`duo_input.domain`, :mod:`duo_input.device`
or :mod:`duo_input.protocol`. Pages ask for a *role* - "this is a page title",
"this is a value that is missing" - and the stylesheet decides what that looks
like, so the look can change in one place.

The rules the design commits to are written down in
``docs/user/configurator-visual-design.md`` and asserted, where they are
measurable, in ``configurator/tests/ui/test_theme.py``.

The interface face, Golos Text, ships with the program: it is not one the
operating system has, so its files travel in ``resources/fonts`` and are
loaded from there before anything asks for the family by name. The monospace
face is still resolved rather than bundled - Windows already ships Consolas,
loading it from ``%SystemRoot%\\Fonts`` costs the installer nothing, and
Golos Text has no monospaced companion anyway. The loading matters beyond
taste: Qt's ``offscreen`` platform starts with an *empty* font database, so a
screenshot taken without this step renders every glyph as an empty box -
which is exactly what "шрифты не подключены" looks like.
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QFormLayout,
    QLabel,
    QSizePolicy,
    QStyleOption,
    QVBoxLayout,
    QWidget,
)

# --------------------------------------------------------------------- palette
#
# Neutrals carry the structure, and exactly four colours carry meaning:
# blue for "you can act on this", green for "these two agree", amber for
# "these two do not agree yet", red for "this failed or will destroy
# something". Nothing is coloured for decoration.

#: Body text: headings, values, anything the operator reads to get an answer.
INK = "#17191C"
#: Field labels and secondary text. Readable, deliberately not the loudest.
INK_MUTED = "#565B63"
#: Absence of data. Quieter than a label so ``unknown`` cannot pass for a value.
INK_FAINT = "#7C838D"

#: Cards, tables and text inputs.
SURFACE = "#FFFFFF"
#: The page behind the cards.
CANVAS = "#F2F2F0"
#: Toolbar, state strip and status bar - the frame around the pages.
CHROME = "#EDEDEA"
#: Hairlines between and around things.
LINE = "#D9D9D4"
#: The heavier line an input or a button draws around itself.
LINE_STRONG = "#C6C6C0"

#: The navigation rail: dark, so the chrome never competes with the content.
RAIL = "#22242A"
RAIL_INK = "#C3CAD4"
RAIL_INK_ACTIVE = "#FFFFFF"
RAIL_HOVER = "#2E313A"

#: Interactive. Save, selection, focus.
ACCENT = "#2A5C8A"
ACCENT_STRONG = "#22496D"
ACCENT_TINT = "#EBF0F5"

#: Agreement: saved, in sync, connected.
OK = "#16743F"
OK_TINT = "#EAF6EF"

#: Divergence: unsaved edits, a device holding something else.
WARN = "#8A6100"
WARN_TINT = "#FAF2DF"

#: Failure, and the one button that overwrites the device.
DANGER = "#C0362C"
DANGER_STRONG = "#9E2B23"
DANGER_TINT = "#FBEDEB"

#: Which wash sits behind each signal colour when it is used as a chip.
SIGNAL_TINTS = {
    ACCENT: ACCENT_TINT,
    OK: OK_TINT,
    WARN: WARN_TINT,
    DANGER: DANGER_TINT,
}

# ------------------------------------------------------------------ type scale
#
# Point sizes, so Qt converts them for whatever DPI the screen reports. The
# body size stays where Windows puts it; the scale happens above it.

#: Chips, captions and small print.
TEXT_SMALL = 8.0
#: Controls, labels and values - everything by default.
TEXT_BODY = 9.0
#: The title of one card.
TEXT_CARD_TITLE = 10.0
#: The title of a page.
TEXT_PAGE_TITLE = 15.0

#: Weights. Qt maps these onto the faces the family actually ships.
WEIGHT_NORMAL = 400
WEIGHT_MEDIUM = 600
WEIGHT_BOLD = 700

#: What the interface is set in - bundled, not borrowed from the machine.
UI_FAMILY = "Golos Text"
UI_FALLBACK_FAMILY = "Segoe UI"
#: Where digits have to line up: hashes, counters, byte sizes, IDs.
MONO_FAMILY = "Consolas"
MONO_FALLBACK_FAMILY = "monospace"

#: Font files Windows ships that the offscreen database has to be handed.
_SYSTEM_FONT_FILES = (
    "segoeui.ttf",
    "segoeuib.ttf",
    "segoeuisl.ttf",
    "seguisb.ttf",
    "consola.ttf",
    "consolab.ttf",
)

# ------------------------------------------------------------------- the grid

#: Everything that is a distance is a multiple of this.
GRID = 4
SPACE_XS, SPACE_SM, SPACE_MD, SPACE_LG, SPACE_XL = 4, 8, 12, 16, 24
SPACING = (SPACE_XS, SPACE_SM, SPACE_MD, SPACE_LG, SPACE_XL)

#: Minimum height of anything the operator clicks.
CONTROL_HEIGHT = 28
#: Corner radius, one value for everything that has corners.
RADIUS = 4
#: Cards sit a little softer than the controls they contain.
RADIUS_CARD = 6

# ------------------------------------------------------------------- the roles
#
# A page never names a colour. It names what a widget *is*, and the stylesheet
# below is the only thing that decides how that looks.

ROLE_PAGE_TITLE = "pageTitle"
ROLE_PAGE_SUBTITLE = "pageSubtitle"
ROLE_FIELD_LABEL = "fieldLabel"
ROLE_VALUE = "value"
ROLE_MONO = "mono"
ROLE_PLACEHOLDER = "placeholder"
ROLE_NOTE = "note"
ROLE_CHIP = "chip"
ROLE_BANNER = "banner"
ROLE_PRIMARY = "primary"
ROLE_DESTRUCTIVE = "destructive"

ROLES = (
    ROLE_PAGE_TITLE,
    ROLE_PAGE_SUBTITLE,
    ROLE_FIELD_LABEL,
    ROLE_VALUE,
    ROLE_MONO,
    ROLE_PLACEHOLDER,
    ROLE_NOTE,
    ROLE_CHIP,
    ROLE_BANNER,
    ROLE_PRIMARY,
    ROLE_DESTRUCTIVE,
)

SIGNAL_OK = "ok"
SIGNAL_WARN = "warn"
SIGNAL_ERROR = "error"
SIGNAL_MUTED = "muted"
SIGNAL_INFO = "info"

SIGNALS = (SIGNAL_OK, SIGNAL_WARN, SIGNAL_ERROR, SIGNAL_MUTED, SIGNAL_INFO)

#: Object names the stylesheet reaches for by name rather than by role.
NAME_RAIL = "navigationRail"
NAME_TOOLBAR = "shellToolbar"
NAME_STATE_STRIP = "shellStateStrip"

_resolved_families: tuple[str, str] | None = None


#: Where the two image assets the stylesheet needs live on disk.
ASSET_DIRECTORY = Path(__file__).resolve().parent.parent / "resources"

#: The one drawn asset: a chevron for the combo boxes, at 1x and 2x.
CHEVRON_ASSET = "chevron-down.png"
CHEVRON_UP_ASSET = "chevron-up.png"


def asset_url(name: str) -> str:
    """A stylesheet ``url(...)`` for one shipped asset, or ``""`` if it is gone.

    Qt style sheets take filesystem paths with forward slashes, and Qt picks
    the ``@2x`` file up on its own where the screen asks for it. A build that
    somehow lost the file falls back to the platform's own arrow rather than
    drawing a combo box with nothing in its corner.
    """
    path = ASSET_DIRECTORY / name
    return f"url({path.as_posix()})" if path.is_file() else ""


def rgb(colour: str) -> tuple[int, int, int]:
    """The three channels of a ``#rrggbb`` string."""
    text = colour.lstrip("#")
    if len(text) != 6:
        raise ValueError(f"{colour!r} is not a #rrggbb colour")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


# ------------------------------------------------------------------- fonts


def bundled_font_files() -> tuple[Path, ...]:
    """The interface faces that travel with the program, lowest weight first."""
    directory = Path(__file__).resolve().parent.parent / "resources" / "fonts"
    return tuple(sorted(directory.glob("*.ttf")))


def install_fonts() -> tuple[str, str]:
    """Make the interface and monospace families available, and name them.

    The interface face ships with the program: it is not one the operating
    system has, so it is loaded from ``resources/fonts`` before anything asks
    for it. The monospace face is still the system's own Consolas, because
    Golos Text has no monospaced companion and hashes only need to line up.

    Nothing here may fail loudly. A font file that did not travel leaves the
    fallbacks in place and the layout still holds - a blank interface is a
    worse outcome than the wrong typeface.
    """
    global _resolved_families
    if _resolved_families is not None:
        return _resolved_families

    for path in bundled_font_files():
        QFontDatabase.addApplicationFont(str(path))

    families = set(QFontDatabase.families())
    if MONO_FAMILY not in families:
        directory = Path(os.environ.get("SystemRoot", "C:/Windows")) / "Fonts"
        for name in _SYSTEM_FONT_FILES:
            candidate = directory / name
            if candidate.is_file():
                QFontDatabase.addApplicationFont(str(candidate))
        families = set(QFontDatabase.families())

    if UI_FAMILY in families:
        interface = UI_FAMILY
    elif UI_FALLBACK_FAMILY in families:
        interface = UI_FALLBACK_FAMILY
    else:
        interface = _generic(QFontDatabase.SystemFont.GeneralFont, UI_FALLBACK_FAMILY)
    fixed = MONO_FAMILY if MONO_FAMILY in families else _generic(
        QFontDatabase.SystemFont.FixedFont, MONO_FALLBACK_FAMILY
    )
    _resolved_families = (interface, fixed)
    return _resolved_families


def _generic(kind: QFontDatabase.SystemFont, fallback: str) -> str:
    """Whatever this machine calls its general or fixed-width face."""
    family = QFontDatabase.systemFont(kind).family()
    return family or fallback


def interface_font(
    size: float = TEXT_BODY, weight: int = WEIGHT_NORMAL, italic: bool = False
) -> QFont:
    """One font from the scale, in the resolved interface family."""
    font = QFont(install_fonts()[0])
    font.setPointSizeF(size)
    font.setWeight(QFont.Weight(weight))
    font.setItalic(italic)
    return font


def monospace_font(size: float = TEXT_BODY) -> QFont:
    """The face used wherever digits have to line up under each other."""
    font = QFont(install_fonts()[1])
    font.setPointSizeF(size)
    return font


# -------------------------------------------------------------- marking widgets


def _repolish(widget: QWidget) -> None:
    """Make Qt re-read the dynamic properties the stylesheet selects on."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_role(widget: QWidget, role: str | None) -> None:
    """Declare what ``widget`` is, so the stylesheet can dress it."""
    widget.setProperty("role", role or "")
    _repolish(widget)


def set_signal(widget: QWidget, signal: str | None) -> None:
    """Declare what ``widget`` currently *means*: ok, warn, error, muted, info."""
    widget.setProperty("signal", signal or "")
    _repolish(widget)


def mark_placeholder(widget: QWidget, missing: bool, role: str = ROLE_VALUE) -> None:
    """Show a value, or show that there is no value to show.

    A missing value is not a value in a lighter colour; it is a different kind
    of thing, and it says so by changing role rather than by changing text.
    """
    set_role(widget, ROLE_PLACEHOLDER if missing else role)


# ----------------------------------------------------------------- widgets


class ElidingLabel(QLabel):
    """A label that shrinks to fit and never widens the layout around it.

    A SHA-256 digest is sixty-four characters with nowhere to wrap, so a plain
    ``QLabel`` holding one demands about six hundred pixels and pushes a
    horizontal scrollbar under the whole page. This paints the middle out
    instead - the ends are what anyone compares - while ``text()`` keeps
    returning the digest in full, for the tooltip, for the clipboard, and for
    anything that reads the value rather than looks at it.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        # Expanding, not Ignored: a form layout hands an ignored field zero
        # width and the digest disappears entirely instead of being shortened.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self._sync_tooltip(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt override
        super().setText(text)
        self._sync_tooltip(text)

    def _sync_tooltip(self, text: str) -> None:
        # A tooltip repeating an empty value would be a tooltip about nothing.
        self.setToolTip(text or "")

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(0, super().minimumSizeHint().height())

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(0, super().sizeHint().height())

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        option = QStyleOption()
        option.initFrom(self)
        painter = QPainter(self)
        metrics = QFontMetrics(self.font())
        elided = metrics.elidedText(self.text(), Qt.TextElideMode.ElideMiddle, self.width())
        self.style().drawItemText(
            painter,
            self.contentsRect(),
            int(self.alignment()),
            option.palette,
            self.isEnabled(),
            elided,
            self.foregroundRole(),
        )


def page_header(title: str, subtitle: str = "", parent: QWidget | None = None) -> QWidget:
    """The two lines every page opens with.

    The title repeats the word in the navigation rail on purpose - it is the
    anchor for the eye once the rail is out of focus - and the line under it
    says what the page is *for*, which the rail has no room to.
    """
    holder = QWidget(parent)
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(SPACE_XS)

    heading = QLabel(title, holder)
    set_role(heading, ROLE_PAGE_TITLE)
    layout.addWidget(heading)

    if subtitle:
        caption = QLabel(subtitle, holder)
        caption.setWordWrap(True)
        set_role(caption, ROLE_PAGE_SUBTITLE)
        layout.addWidget(caption)
    return holder


def fact_form() -> QFormLayout:
    """A label-and-value table: one column of names, one of answers.

    Every fact table in the program uses this, so a row on Overview and a row
    on Diagnostics sit at the same height with the same gap beside them and
    the eye can run down a column without re-aiming.
    """
    form = QFormLayout()
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    form.setHorizontalSpacing(SPACE_XL)
    form.setVerticalSpacing(SPACE_SM)
    form.setContentsMargins(0, 0, 0, 0)
    return form


def field_label(text: str, parent: QWidget | None = None) -> QLabel:
    """The name half of a fact row."""
    label = QLabel(text, parent)
    set_role(label, ROLE_FIELD_LABEL)
    return label


# -------------------------------------------------------------- the stylesheet


def build_stylesheet(interface_family: str, mono_family: str) -> str:
    """The whole look, as one Qt style sheet."""
    chevron = asset_url(CHEVRON_ASSET)
    chevron_up = asset_url(CHEVRON_UP_ASSET)
    # Styling the drop-down at all replaces the platform's arrow, so the rule
    # is only written when there is something to put back in its place.
    chevron_rules = (
        f"""
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: {SPACE_XL}px;
    border: none;
    background: transparent;
}}
QComboBox::down-arrow {{ image: {chevron}; width: {SPACE_MD}px; height: {SPACE_MD}px; }}
QSpinBox::up-button, QSpinBox::down-button {{
    subcontrol-origin: border;
    width: {SPACE_XL}px;
    border: none;
    background: transparent;
}}
QSpinBox::up-button {{ subcontrol-position: top right; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; }}
QSpinBox::up-arrow {{ image: {chevron_up}; width: {SPACE_MD}px; height: {SPACE_MD}px; }}
QSpinBox::down-arrow {{ image: {chevron}; width: {SPACE_MD}px; height: {SPACE_MD}px; }}
"""
        if chevron and chevron_up
        else ""
    )
    return f"""
/* ------------------------------------------------------------- foundation */
QWidget {{
    font-family: "{interface_family}";
    font-size: {TEXT_BODY}pt;
    color: {INK};
}}
QMainWindow, QDialog {{ background-color: {CANVAS}; }}
QStackedWidget, QStackedWidget > QWidget {{ background-color: {CANVAS}; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QSplitter::handle {{ background-color: {CANVAS}; width: {GRID}px; }}
QToolTip {{
    background-color: {INK};
    color: {SURFACE};
    border: none;
    padding: {SPACE_XS}px {SPACE_SM}px;
}}

/* ------------------------------------------------------------------ chrome */
QFrame#{NAME_TOOLBAR} {{
    background-color: {CHROME};
    border-bottom: 1px solid {LINE};
}}
QFrame#{NAME_STATE_STRIP} {{
    background-color: {CHROME};
    border-bottom: 1px solid {LINE};
}}
QStatusBar {{
    background-color: {CHROME};
    border-top: 1px solid {LINE};
    color: {INK_MUTED};
}}
QStatusBar::item {{ border: none; }}

/* -------------------------------------------------------- navigation rail */
QListWidget#{NAME_RAIL} {{
    background-color: {RAIL};
    border: none;
    outline: none;
    padding: {SPACE_SM}px 0px;
}}
QListWidget#{NAME_RAIL}::item {{
    color: {RAIL_INK};
    padding: {SPACE_SM}px {SPACE_MD}px;
    margin: 0px {SPACE_SM}px;
    border-radius: {RADIUS}px;
    min-height: {CONTROL_HEIGHT - 2 * SPACE_SM}px;
}}
QListWidget#{NAME_RAIL}::item:hover {{
    background-color: {RAIL_HOVER};
    color: {RAIL_INK_ACTIVE};
}}
QListWidget#{NAME_RAIL}::item:selected {{
    background-color: {ACCENT};
    color: {RAIL_INK_ACTIVE};
    font-weight: {WEIGHT_MEDIUM};
}}

/* ------------------------------------------------------------------- cards */
QGroupBox {{
    background-color: {SURFACE};
    border: 1px solid {LINE};
    border-radius: {RADIUS_CARD}px;
    margin-top: 0px;
    padding: {2 * SPACE_XL - SPACE_SM}px {SPACE_LG}px {SPACE_LG}px {SPACE_LG}px;
    font-size: {TEXT_CARD_TITLE}pt;
    font-weight: {WEIGHT_MEDIUM};
}}
QGroupBox::title {{
    subcontrol-origin: border;
    subcontrol-position: top left;
    left: {SPACE_LG}px;
    top: {SPACE_MD}px;
    padding: 0px;
    color: {INK};
    background: transparent;
}}

/* ------------------------------------------------------------------- text */
QLabel {{ background: transparent; }}
QLabel[role="{ROLE_PAGE_TITLE}"] {{
    font-size: {TEXT_PAGE_TITLE}pt;
    font-weight: {WEIGHT_MEDIUM};
    color: {INK};
}}
QLabel[role="{ROLE_PAGE_SUBTITLE}"] {{
    font-size: {TEXT_BODY}pt;
    color: {INK_MUTED};
}}
QLabel[role="{ROLE_FIELD_LABEL}"] {{ color: {INK_MUTED}; }}
QLabel[role="{ROLE_VALUE}"] {{ color: {INK}; }}
QLabel[role="{ROLE_MONO}"] {{
    font-family: "{mono_family}";
    color: {INK};
}}
QLabel[role="{ROLE_PLACEHOLDER}"] {{
    color: {INK_FAINT};
    font-style: italic;
}}
QLabel[role="{ROLE_NOTE}"] {{
    font-size: {TEXT_SMALL}pt;
    color: {INK_MUTED};
}}

/* ------------------------------------------------------- state chips */
QLabel[role="{ROLE_CHIP}"] {{
    font-size: {TEXT_SMALL}pt;
    color: {INK_MUTED};
    background-color: {SURFACE};
    border: 1px solid {LINE};
    border-radius: {RADIUS}px;
    padding: {SPACE_XS}px {SPACE_SM}px;
}}
QLabel[role="{ROLE_CHIP}"][signal="{SIGNAL_OK}"] {{
    color: {OK}; background-color: {OK_TINT}; border-color: {OK};
}}
QLabel[role="{ROLE_CHIP}"][signal="{SIGNAL_WARN}"] {{
    color: {WARN}; background-color: {WARN_TINT}; border-color: {WARN};
}}
QLabel[role="{ROLE_CHIP}"][signal="{SIGNAL_ERROR}"] {{
    color: {DANGER}; background-color: {DANGER_TINT}; border-color: {DANGER};
}}
QLabel[role="{ROLE_CHIP}"][signal="{SIGNAL_INFO}"] {{
    color: {ACCENT}; background-color: {ACCENT_TINT}; border-color: {ACCENT};
}}
QLabel[role="{ROLE_CHIP}"][signal="{SIGNAL_MUTED}"] {{
    color: {INK_FAINT}; background-color: {SURFACE}; border-color: {LINE};
}}

/* ------------------------------------------------- inline explanation band */
QLabel[role="{ROLE_BANNER}"] {{
    background-color: {CANVAS};
    border: 1px solid {LINE};
    border-radius: {RADIUS}px;
    padding: {SPACE_SM}px {SPACE_MD}px;
    color: {INK_MUTED};
}}
QLabel[role="{ROLE_BANNER}"][signal="{SIGNAL_WARN}"] {{
    color: {WARN}; background-color: {WARN_TINT}; border-color: {WARN};
}}
QLabel[role="{ROLE_BANNER}"][signal="{SIGNAL_ERROR}"] {{
    color: {DANGER}; background-color: {DANGER_TINT}; border-color: {DANGER};
}}
QLabel[role="{ROLE_BANNER}"][signal="{SIGNAL_INFO}"] {{
    color: {ACCENT}; background-color: {ACCENT_TINT}; border-color: {ACCENT};
}}
QLabel[role="{ROLE_BANNER}"][signal="{SIGNAL_OK}"] {{
    color: {OK}; background-color: {OK_TINT}; border-color: {OK};
}}
QLabel[role="{ROLE_BANNER}"][signal="{SIGNAL_MUTED}"] {{
    color: {INK_FAINT};
}}

/* --------------------------------------------------- values that mean more */
QLabel[signal="{SIGNAL_OK}"] {{ color: {OK}; }}
QLabel[signal="{SIGNAL_WARN}"] {{ color: {WARN}; }}
QLabel[signal="{SIGNAL_ERROR}"] {{ color: {DANGER}; }}
QLabel[signal="{SIGNAL_MUTED}"] {{ color: {INK_FAINT}; }}
QLabel[signal="{SIGNAL_INFO}"] {{ color: {ACCENT}; }}

/* ----------------------------------------------------------------- buttons */
QPushButton {{
    background-color: {SURFACE};
    border: 1px solid {LINE_STRONG};
    border-radius: {RADIUS}px;
    padding: {SPACE_XS}px {SPACE_MD}px;
    min-height: {CONTROL_HEIGHT - 2 * SPACE_XS - 2}px;
    color: {INK};
}}
QPushButton:hover {{ background-color: {ACCENT_TINT}; border-color: {ACCENT}; }}
QPushButton:pressed {{ background-color: {LINE}; border-color: {LINE_STRONG}; }}
QPushButton:focus {{ border: 2px solid {ACCENT}; padding: {SPACE_XS - 1}px {SPACE_MD - 1}px; }}
QPushButton:disabled {{
    background-color: {CANVAS};
    border-color: {LINE};
    color: {INK_FAINT};
}}
QPushButton[role="{ROLE_PRIMARY}"] {{
    background-color: {ACCENT};
    border-color: {ACCENT};
    color: {SURFACE};
    font-weight: {WEIGHT_MEDIUM};
}}
QPushButton[role="{ROLE_PRIMARY}"]:hover {{ background-color: {ACCENT_STRONG}; border-color: {ACCENT_STRONG}; }}
QPushButton[role="{ROLE_PRIMARY}"]:pressed {{ background-color: {ACCENT_STRONG}; border-color: {INK}; }}
QPushButton[role="{ROLE_PRIMARY}"]:disabled {{
    background-color: {CANVAS}; border-color: {LINE}; color: {INK_FAINT};
}}
QPushButton[role="{ROLE_DESTRUCTIVE}"] {{
    background-color: {DANGER};
    border-color: {DANGER_STRONG};
    color: {SURFACE};
    font-weight: {WEIGHT_BOLD};
    padding: {SPACE_XS}px {SPACE_LG}px;
}}
QPushButton[role="{ROLE_DESTRUCTIVE}"]:hover {{ background-color: {DANGER_STRONG}; border-color: {DANGER_STRONG}; }}
QPushButton[role="{ROLE_DESTRUCTIVE}"]:pressed {{ background-color: {DANGER_STRONG}; border-color: {INK}; }}
QPushButton[role="{ROLE_DESTRUCTIVE}"]:disabled {{
    background-color: {CANVAS}; border-color: {LINE}; color: {INK_FAINT}; font-weight: {WEIGHT_NORMAL};
}}

/* ------------------------------------------------------------------ inputs */
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {{
    background-color: {SURFACE};
    border: 1px solid {LINE_STRONG};
    border-radius: {RADIUS}px;
    padding: {SPACE_XS}px {SPACE_SM}px;
    min-height: {CONTROL_HEIGHT - 2 * SPACE_XS - 2}px;
    selection-background-color: {ACCENT};
    selection-color: {SURFACE};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{
    border: 1px solid {ACCENT};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    background-color: {CANVAS};
    color: {INK_FAINT};
    border-color: {LINE};
}}
{chevron_rules}
QComboBox QAbstractItemView {{
    background-color: {SURFACE};
    border: 1px solid {LINE_STRONG};
    selection-background-color: {ACCENT};
    selection-color: {SURFACE};
    outline: none;
}}
QCheckBox {{ spacing: {SPACE_SM}px; min-height: {CONTROL_HEIGHT - SPACE_SM}px; }}
QCheckBox:disabled {{ color: {INK_FAINT}; }}

/* ------------------------------------------------------------------- lists */
QListWidget, QListView, QTableView, QTreeView {{
    background-color: {SURFACE};
    border: 1px solid {LINE};
    border-radius: {RADIUS}px;
    outline: none;
    alternate-background-color: {CANVAS};
}}
QListWidget::item, QListView::item {{
    padding: {SPACE_XS}px {SPACE_SM}px;
    border-radius: {RADIUS - 2}px;
}}
QListWidget::item:hover, QListView::item:hover, QTableView::item:hover {{
    background-color: {ACCENT_TINT};
}}
QListWidget::item:selected, QListView::item:selected, QTableView::item:selected {{
    background-color: {ACCENT};
    color: {SURFACE};
}}
QTableView {{ gridline-color: {LINE}; }}
QHeaderView::section {{
    background-color: {CHROME};
    color: {INK_MUTED};
    border: none;
    border-bottom: 1px solid {LINE};
    border-right: 1px solid {LINE};
    padding: {SPACE_SM}px;
    font-size: {TEXT_SMALL}pt;
    font-weight: {WEIGHT_MEDIUM};
}}
QHeaderView::section:last {{ border-right: none; }}
QTableCornerButton::section {{ background-color: {CHROME}; border: none; }}

/* -------------------------------------------------------------- scrollbars */
QScrollBar:vertical {{
    background: transparent; width: {SPACE_MD}px; margin: 0px;
}}
QScrollBar:horizontal {{
    background: transparent; height: {SPACE_MD}px; margin: 0px;
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background-color: {LINE_STRONG};
    border-radius: {RADIUS - 2}px;
    min-height: {SPACE_XL}px;
    min-width: {SPACE_XL}px;
}}
QScrollBar::handle:hover {{ background-color: {INK_FAINT}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0px; width: 0px; border: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---------------------------------------------------------------- progress */
QProgressBar {{
    background-color: {SURFACE};
    border: 1px solid {LINE};
    border-radius: {RADIUS}px;
    height: {SPACE_SM}px;
    text-align: center;
    color: {INK_MUTED};
    font-size: {TEXT_SMALL}pt;
}}
QProgressBar::chunk {{ background-color: {ACCENT}; border-radius: {RADIUS - 1}px; }}
"""


def apply_theme(application) -> None:
    """Dress ``application``: resolve the fonts, then install the stylesheet."""
    interface, mono = install_fonts()
    application.setFont(interface_font())
    application.setStyleSheet(build_stylesheet(interface, mono))


__all__ = [
    "ACCENT",
    "CANVAS",
    "CONTROL_HEIGHT",
    "DANGER",
    "ElidingLabel",
    "GRID",
    "INK",
    "INK_FAINT",
    "INK_MUTED",
    "NAME_RAIL",
    "NAME_STATE_STRIP",
    "NAME_TOOLBAR",
    "OK",
    "ROLES",
    "ROLE_BANNER",
    "ROLE_CHIP",
    "ROLE_DESTRUCTIVE",
    "ROLE_FIELD_LABEL",
    "ROLE_MONO",
    "ROLE_NOTE",
    "ROLE_PAGE_SUBTITLE",
    "ROLE_PAGE_TITLE",
    "ROLE_PLACEHOLDER",
    "ROLE_PRIMARY",
    "ROLE_VALUE",
    "SIGNALS",
    "SIGNAL_ERROR",
    "SIGNAL_INFO",
    "SIGNAL_MUTED",
    "SIGNAL_OK",
    "SIGNAL_WARN",
    "SPACE_LG",
    "SPACE_MD",
    "SPACE_SM",
    "SPACE_XL",
    "SPACE_XS",
    "SPACING",
    "SURFACE",
    "TEXT_BODY",
    "TEXT_CARD_TITLE",
    "TEXT_PAGE_TITLE",
    "TEXT_SMALL",
    "WARN",
    "apply_theme",
    "build_stylesheet",
    "fact_form",
    "field_label",
    "install_fonts",
    "interface_font",
    "mark_placeholder",
    "monospace_font",
    "page_header",
    "rgb",
    "set_role",
    "set_signal",
]
