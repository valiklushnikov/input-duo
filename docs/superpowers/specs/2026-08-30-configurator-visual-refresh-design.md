# Configurator visual refresh — design

**Date:** 2026-08-30
**Status:** approved for planning
**Scope:** `configurator/` only. No firmware, no protocol, no domain code.

## What this changes and what it does not

The configurator works. Nothing here changes what it does, what it writes, or
what any page means. This is presentation: palette, typeface, corner radii,
two animations, the shape of a modal dialog, and an application icon that has
never existed.

The role system survives intact. Pages keep asking for `ROLE_PAGE_TITLE` and
`SIGNAL_ERROR`; `theme.py` keeps deciding what those look like. That is why
this is a refresh and not a rewrite: the seven pages are barely touched.

**Explicitly out of scope.** A dark theme is not built here — only made
cheaper to build later (see *Tokens*). Blur behind a modal is rejected: Qt
has no cheap way to do it, and the cost is not worth a decoration. Animated
hover states are rejected for the reason given under *Motion*.

## 1. Palette

Warm neutrals replace the current blue-grey ones, and the accent moves from
`#1A63D8` to a steel blue. The four meaning-carrying colours — agreement,
divergence, failure, interactive — keep their jobs and, except for the accent,
their values.

| Token | Now | After | Job |
|---|---|---|---|
| `CANVAS` | `#F1F3F5` | `#F2F2F0` | the page behind the cards |
| `SURFACE` | `#FFFFFF` | `#FFFFFF` | cards, tables, inputs |
| `CHROME` | `#EDEFF3` | `#EDEDEA` | toolbar, state strip, status bar |
| `LINE` | `#D8DDE4` | `#D9D9D4` | hairlines |
| `INK` | `#16191D` | `#17191C` | body text |
| `INK_MUTED` | `#5A626D` | `#565B63` | field labels |
| `INK_FAINT` | `#7F8794` | `#868C96` | absence of data |
| `RAIL` | `#1E2530` | `#22242A` | navigation rail |
| `ACCENT` | `#1A63D8` | `#2A5C8A` | interactive |
| `OK` | `#16743F` | unchanged | agreement |
| `WARN` | `#8A6100` | unchanged | divergence |
| `DANGER` | `#C0362C` | unchanged | failure, and the overwrite button |

### Why the accent is not terracotta

The chosen mock used terracotta `#C0492B`. It cannot ship. Two independent
reasons, both measured against the thresholds already asserted in
`test_theme.py`:

- **It collides with `DANGER`.** Redmean distance to `#C0362C` is **38**; the
  suite requires **100**. In a program whose red marks the one button that
  overwrites a device, an accent that reads as red is a hazard, not a
  preference.
- **It fails contrast as text.** On `#F2F2F0` it reaches **4.43**, under the
  4.5 the suite requires.

Moving the hue does not rescue it: away from red it runs into `WARN`'s ochre.
Measured across the corridor, no shade clears both — `#B85426` scores 62/98,
`#A85F14` scores 98/58. The corridor between red and ochre is narrower than
the separation requirement.

Terracotta survives in the application icon, where no signal colour sits
beside it.

### Why `#2A5C8A` clears

| Check | Threshold | `#2A5C8A` |
|---|---|---|
| distance to `DANGER` | ≥ 100 | 289 |
| distance to `WARN` | ≥ 100 | 269 |
| distance to `OK` | ≥ 100 | 139 |
| as text on `CANVAS` | ≥ 4.5 | 6.25 |
| white text on its fill | ≥ 4.5 | 7.01 |

`ACCENT_STRONG` and `ACCENT_TINT` are re-derived from it: `#22496D` for the
pressed state, `#EBF0F5` for the chip wash. Both need the same assertions the
current pair already gets.

### Tokens, not bare hex

The palette becomes semantic names resolved once, so a second palette can be
swapped in later without touching a page. The dark theme is not built now; the
point is that building it later becomes a matter of one substituted table
rather than a hunt through the stylesheet. This is the only structural change
in `theme.py`.

## 2. Typeface

