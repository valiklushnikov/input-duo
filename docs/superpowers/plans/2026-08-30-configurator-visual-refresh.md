# Configurator Visual Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the configurator a warm, dense visual language with a bundled typeface, two animations, reshaped modal dialogs and an application icon — without changing what any page does.

**Architecture:** `theme.py` already centralises the look behind role names, so the palette, radii and font all change in one module and the seven pages stay untouched. Motion lands in a new sibling module, `motion.py`, held to the same rule: presentation only, no imports from `domain`, `device` or `protocol`. Dialogs and the icon are wired at their existing call sites.

**Tech Stack:** Python 3.12, PySide6 (Qt 6), pytest + pytest-qt, Nuitka for packaging, PowerShell build script.

**Spec:** `docs/superpowers/specs/2026-08-30-configurator-visual-refresh-design.md`

## Global Constraints

- **Working directory:** `C:\Users\Valentyn\Documents\Codex\2026-08-01\new-chat\work\duo-input-mvp`. All paths below are relative to it.
- **Python:** use `.venv/Scripts/python.exe` from the repository root. Run pytest from the repository root: `.venv/Scripts/python.exe -m pytest configurator/tests -q`. Running it from inside `configurator/` breaks `test_transport_vectors.py`, which resolves its vectors relative to the root.
- **Offscreen rendering:** `configurator/tests/ui/conftest.py` sets
  `QT_QPA_PLATFORM=offscreen` for the whole UI suite, which means
  `motion.animations_enabled()` is False there and no test ever waits on an
  animation. The consequence: **the tests prove the animations do not break
  anything, never that they look right.** Task 11 is the only place movement
  is actually seen.
- **`theme.py` and `motion.py` may not import** from `duo_input.domain`, `duo_input.device` or `duo_input.protocol`. This is a stated rule of the module, not a style preference.
- **Exact palette values** (spec section 1), to be copied verbatim:
  `CANVAS #F2F2F0`, `SURFACE #FFFFFF`, `CHROME #EDEDEA`, `LINE #D9D9D4`,
  `LINE_STRONG #C6C6C0`, `INK #17191C`, `INK_MUTED #565B63`,
  `INK_FAINT #7C838D`, `RAIL #22242A`, `RAIL_INK #C3CAD4`,
  `RAIL_INK_ACTIVE #FFFFFF`, `RAIL_HOVER #2E313A`, `ACCENT #2A5C8A`,
  `ACCENT_STRONG #22496D`, `ACCENT_TINT #EBF0F5`.
  `OK`, `OK_TINT`, `WARN`, `WARN_TINT`, `DANGER`, `DANGER_STRONG`,
  `DANGER_TINT` keep their current values.
- **Every colour is a six-digit hex triple** with a leading `#`. `test_every_declared_colour_is_a_six_digit_hex_triple` enforces this.
- **No new wording.** No `tr()` literal may be reworded or invented. Moving an
  existing literal into a new class is still a change Qt notices — it looks
  translations up by context *and* source — so the catalogues must be
  regenerated and the Russian side filled in whenever that happens. Task 6 is
  the only place in this plan where it does.
- **Commit after every task.** Never use bare `git stash` — this is a worktree sharing its stash stack with other checkouts.

---

### Task 1: The palette

**Files:**
- Modify: `configurator/src/duo_input/ui/theme.py:44-95`
- Test: `configurator/tests/ui/test_theme.py`

**Interfaces:**
- Consumes: nothing.
- Produces: the module constants named in Global Constraints. Every later task and all seven pages read them by name; no name is added or removed in this task, only values change.

The existing contrast and separation tests are the specification here. They are parametrised over the constants, so they re-run against the new values automatically — but they do **not** currently assert that the accent is distinguishable from danger, which is the pair that ruled terracotta out. Step 1 adds it.

- [ ] **Step 1: Write the failing test**

Add to `configurator/tests/ui/test_theme.py`, directly after `test_two_signals_are_never_mistaken_for_one_another`:

```python
def test_the_accent_is_never_mistaken_for_the_destructive_colour():
    """Blue means "you may act"; red means "this overwrites the device".

    An accent that reads as red turns every ordinary button into the one
    button the operator is supposed to hesitate over.
    """
    assert _distance(theme.ACCENT, theme.DANGER) >= 100
```

- [ ] **Step 2: Run it and watch it pass against the current palette**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_theme.py::test_the_accent_is_never_mistaken_for_the_destructive_colour -q`
Expected: PASS. The current blue is 389 away from danger. This test is a guard for the new value, not a red-to-green cycle — it must be in place *before* the palette moves so that a bad palette cannot land green.

- [ ] **Step 3: Replace the palette values**

In `configurator/src/duo_input/ui/theme.py`, change only the right-hand sides. Keep every comment; they explain what each colour is for and stay true.

```python
INK = "#17191C"
INK_MUTED = "#565B63"
INK_FAINT = "#7C838D"

SURFACE = "#FFFFFF"
CANVAS = "#F2F2F0"
CHROME = "#EDEDEA"
LINE = "#D9D9D4"
LINE_STRONG = "#C6C6C0"

RAIL = "#22242A"
RAIL_INK = "#C3CAD4"
RAIL_INK_ACTIVE = "#FFFFFF"
RAIL_HOVER = "#2E313A"

