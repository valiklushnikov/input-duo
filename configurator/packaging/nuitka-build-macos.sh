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

# --- macOS File Provider (Phase 10 / Task 18): appex + shim dylib embedding.
#
# The production host bundle identifier. Deliberately explicit rather than
# left to Nuitka's default (which falls back to the --macos-app-name, "DuoInput"
# — not reverse-DNS shaped) so it is stable, matches the
# `com.duoinput.configurator` prefix the appex/dylib bundle ids nest under
# (fileprovider/project.yml's bundleIdPrefix), and is checked by
# tests/packaging/test_dist.py's bundle-ID collision guard against every known
# scratchpad spike id (com.duoinput.DuoNuitka, .DuoEnumSpike, .DuoFPX2, ...).
HOST_BUNDLE_ID="com.duoinput.configurator"
#: 4YKVN22BMX = "Valentin Lushnikov (Personal Team)" — no paid Developer ID
#: account or App Group entitlement involved anywhere in this build.
FILEPROVIDER_TEAM_ID="4YKVN22BMX"
FILEPROVIDER_BUILD_SCRIPT="$PACKAGING_ROOT/fileprovider-build.sh"
FILEPROVIDER_APPEX_NAME="DuoInputFileProvider.appex"
FILEPROVIDER_DYLIB_NAME="libduofpproto.dylib"

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
    # PySide6 + pytest-qt can raise SIGSEGV in Qt's global destructors at
    # interpreter shutdown on macOS — AFTER a fully green run. Judge this gate by
    # the reported result, not the process exit code: a post-summary crash must
    # not fail a build whose tests all passed, while any real failure/error must.
    GATE_LOG="$DIST_ROOT/clipboard-gate.log"
    mkdir -p "$DIST_ROOT"
    set +e
    ( cd "$CONFIGURATOR_ROOT" && QT_QPA_PLATFORM=offscreen "$BUILD_PY" -m pytest tests/clipboard -q ) \
        > "$GATE_LOG" 2>&1
    GATE_RC=$?
    set -e
    cat "$GATE_LOG"
    if grep -qiE "[0-9]+ (failed|error)|^(FAILED|ERROR) " "$GATE_LOG"; then
        echo "clipboard gate reported test failures — aborting" >&2
        exit 1
    fi
    if ! grep -qE "[0-9]+ passed" "$GATE_LOG"; then
        echo "clipboard gate did not reach a passed summary (rc=$GATE_RC) — aborting" >&2
        exit 1
    fi
    if [ "$GATE_RC" -ne 0 ]; then
        echo "note: pytest exited $GATE_RC after a green run" \
             "(Qt shutdown segfault on macOS) — all tests passed, continuing"
    fi
fi