**Golos Text** replaces Segoe UI. Weights 400, 500 and 600, Cyrillic included,
SIL OFL 1.1 — so it can ship in the distribution with a licence notice and no
other obligation.

Static weights, not the variable file. The variable form is smaller (64 KB),
but Qt's support for variable fonts is version-dependent and a weight that
silently resolves to the wrong instance is worse than three files. The plan
should confirm which form this Qt renders correctly before committing; on a
tie, static wins.

This reverses a documented decision. `theme.py` currently says fonts are
"resolved, never bundled", because Windows already ships Segoe UI and bundling
cost the installer nothing. The reason still holds; the conclusion changes,
because the point of the exercise is a face the operating system does not have.
The module docstring must be rewritten to say so, or it becomes a lie about
its own code.

`install_fonts()` gains a step and keeps its shape:

1. Load the bundled `.ttf`s from `resources/fonts/` via
   `QFontDatabase.addApplicationFont`.
2. If Golos Text is now available, use it.
3. Otherwise fall back exactly as today — Segoe UI, then the platform's
   general font. A missing font file must not leave the program unreadable.

The offscreen-platform trick that already exists — loading faces explicitly
because Qt's offscreen database starts empty — keeps working, and now matters
more: the screenshot tests must render in Golos or they prove nothing.

**Monospace stays Consolas**, resolved from the system as it is today. Golos
Text has no monospaced companion, and the four places monospace appears
(diagnostics counters, overview events and values, profile routes and colour)
are neutral enough that the pairing does not jar. Nothing is bundled for it.

**The type scale does not change.** Sizes stay at 8/9/10/15 pt. Golos has
different metrics from Segoe UI, and that is the risk this section carries:
the screenshot tests assert that no control is clipped at 1280×800 in two
scale factors, and a wider face can break a tight row. The plan must run those
tests early rather than at the end.

## 3. Corner radii and density

`theme.py` currently has a single `RADIUS = 4` used for everything with
corners. This direction wants cards to read as slightly softer than the
controls inside them, so the one value becomes two:

| Token | Now | After |
|---|---|---|
| `RADIUS` (controls) | 4 px | 4 px |
| `RADIUS_CARD` (new) | — (uses 4) | 6 px |

Controls are already where this direction wants them; only cards change, and
only by two pixels. The single existing derived case, `RADIUS - 2`, keeps
deriving from the control value.

Spacing steps stay on the four-pixel grid the suite asserts, and the minimum
control height stays at its current hit-target value. Density in this direction comes
from smaller card padding, not from smaller controls — a control that is
harder to hit is not a design improvement.

## 4. Motion

A new module, `duo_input/ui/motion.py`, holding animation helpers and nothing
else. It may not import from `domain`, `device` or `protocol`, under the same
rule that keeps `theme.py` clean.

| Where | What | Duration | Curve |
|---|---|---|---|
| Modal opens | scrim opacity 0 → 1 | 200 ms | `OutCubic` |
| Modal opens | dialog rises 10 px | 220 ms | `OutCubic` |
| Page changes | content opacity 0 → 1 | 160 ms | `OutCubic` |

Nothing else animates. Specifically, **hover and press states change
instantly**, as they do today. QSS has no `transition`; animating a hover
would mean a `QPropertyAnimation` bound to every button in the program, and
the cost of that machinery is not repaid by the effect. The showcase mock
originally promised this and was corrected.

Every animation must be skippable: if `QT_QPA_PLATFORM` is `offscreen`, or a
duration is zero, the helper applies the end state immediately. The screenshot
tests and the UI suite must never wait on a timer.

## 5. Modal dialogs

`CaptureDialog` (`ui/bindings.py`) and the macro dialogs (`ui/macros.py`)
become frameless `QDialog`s over a scrim that dims the main window.

The capture dialog's countdown becomes a ring: a custom widget that paints an
arc from full to empty across the ten seconds, with the remaining whole
seconds as a numeral in the middle. It replaces the "{0} s left" label.

Two behaviours are load-bearing and must not regress — both are already
covered by tests that have to keep passing:

- Escape and the Cancel button reject the dialog, and the countdown stops.
- The mouse-only capture mode keeps ignoring keyboard events and re-arming
  without resetting the visible countdown.