ACCENT = "#2A5C8A"
ACCENT_STRONG = "#22496D"
ACCENT_TINT = "#EBF0F5"
```

`OK`, `OK_TINT`, `WARN`, `WARN_TINT`, `DANGER`, `DANGER_STRONG` and `DANGER_TINT` are not touched.

- [ ] **Step 4: Run the whole theme suite**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_theme.py -q`
Expected: PASS, all of it. These numbers were measured before this plan was written: `INK` reaches 15.02 on the worst ground, `INK_MUTED` 5.83, `INK_FAINT` 3.26 against a 3.0 floor, white on `ACCENT` 7.01, `RAIL_INK` on `RAIL` 9.40, and the accent sits 289 from danger. If anything fails, the value was mistyped — compare against Global Constraints rather than adjusting a threshold.

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests -q`
Expected: 734 passed, 4 skipped. (733 before, plus the new guard.)

- [ ] **Step 6: Commit**

```bash
git add configurator/src/duo_input/ui/theme.py configurator/tests/ui/test_theme.py
git commit -m "Move the palette to warm neutrals and a steel accent"
```

---

### Task 2: A softer radius for cards

**Files:**
- Modify: `configurator/src/duo_input/ui/theme.py:142-143`, and the card rule inside `build_stylesheet`
- Test: `configurator/tests/ui/test_theme.py`

**Interfaces:**
- Consumes: nothing from Task 1 beyond the module existing.
- Produces: `theme.RADIUS_CARD: int`. No later task reads it; the stylesheet does.

`theme.py` has one `RADIUS = 4` for everything with corners. Cards become slightly softer than the controls inside them.

- [ ] **Step 1: Write the failing test**

Add to `configurator/tests/ui/test_theme.py`, after `test_a_control_is_tall_enough_to_hit`:

```python
def test_a_card_is_rounder_than_the_controls_inside_it():
    """A card that shares its radius with its own inputs reads as one slab."""
    assert theme.RADIUS_CARD > theme.RADIUS
    assert theme.RADIUS_CARD - theme.RADIUS <= 4
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_theme.py::test_a_card_is_rounder_than_the_controls_inside_it -q`
Expected: FAIL with `AttributeError: module 'duo_input.ui.theme' has no attribute 'RADIUS_CARD'`

- [ ] **Step 3: Add the token**

In `configurator/src/duo_input/ui/theme.py`, beside `RADIUS`:

```python
#: Corner radius, one value for everything that has corners.
RADIUS = 4
#: Cards sit a little softer than the controls they contain.
RADIUS_CARD = 6
```

- [ ] **Step 4: Point the card rule at it**

In `build_stylesheet`, find the `QGroupBox` rule — it is the card — and change its `border-radius` from `{RADIUS}px` to `{RADIUS_CARD}px`. Leave every other `border-radius` alone, including the `{RADIUS - 2}px` case, which derives from the control value on purpose.

Locate it with:

```bash
grep -n "QGroupBox" configurator/src/duo_input/ui/theme.py
```

- [ ] **Step 5: Run the theme suite**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_theme.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add configurator/src/duo_input/ui/theme.py configurator/tests/ui/test_theme.py
git commit -m "Let a card sit softer than the controls inside it"
```

---

### Task 3: Bundle the typeface