VERSION="$(version)"
step "Compiling Duo Input $VERSION"
(
    cd "$CONFIGURATOR_ROOT"
    "$BUILD_PY" -m nuitka --standalone --macos-create-app-bundle --assume-yes-for-downloads \
        --enable-plugin=pyside6 \
        --macos-app-name=DuoInput \
        --macos-signed-app-name="$HOST_BUNDLE_ID" \
        --macos-app-icon="$ICON" \
        --macos-app-protected-resource="NSLocalNetworkUsageDescription:Duo Input finds and connects to your paired computer on the local network." \
        --output-dir=dist \
        --include-qt-plugins=platforms,styles,imageformats \
        --include-module=duo_input.clipboard.macos_pasteboard \
        --include-module=duo_input.transfer.macos_pasteboard \
        --include-module=duo_input.transfer.macos_files \
        --include-module=duo_input.transfer.staging \
        --include-module=duo_input.transfer.fileprovider_backend \
        --include-module=duo_input.transfer.fileprovider_client \
        --include-module=duo_input.transfer.fileprovider_domain \
        --include-module=duo_input.transfer.fileprovider_proto \
        --include-module=duo_input.transfer.fileprovider_replica \
        --include-module=objc --include-module=AppKit --include-module=Foundation \
        --include-module=FileProvider \
        --nofollow-import-to=duo_input.transfer.windows_files \
        --nofollow-import-to=duo_input.transfer.windows_com \
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

step "Building the File Provider appex + shim dylib"
[ -x "$FILEPROVIDER_BUILD_SCRIPT" ] || chmod +x "$FILEPROVIDER_BUILD_SCRIPT"
# Two lines on stdout: the appex path, then the dylib path (see the script's
# own header). Both are already signed on the Personal Team by this call —
# nested-first, per Apple's guidance (sign leaves before the tree that
# contains them; never `codesign --deep` a container that embeds an app
# extension, since --deep would blindly re-apply the OUTER entitlements
# — this build's empty host entitlements — onto the appex's own
# app-sandbox entitlement).
# macOS ships bash 3.2 (no `mapfile`): read the script's two output lines
# (appex path, then dylib path) with plain `read` from a here-string instead.
FILEPROVIDER_ARTIFACTS_OUT="$("$FILEPROVIDER_BUILD_SCRIPT")"
{
    IFS= read -r FILEPROVIDER_APPEX_SRC
    IFS= read -r FILEPROVIDER_DYLIB_SRC
} <<< "$FILEPROVIDER_ARTIFACTS_OUT"
[ -d "$FILEPROVIDER_APPEX_SRC" ] || { echo "fileprovider-build.sh did not report a usable appex path" >&2; exit 1; }
[ -f "$FILEPROVIDER_DYLIB_SRC" ] || { echo "fileprovider-build.sh did not report a usable dylib path" >&2; exit 1; }

step "Embedding the appex (Contents/PlugIns) and shim dylib (Contents/Frameworks)"
# Added AFTER Nuitka's own build (which already ad-hoc self-signs the .app it
# just produced) rather than via `--include-data-files`, so neither nested
# artifact's real Personal-Team signature is clobbered by that ad-hoc pass.
PLUGINS_APP="$OUTPUT_APP/Contents/PlugIns"
FRAMEWORKS_APP="$OUTPUT_APP/Contents/Frameworks"
mkdir -p "$PLUGINS_APP" "$FRAMEWORKS_APP"
rm -rf "$PLUGINS_APP/$FILEPROVIDER_APPEX_NAME"
cp -R "$FILEPROVIDER_APPEX_SRC" "$PLUGINS_APP/$FILEPROVIDER_APPEX_NAME"
cp "$FILEPROVIDER_DYLIB_SRC" "$FRAMEWORKS_APP/$FILEPROVIDER_DYLIB_NAME"

step "Signing the outer bundle (Personal Team, hardened runtime)"
IDENTITY="$(security find-identity -v -p codesigning | sed -n '1s/^[[:space:]]*[0-9]*)[[:space:]]*\([0-9A-F]*\).*/\1/p')"
[ -n "$IDENTITY" ] || { echo "no codesigning identity found in the keychain (security find-identity -v -p codesigning)" >&2; exit 1; }

HOST_ENTITLEMENTS="$DIST_ROOT/host-production.entitlements"
# Deliberately empty: the host ships NO application-groups, named-Mach-service,
# or temporary-exception entitlement, and needs none of the appex's
# com.apple.security.app-sandbox (the host process itself is not sandboxed;
# only the File Provider extension is).
cat > "$HOST_ENTITLEMENTS" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict/>
</plist>
PLIST

# Sign EVERY nested Mach-O with the Personal Team first (inside-out). Nuitka
# ad-hoc self-signs the bundle it produces, so Contents/MacOS/Python, the
# bundled PySide6 Qt libraries (many are EXTENSIONLESS Mach-O, e.g.
# Contents/MacOS/QtCore), and every *.so/*.dylib carry TeamIdentifier "not
# set". Under the hardened runtime, dyld refuses to map a non-platform library
# whose Team ID differs from the loading process, so a bundle whose outer app
# is Team-signed but whose Python/Qt libs are ad-hoc CRASHES at launch
# ("different Team IDs") even though `codesign --verify --deep --strict`
# passes. We must therefore re-sign each nested Mach-O with the real team.
# Exclude Contents/PlugIns (the appex is already correctly signed WITH its own
# app-sandbox entitlements by fileprovider-build.sh — re-signing it here would
# clobber that, which is exactly why we do NOT use `codesign --deep` on the
# outer app) and the main executable (sealed by the outer sign below).
step "Signing nested Mach-O libraries (Python, Qt, *.so/*.dylib) on the team"
nested_signed=0
while IFS= read -r macho; do
    case "$macho" in
        "$OUTPUT_APP/Contents/MacOS/app") continue ;;
        "$OUTPUT_APP"/Contents/PlugIns/*) continue ;;
    esac
    if file "$macho" 2>/dev/null | grep -q "Mach-O"; then
        codesign --force --sign "$IDENTITY" --options runtime --timestamp=none "$macho"
        nested_signed=$((nested_signed + 1))
    fi
done < <(find "$OUTPUT_APP/Contents" -type f -not -path "$OUTPUT_APP/Contents/PlugIns/*")
echo "Signed $nested_signed nested Mach-O libraries on team $FILEPROVIDER_TEAM_ID."

# Now seal the main executable + outer container. Shallow (never --deep): the
# appex keeps its own signature/entitlements and every other nested Mach-O was
# just signed above, so the outer seal only needs to (re)sign the main binary
# and hash the already-signed nested code by reference.
codesign --force --sign "$IDENTITY" --options runtime \
    --entitlements "$HOST_ENTITLEMENTS" \
    --identifier "$HOST_BUNDLE_ID" \
    "$OUTPUT_APP"

step "Verifying the signed bundle (codesign --verify --deep --strict)"
codesign --verify --deep --strict "$OUTPUT_APP"

for artifact in "$OUTPUT_APP" "$PLUGINS_APP/$FILEPROVIDER_APPEX_NAME"; do
    team="$(codesign -dv "$artifact" 2>&1 | sed -n 's/^TeamIdentifier=//p')"
    if [ "$team" != "$FILEPROVIDER_TEAM_ID" ]; then
        echo "unexpected signing team for $artifact: '$team' (expected $FILEPROVIDER_TEAM_ID)" >&2
        exit 1
    fi
done

printf '\nDuo Input %s built into %s\n' "$VERSION" "$OUTPUT_APP"
echo "Signed on the Personal Team ($FILEPROVIDER_TEAM_ID); codesign --verify --deep --strict passed."
echo "Gatekeeper still needs right-click -> Open on an unnotarized build, or:"
echo "  xattr -dr com.apple.quarantine \"$OUTPUT_APP\""
