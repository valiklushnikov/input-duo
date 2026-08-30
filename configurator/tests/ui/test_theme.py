"""The visual language: what it promises, checked as numbers.

A palette and a type scale are design decisions, but the properties that make
them *usable* are measurable, so they are asserted here rather than left to
whoever next opens a screenshot: text meets WCAG AA against the surface it sits
on, the type scale is strictly increasing, every spacing step is on the grid,
and the three project states of specification section 18.5 get three colours
that are actually distinguishable.
"""

from __future__ import annotations

import pytest

from duo_input.ui import theme

# ------------------------------------------------------------------ contrast


def _channel(value: int) -> float:
    """One sRGB channel, linearised as WCAG 2.1 defines it."""
    fraction = value / 255.0
    if fraction <= 0.04045:
        return fraction / 12.92
    return ((fraction + 0.055) / 1.055) ** 2.4


def _luminance(colour: str) -> float:
    red, green, blue = theme.rgb(colour)
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def contrast(foreground: str, background: str) -> float:
    """WCAG 2.1 contrast ratio between two ``#rrggbb`` colours."""
    first, second = _luminance(foreground), _luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def test_the_contrast_helper_agrees_with_the_two_extremes():
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0, abs=0.01)
    assert contrast("#ffffff", "#ffffff") == pytest.approx(1.0, abs=0.01)


# ------------------------------------------------------------------- palette


@pytest.mark.parametrize("ink", (theme.INK, theme.INK_MUTED))
@pytest.mark.parametrize("ground", (theme.SURFACE, theme.CANVAS, theme.CHROME))
def test_text_meets_wcag_aa_on_every_ground_it_is_used_on(ink, ground):
    assert contrast(ink, ground) >= 4.5


@pytest.mark.parametrize("ground", (theme.SURFACE, theme.CANVAS, theme.CHROME))
def test_the_placeholder_ink_stays_above_the_non_text_floor(ground):
    """``unknown`` is not information to read, so AA large (3:1) is the bar.

    It still has to be visible - an absence the operator cannot see at all is
    indistinguishable from a page that failed to draw.
    """
    assert contrast(theme.INK_FAINT, ground) >= 3.0


@pytest.mark.parametrize(
    "signal", (theme.ACCENT, theme.OK, theme.WARN, theme.DANGER)
)
def test_every_signal_colour_is_readable_as_text_on_its_own_tint(signal):
    tint = theme.SIGNAL_TINTS[signal]

    assert contrast(signal, tint) >= 4.5


@pytest.mark.parametrize("signal", (theme.ACCENT, theme.DANGER, theme.OK))
def test_a_filled_button_carries_white_text(signal):
    assert contrast("#ffffff", signal) >= 4.5


def test_the_navigation_rail_is_readable_and_its_selection_stands_out():
    assert contrast(theme.RAIL_INK, theme.RAIL) >= 4.5
    assert contrast(theme.RAIL_INK_ACTIVE, theme.ACCENT) >= 4.5


def _distance(first: str, second: str) -> float:
    """Weighted sRGB distance ("redmean"), a cheap stand-in for perception.

    Contrast alone will not do here: green, amber and red are all pinned to
    roughly one luminance by the AA requirement above, so they differ in hue
    rather than in lightness, and a luminance ratio would call them identical.
    """
    red_a, green_a, blue_a = theme.rgb(first)
    red_b, green_b, blue_b = theme.rgb(second)
    mean_red = (red_a + red_b) / 2
    return (
        (2 + mean_red / 256) * (red_a - red_b) ** 2
        + 4 * (green_a - green_b) ** 2
        + (2 + (255 - mean_red) / 256) * (blue_a - blue_b) ** 2
    ) ** 0.5


@pytest.mark.parametrize(
    ("first", "second"),
    (
        (theme.OK, theme.WARN),
        (theme.WARN, theme.DANGER),
        (theme.OK, theme.DANGER),
        (theme.ACCENT, theme.OK),
    ),
)
def test_two_signals_are_never_mistaken_for_one_another(first, second):
    assert _distance(first, second) >= 100


def test_the_accent_is_never_mistaken_for_the_destructive_colour():
    """Blue means "you may act"; red means "this overwrites the device".

    An accent that reads as red turns every ordinary button into the one
    button the operator is supposed to hesitate over.
    """
    assert _distance(theme.ACCENT, theme.DANGER) >= 100


def test_a_signal_is_never_carried_by_colour_alone():
    """Every signal has a tint behind it as well as a colour on the text."""
    tints = {theme.SIGNAL_TINTS[signal] for signal in (theme.OK, theme.WARN, theme.DANGER, theme.ACCENT)}

    assert len(tints) == 4