**Files:**
- Create: `configurator/src/duo_input/resources/fonts/GolosText-Regular.ttf`
- Create: `configurator/src/duo_input/resources/fonts/GolosText-Medium.ttf`
- Create: `configurator/src/duo_input/resources/fonts/GolosText-SemiBold.ttf`
- Create: `configurator/src/duo_input/resources/fonts/OFL.txt`
- Modify: `configurator/src/duo_input/ui/theme.py:14-21` (docstring), `:116-124` (family names), `:223-249` (`install_fonts`)
- Modify: `docs/release/third-party-licenses.md`
- Test: `configurator/tests/ui/test_theme.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `theme.install_fonts() -> tuple[str, str]` — unchanged signature, returning `(interface_family, mono_family)`. `theme.UI_FAMILY` becomes `"Golos Text"`; `theme.MONO_FAMILY` stays `"Consolas"`. Task 4 depends on this having landed.

This reverses the module's documented "resolved, never bundled" decision, so the docstring is part of the work, not a follow-up.

- [ ] **Step 1: Fetch the font files**

The Google Fonts CSS API serves the `.ttf` per weight. Run from the repository root:

```powershell
$dir = 'configurator/src/duo_input/resources/fonts'
New-Item -ItemType Directory -Force $dir | Out-Null
$weights = @{ 400 = 'Regular'; 500 = 'Medium'; 600 = 'SemiBold' }
foreach ($w in $weights.Keys) {
  $css = Invoke-WebRequest -UseBasicParsing -UserAgent 'Mozilla/5.0' `
    -Uri "https://fonts.googleapis.com/css2?family=Golos+Text:wght@$w&subset=cyrillic,latin"
  $url = ([regex]::Match($css.Content, 'url\((https://[^)]+\.(ttf|woff2))\)')).Groups[1].Value
  Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile "$dir/GolosText-$($weights[$w]).ttf"
}
Get-ChildItem $dir | Select-Object Name, Length
```

If the API returns `.woff2` rather than `.ttf` for a weight, fetch the static TTF from the Google Fonts GitHub repository instead:
`https://raw.githubusercontent.com/google/fonts/main/ofl/golostext/GolosText%5Bwght%5D.ttf`
— that is the variable file. Prefer it only if Step 4's check passes for it; otherwise take the static instances from the same directory.

Save the licence beside them:

```powershell
Invoke-WebRequest -UseBasicParsing `
  -Uri 'https://raw.githubusercontent.com/google/fonts/main/ofl/golostext/OFL.txt' `
  -OutFile 'configurator/src/duo_input/resources/fonts/OFL.txt'
```

- [ ] **Step 2: Write the failing tests**

Add to `configurator/tests/ui/test_theme.py`, in the fonts section beside `test_the_resolved_families_are_ones_qt_can_actually_use`:

```python
def test_the_interface_face_ships_with_the_program(qapp):
    """The face is bundled, not borrowed from the operating system."""
    from duo_input.ui.theme import bundled_font_files

    files = bundled_font_files()

    assert files, "no font files were found beside the package"
    for path in files:
        assert path.is_file(), path
        assert path.suffix == ".ttf", path


def test_the_bundled_face_is_the_one_the_interface_uses(qapp):
    theme._resolved_families = None
    try:
        interface, _mono = theme.install_fonts()
    finally:
        theme._resolved_families = None

    assert interface == theme.UI_FAMILY


def test_a_missing_bundle_still_leaves_a_readable_interface(qapp, monkeypatch):
    """A font file that did not travel must not blank the program out."""
    monkeypatch.setattr(theme, "bundled_font_files", lambda: ())
    theme._resolved_families = None
    try:
        interface, mono = theme.install_fonts()
    finally:
        theme._resolved_families = None

    assert interface
    assert mono
```

- [ ] **Step 3: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_theme.py -q -k "ships_with_the_program or bundled_face or missing_bundle"`
Expected: FAIL — `ImportError` / `AttributeError` on `bundled_font_files`.

- [ ] **Step 4: Confirm which font form Qt renders**

Before writing the loader, check that Qt actually resolves the files you downloaded:

```bash
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -c "
import sys; sys.path.insert(0,'configurator/src')
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase
from pathlib import Path
app = QApplication([])
for p in sorted(Path('configurator/src/duo_input/resources/fonts').glob('*.ttf')):
    i = QFontDatabase.addApplicationFont(str(p))
    print(p.name, '->', QFontDatabase.applicationFontFamilies(i))
"
```

Expected: every file reports `['Golos Text']`. If a file reports `[]` it did not load — re-fetch it. If you used the variable file and only one weight is offered, fall back to the three static instances; the spec says static wins on a tie.

- [ ] **Step 5: Write the loader**

In `configurator/src/duo_input/ui/theme.py`, change the family constants:

```python
UI_FAMILY = "Golos Text"
UI_FALLBACK_FAMILY = "Segoe UI"
MONO_FAMILY = "Consolas"
MONO_FALLBACK_FAMILY = "monospace"
```

Add above `install_fonts`:

```python
def bundled_font_files() -> tuple[Path, ...]:
    """The interface faces that travel with the program, lowest weight first."""
    directory = Path(__file__).resolve().parent.parent / "resources" / "fonts"
    return tuple(sorted(directory.glob("*.ttf")))
```

Then rewrite the body of `install_fonts`, keeping its docstring's promise honest by rewriting it too:

```python
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
```

Update the module docstring's font paragraph (`theme.py:14-21`) to say the interface face is bundled and the monospace one is resolved. Leave the sentence about the offscreen platform starting with an empty database — it is still true and still the reason this function exists.

- [ ] **Step 6: Run the font tests**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_theme.py -q -k "font or face or families"`
Expected: PASS

- [ ] **Step 7: Record the licence**

Append to `docs/release/third-party-licenses.md`, following the format already used there:

```markdown
## Golos Text

Interface typeface, bundled in `duo_input/resources/fonts`.
Copyright the Golos Text Project Authors.
SIL Open Font License 1.1 — the full text ships beside the fonts as `OFL.txt`.
```

- [ ] **Step 8: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests -q`
Expected: everything passes **except possibly the screenshot tests** — a wider face can clip a control. Do not fix that here; Task 4 exists for it. Record which page and scale failed.

- [ ] **Step 9: Commit**

```bash
git add configurator/src/duo_input/resources/fonts configurator/src/duo_input/ui/theme.py configurator/tests/ui/test_theme.py docs/release/third-party-licenses.md
git commit -m "Ship the interface typeface instead of borrowing one"
```

---

### Task 4: Hold the layout under the new metrics

**Files:**
- Modify: whichever page file the screenshot run names — likely `configurator/src/duo_input/ui/mouse.py` or `configurator/src/duo_input/ui/bindings.py`
- Test: `configurator/tests/ui/test_localization.py::test_no_page_clips_a_control_or_shows_stale_english`

**Interfaces:**
- Consumes: `theme.install_fonts()` from Task 3, now resolving Golos Text.
- Produces: nothing new. This task only removes clipping.

This is the risk the spec names. Golos Text has different metrics from Segoe UI, and the screenshot tests render every page in two languages at two scale factors and fail on a clipped control. Run them while the font is the only thing that changed.

- [ ] **Step 1: Run the screenshot tests**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_localization.py -q -k "no_page_clips"`
Expected: PASS ideally. If it fails, the failure text names the language and scale.

- [ ] **Step 2: If it passed, verify the minimum window too, then skip to Step 5**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_main_window.py -q -k "minimum or fits_the_minimum"`
Expected: PASS

- [ ] **Step 3: If something clipped, see it**

```bash
mkdir -p /tmp/shots
QT_QPA_PLATFORM=offscreen QT_SCALE_FACTOR=1.5 PYTHONPATH=configurator/src \
  .venv/Scripts/python.exe configurator/tests/ui/screenshot_smoke.py --language ru --output /tmp/shots
```

Open the named page's PNG and find the control whose text is cut.

- [ ] **Step 4: Fix the row, not the type scale**

Give the offending row room: widen the form's label column, let the control expand, or allow the label to wrap. Do **not** reduce `TEXT_BODY` or the other scale values — `test_the_body_size_stays_close_to_what_windows_uses` holds them, and shrinking type to fit a layout is the wrong repair.

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "Keep every control unclipped under the new typeface"
```

If nothing needed changing, say so and skip the commit rather than making an empty one.

---

### Task 5: Motion helpers

**Files:**
- Create: `configurator/src/duo_input/ui/motion.py`
- Test: `configurator/tests/ui/test_motion.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `motion.FAST = 160`, `motion.SCRIM = 200`, `motion.LIFT = 220` — durations in milliseconds.
  - `motion.animations_enabled() -> bool`
  - `motion.fade_in(widget: QWidget, duration: int = FAST) -> QPropertyAnimation | None`
  - `motion.lift_in(widget: QWidget, distance: int = 10, duration: int = LIFT) -> QPropertyAnimation | None`

  Both return `None` when animation is disabled, having already applied the end state. Tasks 7, 8 and 9 call these.

- [ ] **Step 1: Write the failing tests**

Create `configurator/tests/ui/test_motion.py`:

```python
"""Movement, and the promise that it can always be skipped.

Animation here is decoration over a program that has to stay testable: every
helper must be able to reach its end state instantly, or the offscreen suite
would be waiting on timers it cannot see.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QWidget

from duo_input.ui import motion


@pytest.fixture
def widget(qtbot) -> QWidget:
    item = QWidget()
    qtbot.addWidget(item)
    return item


def test_the_durations_are_ordered_the_way_the_design_states():
    assert motion.FAST < motion.SCRIM <= motion.LIFT


def test_animation_is_off_under_the_offscreen_platform(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    assert motion.animations_enabled() is False


def test_a_disabled_fade_leaves_the_widget_fully_visible(widget, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)

    assert motion.fade_in(widget) is None
    assert widget.graphicsEffect() is None


def test_a_zero_duration_fade_is_also_instant(widget, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)

    assert motion.fade_in(widget, duration=0) is None
    assert widget.graphicsEffect() is None


def test_a_second_fade_does_not_strand_the_first_effect(widget, monkeypatch):
    """A page faded twice must not keep a half-applied effect from the first."""
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    first = motion.fade_in(widget)
    assert first is not None
    first.stop()

    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    assert motion.fade_in(widget) is None
    assert widget.graphicsEffect() is None


def test_an_enabled_fade_returns_a_running_animation(widget, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)

    animation = motion.fade_in(widget)

    assert animation is not None
    assert animation.duration() == motion.FAST
    animation.stop()


def test_a_disabled_lift_leaves_the_widget_where_it_belongs(widget, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    widget.move(40, 60)

    assert motion.lift_in(widget) is None
    assert widget.pos().y() == 60


def test_an_enabled_lift_starts_below_and_ends_in_place(widget, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    widget.move(40, 60)

    animation = motion.lift_in(widget, distance=10)

    assert animation is not None
    assert animation.startValue().y() == 70
    assert animation.endValue().y() == 60
    animation.stop()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_motion.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'duo_input.ui.motion'`

- [ ] **Step 3: Write the module**

Create `configurator/src/duo_input/ui/motion.py`:

```python
"""How things move, and how to make them stop.

Qt style sheets have no ``transition``, so every animation here is an explicit
``QPropertyAnimation``. That cost is why so little moves: a modal appearing
and a page changing, and nothing else. Hover and press states change at once,
as they always have - animating them would mean an animation object bound to
every button in the program, which is a great deal of machinery for an effect
nobody is waiting to see.

Like :mod:`duo_input.ui.theme`, this module is presentation and nothing else.
It has never heard of a profile, a binding or a device.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

#: A page changing under the operator: quick enough not to be a wait.
FAST = 160
#: The wash that dims the window behind a modal.
SCRIM = 200
#: A dialog rising into place.
LIFT = 220

_CURVE = QEasingCurve.Type.OutCubic


def animations_enabled() -> bool:
    """False where movement cannot be seen or must not be waited on.

    The offscreen platform renders for the screenshot tests, which compare
    finished frames; an animation there is a timer the suite would have to
    sleep through for no gain.
    """
    return os.environ.get("QT_QPA_PLATFORM") != "offscreen"


def fade_in(widget: QWidget, duration: int = FAST) -> QPropertyAnimation | None:
    """Bring ``widget`` up from transparent. ``None`` when it happened at once.

    A widget with no effect is a widget at full opacity, so the instant path
    clears any effect a previous fade left behind rather than setting one to
    1.0 - ``setWindowOpacity`` would be meaningless here, since the things
    faded are pages inside a window, not windows.
    """
    if not animations_enabled() or duration <= 0:
        widget.setGraphicsEffect(None)
        return None

    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    animation = QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(duration)
    animation.setEasingCurve(_CURVE)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    return animation


def lift_in(
    widget: QWidget, distance: int = 10, duration: int = LIFT
) -> QPropertyAnimation | None:
    """Raise ``widget`` into its own position from ``distance`` below it."""
    if not animations_enabled() or duration <= 0:
        return None

    end = widget.pos()
    start = QPoint(end.x(), end.y() + distance)
    animation = QPropertyAnimation(widget, b"pos", widget)
    animation.setDuration(duration)
    animation.setEasingCurve(_CURVE)
    animation.setStartValue(start)
    animation.setEndValue(end)
    animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    return animation


__all__ = ["FAST", "LIFT", "SCRIM", "animations_enabled", "fade_in", "lift_in"]
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_motion.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/ui/motion.py configurator/tests/ui/test_motion.py
git commit -m "Add the two kinds of movement the interface is allowed"
```

---

### Task 6: The countdown ring

**Files:**
- Modify: `configurator/src/duo_input/ui/theme.py` (new widget, beside `ElidingLabel`)
- Test: `configurator/tests/ui/test_theme.py`

**Interfaces:**
- Consumes: `theme.ACCENT`, `theme.LINE` from Task 1.
- Produces: `theme.CountdownRing(total: int, parent: QWidget | None = None)` with
  `set_remaining(seconds: int) -> None` and a read-only `remaining` property.
  Task 7 puts it in the capture dialog.

The ring is presentation, so it lives beside the other painted widget rather than in a dialog. It holds no timer — the dialog already has one.

- [ ] **Step 1: Write the failing tests**

Add to `configurator/tests/ui/test_theme.py`, after the `ElidingLabel` tests:

```python
def test_a_countdown_ring_reports_the_seconds_it_was_given(qtbot):
    ring = theme.CountdownRing(10)
    qtbot.addWidget(ring)

    ring.set_remaining(7)

    assert ring.remaining == 7


def test_a_countdown_ring_never_reports_past_its_ends(qtbot):
    ring = theme.CountdownRing(10)
    qtbot.addWidget(ring)

    ring.set_remaining(-3)
    assert ring.remaining == 0

    ring.set_remaining(99)
    assert ring.remaining == 10


def test_a_countdown_ring_states_the_seconds_for_a_screen_reader(qtbot):
    ring = theme.CountdownRing(10)
    qtbot.addWidget(ring)

    ring.set_remaining(4)

    assert "4" in ring.accessibleName()


def test_a_countdown_ring_paints_without_raising(qtbot):
    """A paintEvent that throws takes the dialog down with it."""
    ring = theme.CountdownRing(10)
    qtbot.addWidget(ring)
    ring.resize(76, 76)
    ring.set_remaining(5)

    ring.grab()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_theme.py -q -k countdown_ring`
Expected: FAIL — `AttributeError: module 'duo_input.ui.theme' has no attribute 'CountdownRing'`

- [ ] **Step 3: Write the widget**

Add to `configurator/src/duo_input/ui/theme.py`, after the `ElidingLabel` class. `QPainter`, `QSize` and `Qt` are already imported; add `QColor`, `QPen` and `QRectF` to the existing `PySide6.QtGui` / `PySide6.QtCore` import lines.

```python
class CountdownRing(QWidget):
    """The seconds left, as an arc that empties and a numeral in the middle.

    It owns no timer. Whatever is counting - a dialog waiting for a button
    press - tells it what to show, so there is exactly one clock and the two
    can never disagree.
    """

    DIAMETER = 76
    THICKNESS = 5

    def __init__(self, total: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._total = max(int(total), 1)
        self._remaining = self._total
        self.setFixedSize(QSize(self.DIAMETER, self.DIAMETER))
        self._announce()

    @property
    def remaining(self) -> int:
        return self._remaining

    def set_remaining(self, seconds: int) -> None:
        """Show ``seconds``, clamped to the range this ring was built for."""
        self._remaining = max(0, min(int(seconds), self._total))
        self._announce()
        self.update()

    def _announce(self) -> None:
        self.setAccessibleName(self.tr("{0} s left").format(self._remaining))

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        inset = self.THICKNESS / 2 + 1
        box = QRectF(inset, inset, self.width() - 2 * inset, self.height() - 2 * inset)

        track = QPen(QColor(LINE), self.THICKNESS)
        track.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(track)
        painter.drawEllipse(box)

        if self._remaining > 0:
            span = int(360 * 16 * self._remaining / self._total)
            arc = QPen(QColor(ACCENT), self.THICKNESS)
            arc.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(arc)
            painter.drawArc(box, 90 * 16, -span)

        painter.setPen(QColor(ACCENT))
        painter.setFont(interface_font(TEXT_PAGE_TITLE, WEIGHT_MEDIUM))
        painter.drawText(
            self.rect(), Qt.AlignmentFlag.AlignCenter, str(self._remaining)
        )
        painter.end()
```

Add `"CountdownRing"` to `__all__`, keeping it alphabetical.

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_theme.py -q -k countdown_ring`
Expected: PASS

- [ ] **Step 5: Give the string its new context**

The literal `"{0} s left"` already exists in both catalogues — but under the
`CaptureDialog` context. Qt looks translations up by context *and* source, so
the ring asking for it as `CountdownRing` would find nothing and fall back to
English. Regenerate the catalogues:

Run: `.venv/Scripts/python.exe tools/update_translations.py`
Expected: it reports one new source and exits non-zero, refusing to compile
until the Russian side is filled in.

Fill it in: open `configurator/src/duo_input/resources/translations/duo_input_ru.ts`,
find the `CountdownRing` context, and set the translation to `осталось {0} с`.
Then run the tool again:

Run: `.venv/Scripts/python.exe tools/update_translations.py`
Expected: `every catalogue is complete`

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_localization.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add configurator/src/duo_input/ui/theme.py configurator/tests/ui/test_theme.py configurator/src/duo_input/resources/translations
git commit -m "Draw the capture countdown as a ring"
```

---

### Task 7: Reshape the capture dialog

**Files:**
- Modify: `configurator/src/duo_input/ui/bindings.py:79-175` (`CaptureDialog`)
- Test: `configurator/tests/ui/test_bindings.py`

**Interfaces:**
- Consumes: `theme.CountdownRing` (Task 6), `motion.fade_in` and `motion.lift_in` (Task 5).
- Produces: `CaptureDialog.ring: theme.CountdownRing`. `remaining_seconds`, `trigger`, `start()` and `tick()` keep their current signatures — the existing tests read them and must not be rewritten.

Two behaviours here are load-bearing and already covered: Escape and Cancel stop the countdown, and mouse-only capture ignores keystrokes while re-arming without resetting the visible clock. Neither may change.

- [ ] **Step 1: Write the failing tests**

Add to `configurator/tests/ui/test_bindings.py`, beside the other capture tests:

```python
def test_the_capture_dialog_shows_its_countdown_as_a_ring(qtbot, service):
    dialog = CaptureDialog(service)
    qtbot.addWidget(dialog)

    assert dialog.ring.remaining == dialog.remaining_seconds

    dialog.tick()

    assert dialog.ring.remaining == dialog.remaining_seconds == 9


def test_the_capture_dialog_has_no_window_frame(qtbot, service):
    from PySide6.QtCore import Qt

    dialog = CaptureDialog(service)
    qtbot.addWidget(dialog)

    assert bool(dialog.windowFlags() & Qt.WindowType.FramelessWindowHint)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_bindings.py -q -k "as_a_ring or no_window_frame"`
Expected: FAIL — no `ring` attribute; the dialog still has a frame.

- [ ] **Step 3: Reshape the dialog**

In `configurator/src/duo_input/ui/bindings.py`, add to the imports:

```python
from duo_input.ui import motion
from duo_input.ui.theme import CountdownRing
```

In `CaptureDialog.__init__`, after `self.setModal(True)`:

```python
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
```

`Qt` is already imported from `PySide6.QtCore` in this module — confirm with `grep -n "from PySide6.QtCore" configurator/src/duo_input/ui/bindings.py` and add it to that line if it is missing.

Replace the `countdown_label` with the ring. Delete these two lines:

```python
        self.countdown_label = QLabel(self)
        self.countdown_label.setAccessibleName(self.tr("Time left to press a key"))
```

and the `layout.addWidget(self.countdown_label)` beneath them, then add in their place:

```python
        self.ring = CountdownRing(CAPTURE_SECONDS, self)
        self.ring.setAccessibleName(self.tr("Time left to press a key"))
        layout.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignHCenter)
```

Change `_refresh` to drive the ring instead of the label:

```python
    def _refresh(self) -> None:
        self.ring.set_remaining(max(self._remaining, 0))
```

Add the entrance at the end of `__init__`:

```python
        motion.fade_in(self, motion.SCRIM)
```

and in `start`, after `self._timer.start()`:

```python
        motion.lift_in(self)
```

- [ ] **Step 4: Run the whole bindings suite**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_bindings.py -q`
Expected: PASS, all of it. If `test_the_capture_dialog_counts_down_from_ten` fails on the countdown label, it is still asserting `countdown_label.text()` — update that one assertion to read `dialog.ring.remaining`, and nothing else about it.

- [ ] **Step 5: Check the removed label did not orphan a string**

Run: `.venv/Scripts/python.exe tools/update_translations.py --check`
Expected: `every catalogue is complete`. `"Time left to press a key"` is still used as the ring's accessible name, so nothing was orphaned.

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add configurator/src/duo_input/ui/bindings.py configurator/tests/ui/test_bindings.py
git commit -m "Give the capture dialog a frameless shape and a ring"
```

---

### Task 8: Reshape the macro test dialog

**Files:**
- Modify: `configurator/src/duo_input/ui/macros.py:684` (`TestMacroDialog`)
- Test: `configurator/tests/ui/test_macro_editor.py`

**Interfaces:**
- Consumes: `motion.fade_in` (Task 5). It does **not** use `CountdownRing` — this dialog counts nothing.
- Produces: nothing new. `TestMacroDialog` keeps its current constructor and public methods.

The spec asks for the macro dialog to match the capture one. It is the only
other `QDialog` in the program, and the change is smaller: no countdown, so
only the frame and the entrance.

- [ ] **Step 1: Write the failing test**

Add to `configurator/tests/ui/test_macro_editor.py`:

```python
def test_the_test_macro_dialog_matches_the_capture_dialog_shape(qtbot, service):
    from PySide6.QtCore import Qt

    from duo_input.ui.macros import TestMacroDialog

    dialog = TestMacroDialog(service)
    qtbot.addWidget(dialog)

    assert bool(dialog.windowFlags() & Qt.WindowType.FramelessWindowHint)
```

Check the constructor first — if `TestMacroDialog` takes different arguments,
match them rather than changing the class:

```bash
grep -n "class TestMacroDialog" -A 12 configurator/src/duo_input/ui/macros.py
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_macro_editor.py -q -k matches_the_capture_dialog_shape`
Expected: FAIL — the dialog still has a frame.

- [ ] **Step 3: Reshape it**

In `configurator/src/duo_input/ui/macros.py`, add to the imports:

```python
from duo_input.ui import motion
```

In `TestMacroDialog.__init__`, after the existing `setModal` or `setWindowTitle`
call:

```python
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
```

and at the end of `__init__`:

```python
        motion.fade_in(self, motion.SCRIM)
```

`Qt` is imported in this module already; confirm with
`grep -n "from PySide6.QtCore" configurator/src/duo_input/ui/macros.py` and add
it to that line if it is not.

- [ ] **Step 4: Run the macro suite**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_macro_editor.py configurator/tests/ui/test_macro_steps.py -q`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add configurator/src/duo_input/ui/macros.py configurator/tests/ui/test_macro_editor.py
git commit -m "Give the macro dialog the same shape as the capture one"
```

---

### Task 9: Fade the page change

**Files:**
- Modify: `configurator/src/duo_input/ui/main_window.py:183`
- Test: `configurator/tests/ui/test_main_window.py`

**Interfaces:**
- Consumes: `motion.fade_in` (Task 5).
- Produces: `MainWindow.show_page` keeps its signature. No new public name.

`self.nav.currentRowChanged` is wired straight to `self.pages.setCurrentIndex`. It gains one step.

- [ ] **Step 1: Write the failing test**

Add to `configurator/tests/ui/test_main_window.py`:

```python
def test_changing_the_page_still_shows_the_page(qtbot, window):
    """The fade must never leave a page stranded behind a half-applied effect."""
    window.show_page(MainWindow.PAGE_MOUSE)

    assert window.pages.currentWidget() is window.mouse
    assert window.mouse.graphicsEffect() is None

    window.show_page(MainWindow.PAGE_OVERVIEW)

    assert window.pages.currentWidget() is window.overview
    assert window.overview.graphicsEffect() is None
```

- [ ] **Step 2: Run it to verify it passes for the wrong reason**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_main_window.py -q -k still_shows_the_page`
Expected: PASS — nothing fades yet, so no page carries an effect. It keeps
passing after Step 3 for a reason worth knowing: `configurator/tests/ui/conftest.py`
sets `QT_QPA_PLATFORM=offscreen` before the QApplication exists, so
`animations_enabled()` is False throughout the UI suite and `fade_in` takes
its instant path. Write this guard first so Step 3 cannot strand a page
silently.

- [ ] **Step 3: Fade on change**

In `configurator/src/duo_input/ui/main_window.py`, add to the imports:

```python
from duo_input.ui import motion
```

Replace the direct connection at line 183:

```python
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
```

with:

```python
        self.nav.currentRowChanged.connect(self._show_page_index)
```

and add the slot beside `show_page`:

```python
    def _show_page_index(self, index: int) -> None:
        """Switch pages, bringing the new one up rather than snapping to it."""
        self.pages.setCurrentIndex(index)
        page = self.pages.currentWidget()
        if page is not None:
            motion.fade_in(page)
```

- [ ] **Step 4: Run the main-window suite**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_main_window.py -q`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add configurator/src/duo_input/ui/main_window.py configurator/tests/ui/test_main_window.py
git commit -m "Bring a page up rather than snapping to it"
```

---

### Task 10: The application icon

**Files:**
- Create: `configurator/src/duo_input/resources/duo-input.ico`
- Modify: `configurator/src/duo_input/app.py:49-62`
- Modify: `configurator/packaging/nuitka-build.ps1:120-135`
- Test: `configurator/tests/packaging/test_dist.py`, `configurator/tests/ui/test_app.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `duo_input.app.icon_path() -> Path`. Only Task 11 depends on it.

The program sets no icon today: `setWindowIcon` appears nowhere and the build passes no icon flag, so Windows shows a generic default everywhere.

- [ ] **Step 1: Draw the icon**

The mark: a monogram D open on the right, with a terracotta square — the second computer — in the gap. Terracotta `#C0492B` on graphite `#22242A`. Run from the repository root:

```bash
.venv/Scripts/python.exe - <<'PY'
from pathlib import Path
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QApplication

app = QApplication([])
GRAPHITE, TERRACOTTA, PAPER = QColor("#22242A"), QColor("#C0492B"), QColor("#FFFFFF")
images = []
for size in (16, 32, 48, 256):
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    k = size / 64.0
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(GRAPHITE)
    p.drawRoundedRect(QRectF(0, 0, size, size), 8 * k, 8 * k)
    stroke = max(2.0, 5 * k)
    pen = QPen(PAPER, stroke)
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    # The D: a stem with a bowl, left open on the right at small sizes.
    box = QRectF(18 * k, 16 * k, 26 * k, 32 * k)
    path_rect = QRectF(box.left(), box.top(), box.width(), box.height())
    p.drawArc(path_rect, -90 * 16, 180 * 16)
    p.drawLine(box.left(), box.top(), box.left(), box.bottom())
    p.drawLine(box.left(), box.top(), box.center().x(), box.top())
    p.drawLine(box.left(), box.bottom(), box.center().x(), box.bottom())
    if size >= 32:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(TERRACOTTA)
        p.drawRoundedRect(QRectF(48 * k, 27 * k, 10 * k, 10 * k), 2 * k, 2 * k)
    p.end()
    images.append(image)

out = Path("configurator/src/duo_input/resources/duo-input.ico")
images[-1].save(str(out.with_suffix(".png")))
# Qt writes a single-image .ico; build the multi-size file with Pillow if it is
# available, otherwise ship the 256 and let Windows downscale.
try:
    from PIL import Image
    pngs = []
    for image, size in zip(images, (16, 32, 48, 256)):
        p = out.parent / f"_icon-{size}.png"
        image.save(str(p))
        pngs.append(Image.open(p))
    pngs[-1].save(str(out), format="ICO",
                  sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
    for size in (16, 32, 48, 256):
        (out.parent / f"_icon-{size}.png").unlink()
except ImportError:
    images[-1].save(str(out))
out.with_suffix(".png").unlink(missing_ok=True)
print("wrote", out, out.stat().st_size, "bytes")
PY
```

Open the result and check it at 16 px. If the monogram is mush, thicken the stroke rather than adding detail.

- [ ] **Step 2: Write the failing tests**

Add to `configurator/tests/ui/test_app.py`:

```python
def test_the_program_carries_an_icon(qapp):
    from duo_input.app import icon_path

    assert icon_path().is_file()
    assert icon_path().suffix == ".ico"
```

Add to `configurator/tests/packaging/test_dist.py`, following the shape of `test_the_chevron_the_interface_draws_ships`:

```python
def test_the_application_icon_ships(files):
    assert any(name.endswith("duo-input.ico") for name in files)
```

- [ ] **Step 3: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_app.py -q -k carries_an_icon`
Expected: FAIL — `ImportError: cannot import name 'icon_path'`

- [ ] **Step 4: Set the icon**

In `configurator/src/duo_input/app.py`, add to the imports:

```python
from pathlib import Path

from PySide6.QtGui import QIcon
```

Add above `main`:

```python
def icon_path() -> Path:
    """The application icon, as it sits beside the package."""
    return Path(__file__).resolve().parent / "resources" / "duo-input.ico"
```

In `main`, after `configure_application()`:

```python
    # Set before the first window exists, so nothing is ever shown wearing the
    # platform's default icon and then corrected.
    icon = icon_path()
    if icon.is_file():
        application.setWindowIcon(QIcon(str(icon)))
```

- [ ] **Step 5: Run the app tests**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_app.py -q`
Expected: PASS

- [ ] **Step 6: Teach the build about the icon and the fonts**

In `configurator/packaging/nuitka-build.ps1`, inside the `nuitka` invocation, add these lines beside the existing `--include-data-files` ones:

```powershell
        --include-data-files="src/duo_input/resources/fonts/*.ttf=duo_input/resources/fonts/" `
        --include-data-files="src/duo_input/resources/*.ico=duo_input/resources/" `
        --windows-icon-from-ico="src/duo_input/resources/duo-input.ico" `
```

Also add the font licence to what travels, next to the `third-party-licenses.md` copy:

```powershell
Copy-Item (Join-Path $Translations '..' 'fonts' 'OFL.txt') (Join-Path $OutputDir 'OFL-GolosText.txt')
```

- [ ] **Step 7: Commit before building**

```bash
git add configurator/src/duo_input/resources/duo-input.ico configurator/src/duo_input/app.py configurator/packaging/nuitka-build.ps1 configurator/tests/ui/test_app.py configurator/tests/packaging/test_dist.py
git commit -m "Give the program an icon of its own"
```

---

### Task 11: Build and see it

**Files:**
- Modify: none expected.
- Test: the built distribution.

**Interfaces:**
- Consumes: everything above.
- Produces: `configurator/dist/DuoInput/DuoInput.exe`.

- [ ] **Step 1: Free the serial port**

The build runs the whole suite, and `configurator/tests/integration/test_real_config_contract.py` opens the device. Close any running DuoInput before starting, or those four tests error with `port did not open` and the build refuses to produce anything.

- [ ] **Step 2: Build**

```powershell
powershell -ExecutionPolicy Bypass -File configurator/packaging/nuitka-build.ps1
```

Expected: the suite passes, Nuitka compiles, the packaging contract tests pass, and the script prints `Duo Input 0.1.0 built into ...`.

- [ ] **Step 3: Look at it**

Run `configurator/dist/DuoInput/DuoInput.exe` and check, in this order:

1. The window and task bar show the new icon, not a generic one.
2. Text is set in Golos Text — compare a heading against the old screenshots in `docs/`. If it looks like Segoe UI, the bundled font did not travel; check `dist/DuoInput/duo_input/resources/fonts`.
3. The Mouse page: press **Определить кнопку**. The dialog has no title bar, dims the window behind it, rises into place, and counts down as a ring.
4. Changing pages in the rail brings the new page up rather than snapping.
5. The **Записать в устройство** button is still visibly red and still the only red thing on screen.

- [ ] **Step 4: Commit anything the build changed**

```bash
git status --short
```

Expected: clean. If the build wrote into the tree, commit it with a message saying what and why.

---

## Notes for the executor

**The order matters in one place.** Task 3 (font) must be followed immediately by Task 4 (clipping), while the typeface is the only variable. Everything else can be reordered.

**Do not adjust a threshold to make a test pass.** The contrast floors, the 100-unit signal separation and the four-pixel grid are the design's promises, and every palette value in this plan was measured against them before it was written down. A failure means a typo in a hex triple.

**The suite cannot see the animations.** Everything under `tests/ui` runs
offscreen with movement disabled, by design — a suite that waits on timers is
a slow suite that fails at random. That is why Task 11 ends with a list of
things to look at by hand; skipping it means shipping motion nobody watched.

**If a page needs editing beyond a role name**, stop and say so. The spec's claim is that the seven pages are barely touched; a page that needs restructuring means the change escaped its scope.