The ring is presentation. `remaining_seconds` stays the property the tests
read, and the numeral is derived from it.

## 6. Application icon

The program currently sets no icon at all: `setWindowIcon` appears nowhere,
and the build passes no `--windows-icon-from-ico`. Windows shows it a generic
default in the task bar, the task switcher and the title bar.

The mark: a monogram D whose counter is open on the right, with a terracotta
square — the second computer — sitting in the gap. Terracotta `#C0492B` on
graphite `#22242A`.

Ships as `resources/duo-input.ico` with 16, 32, 48 and 256 px layers, each
drawn for its size rather than downscaled — at 16 px the terracotta square
merges with the monogram and is dropped, leaving a readable silhouette.

Three wiring points, all currently absent:

- `app.py` calls `setWindowIcon` at startup.
- `nuitka-build.ps1` passes `--windows-icon-from-ico`, so the `.exe` carries
  it in Explorer.
- The packaging contract test asserts the icon shipped, beside the checks that
  already assert the catalogues did.

## 7. What gets touched

| File | Change |
|---|---|
| `ui/theme.py` | palette → tokens, radii, bundled font loading, docstring |
| `ui/motion.py` | new; animation helpers |
| `ui/bindings.py` | capture dialog reshaped, countdown ring |
| `ui/macros.py` | macro dialogs reshaped |
| `ui/main_window.py` | page-change fade |
| `app.py` | `setWindowIcon` |
| `resources/fonts/` | new; Golos Text 400/500/600 |
| `resources/duo-input.ico` | new |
| `tests/ui/test_theme.py` | expected colours, radii, bundled-font resolution |
| `tests/ui/test_motion.py` | new |
| `tests/ui/test_bindings.py` | ring replaces the countdown label |
| `tests/packaging/` | icon and font ship |
| `packaging/nuitka-build.ps1` | bundle fonts, embed icon |
| `docs/release/third-party-licenses.md` | Golos Text, SIL OFL 1.1 |

The seven pages are otherwise untouched. If a page needs editing beyond a
role name, that is a signal the change escaped its scope.

## 8. How this is verified

The existing suite is the specification for everything this must not break —
733 tests, and the ones that matter most here already exist:

- **Contrast and separation.** `test_theme.py` asserts WCAG AA for every ink
  on every ground it is used on, and ≥ 100 separation between signals. The new
  palette was chosen against these thresholds, not adjusted to them
  afterwards; the numbers are in section 1.
- **Clipping.** `screenshot_smoke.py` renders every page at 1280×800 in both
  languages and two scale factors and fails on a clipped control. This is what
  catches the font metric change, and it must be run as soon as the font
  lands — not at the end.
- **Minimum window.** `test_minimum_window_size_shows_every_control` and the
  mouse-row test hold the layout at 1024×700.
- **Localisation.** Untouched. No string changes, so the catalogues stay
  complete.

New coverage this needs:

- Motion helpers apply the end state immediately when animation is disabled.
- The countdown ring reports the same seconds the dialog's property holds.
- The icon and the font files are present in a built distribution.

## 9. Risks

**Font metrics break a tight layout.** The likeliest failure. Mitigated by
running the screenshot tests immediately after the font lands, while the font
is the only variable. If a row genuinely cannot hold Golos at 150 % scale, the
fix is that row's layout, not a smaller type scale.

**A bundled font that does not load.** A corrupt or missing `.ttf` must
degrade to Segoe UI, not to blank boxes. The fallback chain in section 2 is
the mitigation, and it needs a test that forces the bundled load to fail.

**Distribution grows.** Negligibly. The Golos Text variable file is 64 KB;
even three static weights stay in the hundreds of kilobytes, against a
distribution folder that is already 75.5 MB (the `.exe` alone is 8.8 MB).
Size is not a reason to choose between the two forms.

**The docstring lies.** `theme.py` currently states fonts are never bundled.
Rewriting it is part of the work, not a follow-up.

## 10. Open questions

None. Palette, typeface, monospace, motion scope, modal treatment and icon are
all settled above.
