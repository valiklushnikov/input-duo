#!/usr/bin/env bash
#
# Build the Duo Input configurator as an unsigned macOS .app bundle.
#
# Mirrors packaging/nuitka-build.ps1 (Windows), adapted for macOS:
#   * a clean Python 3.12 build venv (separate from the dev env on purpose, so a
#     dependency that was never declared is noticed here rather than at runtime),
#   * the locked build requirements (Nuitka, PySide6, cryptography, pyobjc),
#   * Nuitka --standalone --macos-create-app-bundle, never onefile.
#
# The test gate runs the CLIPBOARD suite (configurator/tests/clipboard) from the
# configurator directory: that is the platform surface this macOS build adds, and
# it is reliably green on macOS. The full cross-platform suite is gated by the
# Windows reference build; a handful of its tests are Windows/CWD/font specific
# and are not a macOS packaging concern.
#
# This produces an UNSIGNED bundle. Gatekeeper needs a right-click -> Open (or
# `xattr -dr com.apple.quarantine dist/DuoInput.app`) on first launch. Developer
# ID signing and notarization are a later step, for distribution to others.
#
# Usage:
#   configurator/packaging/nuitka-build-macos.sh [--skip-tests] [--clean]
#
set -euo pipefail

SKIP_TESTS=0
CLEAN=0
for arg in "$@"; do
    case "$arg" in
        --skip-tests) SKIP_TESTS=1 ;;
        --clean) CLEAN=1 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

PACKAGING_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
CONFIGURATOR_ROOT="$(dirname "$PACKAGING_ROOT")"
REPOSITORY_ROOT="$(dirname "$CONFIGURATOR_ROOT")"

BUILD_VENV="$CONFIGURATOR_ROOT/.venv-build"
DIST_ROOT="$CONFIGURATOR_ROOT/dist"
OUTPUT_APP="$DIST_ROOT/DuoInput.app"
REQUIREMENTS="$CONFIGURATOR_ROOT/requirements-build.txt"
TRANSLATIONS="$CONFIGURATOR_ROOT/src/duo_input/resources/translations"
ICON="$CONFIGURATOR_ROOT/src/duo_input/resources/duo-input.icns"
LICENSES="$REPOSITORY_ROOT/docs/release/third-party-licenses.md"

step() { printf '\n==> %s\n' "$1"; }

# Prefer an explicit 3.12 (Homebrew), matching pyproject's >=3.12,<3.13 pin.
PY312="$(command -v python3.12 || true)"
[ -z "$PY312" ] && [ -x /opt/homebrew/opt/python@3.12/bin/python3.12 ] && PY312=/opt/homebrew/opt/python@3.12/bin/python3.12
[ -z "$PY312" ] && { echo "python 3.12 is required to build (brew install python@3.12)" >&2; exit 1; }

version() {
    sed -n 's/^__version__ *= *"\([^"]*\)".*/\1/p' "$CONFIGURATOR_ROOT/src/duo_input/__init__.py" | head -1
}

if [ "$CLEAN" -eq 1 ]; then
    step "Removing the previous build"
    rm -rf "$BUILD_VENV" "$OUTPUT_APP" "$DIST_ROOT/app.app" "$DIST_ROOT/app.build" "$DIST_ROOT/app.dist"
fi

step "Creating the build virtual environment"
[ -d "$BUILD_VENV" ] || "$PY312" -m venv "$BUILD_VENV"
BUILD_PY="$BUILD_VENV/bin/python"

step "Installing the locked build requirements"
"$BUILD_PY" -m pip install --upgrade pip --quiet
"$BUILD_PY" -m pip install --requirement "$REQUIREMENTS" --quiet

# Nuitka's entry point is the package's app.py; install the src-layout project so
# imports of duo_input resolve and lazy production imports are visible to Nuitka.
"$BUILD_PY" -m pip install --editable "$CONFIGURATOR_ROOT" --quiet

step "Compiling the translation catalogues"
for language in ru en; do
    "$BUILD_VENV/bin/pyside6-lrelease" "$TRANSLATIONS/duo_input_$language.ts" \
        -qm "$TRANSLATIONS/duo_input_$language.qm"
done

if [ "$SKIP_TESTS" -eq 0 ]; then
    step "Running the clipboard test suite (macOS platform surface)"
    ( cd "$CONFIGURATOR_ROOT" && QT_QPA_PLATFORM=offscreen "$BUILD_PY" -m pytest tests/clipboard -q )
fi

VERSION="$(version)"
step "Compiling Duo Input $VERSION"
(
    cd "$CONFIGURATOR_ROOT"
    "$BUILD_PY" -m nuitka --standalone --macos-create-app-bundle --assume-yes-for-downloads \
        --enable-plugin=pyside6 \
        --macos-app-name=DuoInput \
        --macos-app-icon="$ICON" \
        --output-dir=dist \
        --include-qt-plugins=platforms,styles,imageformats \
        --include-module=duo_input.clipboard.macos_pasteboard \
        --include-module=objc --include-module=AppKit --include-module=Foundation \
        --include-data-files="src/duo_input/resources/translations/*.qm=duo_input/resources/translations/" \
        --include-data-files="src/duo_input/resources/*.png=duo_input/resources/" \
        --include-data-files="src/duo_input/resources/*.ico=duo_input/resources/" \
        --include-data-files="src/duo_input/resources/fonts/*.ttf=duo_input/resources/fonts/" \
        --product-name="Duo Input Configurator" \
        --product-version="$VERSION" --file-version="$VERSION" \
        --file-description="Duo Input configurator" \
        --copyright="Duo Input" \
        src/duo_input/app.py
)

# Nuitka names the bundle after the entry module (app.app); the operator expects
# DuoInput.app.
if [ -d "$DIST_ROOT/app.app" ]; then
    rm -rf "$OUTPUT_APP"
    mv "$DIST_ROOT/app.app" "$OUTPUT_APP"
fi
rm -rf "$DIST_ROOT/app.build" "$DIST_ROOT/app.dist"

step "Copying the third-party licence notices"
[ -f "$LICENSES" ] || { echo "missing $LICENSES" >&2; exit 1; }
RESOURCES_APP="$OUTPUT_APP/Contents/Resources"
mkdir -p "$RESOURCES_APP"
cp "$LICENSES" "$RESOURCES_APP/third-party-licenses.md"
cp "$(dirname "$TRANSLATIONS")/fonts/OFL.txt" "$RESOURCES_APP/OFL-GolosText.txt"

printf '\nDuo Input %s built into %s\n' "$VERSION" "$OUTPUT_APP"
echo "Unsigned: first launch needs right-click -> Open, or:"
echo "  xattr -dr com.apple.quarantine \"$OUTPUT_APP\""