def test_faint_ink_is_quieter_than_the_labels_and_the_values():
    """``unknown`` must read as absence, so it may not be as loud as a value."""
    assert contrast(theme.INK_FAINT, theme.SURFACE) < contrast(theme.INK_MUTED, theme.SURFACE)
    assert contrast(theme.INK_MUTED, theme.SURFACE) < contrast(theme.INK, theme.SURFACE)


def test_every_declared_colour_is_a_six_digit_hex_triple():
    for name, value in vars(theme).items():
        if name.isupper() and isinstance(value, str) and value.startswith("#"):
            assert len(value) == 7, name
            theme.rgb(value)


# ---------------------------------------------------------------- type scale


def test_the_type_scale_is_strictly_increasing():
    steps = (
        theme.TEXT_SMALL,
        theme.TEXT_BODY,
        theme.TEXT_CARD_TITLE,
        theme.TEXT_PAGE_TITLE,
    )

    assert list(steps) == sorted(steps)
    assert len(set(steps)) == len(steps)


def test_the_page_title_is_clearly_a_heading_not_a_bigger_label():
    assert theme.TEXT_PAGE_TITLE >= theme.TEXT_BODY * 1.5


def test_the_body_size_stays_close_to_what_windows_uses():
    """Deliberate does not mean loud: controls stay near the system size."""
    assert 8.0 <= theme.TEXT_BODY <= 10.0


# ------------------------------------------------------------------ the grid


def test_every_spacing_step_sits_on_the_four_pixel_grid():
    for step in theme.SPACING:
        assert step % theme.GRID == 0, step


def test_the_spacing_steps_are_ordered_and_distinct():
    assert list(theme.SPACING) == sorted(set(theme.SPACING))


def test_a_control_is_tall_enough_to_hit():
    assert theme.CONTROL_HEIGHT >= 28
    assert theme.CONTROL_HEIGHT % theme.GRID == 0


def test_a_card_is_rounder_than_the_controls_inside_it():
    """A card that shares its radius with its own inputs reads as one slab."""
    assert theme.RADIUS_CARD > theme.RADIUS
    assert theme.RADIUS_CARD - theme.RADIUS <= 4


# ------------------------------------------------------------------ stylesheet


def test_the_stylesheet_names_every_palette_colour_it_was_given():
    sheet = theme.build_stylesheet(theme.UI_FALLBACK_FAMILY, theme.MONO_FALLBACK_FAMILY)

    for colour in (theme.CANVAS, theme.SURFACE, theme.RAIL, theme.ACCENT, theme.DANGER):
        assert colour in sheet


def test_the_stylesheet_carries_the_resolved_font_families():
    sheet = theme.build_stylesheet("Segoe UI", "Consolas")

    assert "Segoe UI" in sheet
    assert "Consolas" in sheet


def test_applying_the_theme_dresses_the_application(qapp):
    theme.apply_theme(qapp)

    assert qapp.styleSheet()
    assert qapp.font().pointSizeF() == pytest.approx(theme.TEXT_BODY, abs=0.5)


def test_the_roles_the_pages_use_all_appear_in_the_stylesheet():
    """A role a page sets but the sheet ignores would be silently dead."""
    sheet = theme.build_stylesheet(theme.UI_FALLBACK_FAMILY, theme.MONO_FALLBACK_FAMILY)

    for role in theme.ROLES:
        assert f'role="{role}"' in sheet, role


def test_the_signal_states_the_pages_use_all_appear_in_the_stylesheet():
    sheet = theme.build_stylesheet(theme.UI_FALLBACK_FAMILY, theme.MONO_FALLBACK_FAMILY)

    for state in theme.SIGNALS:
        assert f'signal="{state}"' in sheet, state


# -------------------------------------------------------------- role marking


def test_marking_a_role_survives_being_changed_twice(qtbot):
    from PySide6.QtWidgets import QLabel

    label = QLabel("x")
    qtbot.addWidget(label)

    theme.set_role(label, theme.ROLE_VALUE)
    assert label.property("role") == theme.ROLE_VALUE

    theme.set_role(label, theme.ROLE_PLACEHOLDER)
    assert label.property("role") == theme.ROLE_PLACEHOLDER


def test_a_signal_can_be_cleared_back_to_no_signal(qtbot):
    from PySide6.QtWidgets import QLabel

    label = QLabel("x")
    qtbot.addWidget(label)

    theme.set_signal(label, theme.SIGNAL_WARN)
    assert label.property("signal") == theme.SIGNAL_WARN

    theme.set_signal(label, None)
    assert not label.property("signal")


# --------------------------------------------------------------- eliding label


def test_a_hash_label_keeps_its_whole_text_however_narrow_it_gets(qtbot):
    """The layout may shrink it; ``text()`` is still the hash it was given."""
    digest = "a" * 32 + "b" * 32

    label = theme.ElidingLabel(digest)
    qtbot.addWidget(label)
    label.resize(60, 20)

    assert label.text() == digest
    assert label.toolTip() == digest
    assert label.minimumSizeHint().width() <= 60


def test_a_hash_label_does_not_widen_the_layout_it_sits_in(qtbot):
    from PySide6.QtWidgets import QHBoxLayout, QWidget

    holder = QWidget()
    qtbot.addWidget(holder)
    layout = QHBoxLayout(holder)
    layout.addWidget(theme.ElidingLabel("f" * 64))

    assert holder.sizeHint().width() < 200


def test_setting_new_text_updates_the_tooltip(qtbot):
    label = theme.ElidingLabel("first")
    qtbot.addWidget(label)

    label.setText("second")

    assert label.text() == "second"
    assert label.toolTip() == "second"


def test_a_placeholder_hash_label_carries_no_tooltip(qtbot):
    """There is nothing to reveal behind a value that is not there."""
    label = theme.ElidingLabel("")
    qtbot.addWidget(label)

    assert label.toolTip() == ""


# ------------------------------------------------------------------- fonts


def test_the_resolved_families_are_ones_qt_can_actually_use(qapp):
    from PySide6.QtGui import QFontDatabase

    ui_family, mono_family = theme.install_fonts()
    families = set(QFontDatabase.families())

    assert ui_family in families or not families
    assert mono_family in families or not families


def test_resolving_fonts_twice_returns_the_same_answer(qapp):
    assert theme.install_fonts() == theme.install_fonts()


# ------------------------------------------------------------------- assets


def test_the_chevron_the_combo_boxes_need_actually_ships():
    """Styling the drop-down replaces the platform arrow; something must return."""
    assert (theme.ASSET_DIRECTORY / theme.CHEVRON_ASSET).is_file()
    assert (theme.ASSET_DIRECTORY / "chevron-down@2x.png").is_file()
    assert (theme.ASSET_DIRECTORY / theme.CHEVRON_UP_ASSET).is_file()
    assert (theme.ASSET_DIRECTORY / "chevron-up@2x.png").is_file()


def test_the_stylesheet_points_the_drop_down_at_that_chevron():
    sheet = theme.build_stylesheet(theme.UI_FALLBACK_FAMILY, theme.MONO_FALLBACK_FAMILY)

    assert theme.CHEVRON_ASSET in sheet
    assert "QComboBox::down-arrow" in sheet


def test_a_missing_asset_leaves_the_platform_arrow_alone(monkeypatch, tmp_path):
    monkeypatch.setattr(theme, "ASSET_DIRECTORY", tmp_path)

    sheet = theme.build_stylesheet(theme.UI_FALLBACK_FAMILY, theme.MONO_FALLBACK_FAMILY)

    assert "QComboBox::drop-down" not in sheet
    assert "QComboBox::down-arrow" not in sheet
    assert "QSpinBox::up-arrow" not in sheet


def test_the_drawn_assets_stay_small_enough_not_to_matter():
    """Two chevrons may not become a reason the installer grew."""
    total = sum(
        path.stat().st_size for path in theme.ASSET_DIRECTORY.glob("chevron-*.png")
    )

    assert total < 4096


# ------------------------------------------------------------- page furniture


def test_a_page_header_is_a_title_over_a_caption(qtbot):
    header = theme.page_header("Overview", "What the device reports.")
    qtbot.addWidget(header)

    from PySide6.QtWidgets import QLabel

    labels = header.findChildren(QLabel)
    assert [label.text() for label in labels] == ["Overview", "What the device reports."]
    assert labels[0].property("role") == theme.ROLE_PAGE_TITLE
    assert labels[1].property("role") == theme.ROLE_PAGE_SUBTITLE


def test_a_page_header_without_a_caption_is_just_the_title(qtbot):
    header = theme.page_header("Settings")
    qtbot.addWidget(header)

    from PySide6.QtWidgets import QLabel

    assert len(header.findChildren(QLabel)) == 1


def test_every_fact_table_is_spaced_the_same_way():
    """Two pages of facts have to line up, or neither is scannable."""
    first, second = theme.fact_form(), theme.fact_form()

    assert first.horizontalSpacing() == second.horizontalSpacing()
    assert first.verticalSpacing() == second.verticalSpacing()
    assert first.verticalSpacing() % theme.GRID == 0
    assert first.horizontalSpacing() % theme.GRID == 0


def test_a_field_label_is_marked_as_one(qtbot):
    label = theme.field_label("Chip ID:")
    qtbot.addWidget(label)

    assert label.text() == "Chip ID:"
    assert label.property("role") == theme.ROLE_FIELD_LABEL
