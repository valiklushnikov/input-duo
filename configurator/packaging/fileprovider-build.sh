#!/usr/bin/env bash
#
# Build the two Swift/clang artifacts the macOS host bundle embeds:
#   * DuoInputFileProvider.appex — the File Provider app-extension
#     (configurator/fileprovider/Extension + Shared).
#   * libduofpproto.dylib — the shared clang XPC protocol contract
#     (configurator/fileprovider/Shared/DuoFPProto.m), loaded at runtime by
#     the PyObjC host via fileprovider_proto.py.
#
# Both are xcodegen targets defined in configurator/fileprovider/project.yml,
# built by ONE xcodebuild invocation of the DuoInputFileProvider scheme
# (project.yml wires `duofpproto: all` into that scheme's build phase), and
# signed automatically during that build on the Personal Team
# (DEVELOPMENT_TEAM: 4YKVN22BMX in project.yml, CODE_SIGN_STYLE: Automatic) —
# there is no Developer ID / paid-account requirement here.
#
# This script does NOT do the outer-app embedding or re-signing; that is
# nuitka-build-macos.sh's job, once Nuitka has produced the host .app to
# embed these into. This script only produces the two signed artifacts and
# prints their paths (one per line: appex, then dylib) so the caller does not
# need to know xcodebuild's derived-data layout.
#
# Usage:
#   configurator/packaging/fileprovider-build.sh
#
# Output (stdout, exactly two lines):
#   /abs/path/to/DuoInputFileProvider.appex
#   /abs/path/to/libduofpproto.dylib
set -euo pipefail

PACKAGING_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
CONFIGURATOR_ROOT="$(dirname "$PACKAGING_ROOT")"
FILEPROVIDER_ROOT="$CONFIGURATOR_ROOT/fileprovider"

CONFIGURATION="Release"
# Same derived-data location the dev/test workflow uses (see
# fileprovider_proto.py's _dev_dylib): keeping it in-tree under
# fileprovider/build/dd means a build made by this script is found by the
# exact same path a developer's manual `xcodebuild test` run already
# produces, one fewer place for the two to drift apart.
DERIVED_DATA="$FILEPROVIDER_ROOT/build/dd"
PRODUCTS_DIR="$DERIVED_DATA/Build/Products/$CONFIGURATION"

APPEX_NAME="DuoInputFileProvider.appex"
DYLIB_NAME="libduofpproto.dylib"

step() { printf '\n==> %s\n' "$1" >&2; }

command -v xcodegen >/dev/null 2>&1 || { echo "xcodegen is required (brew install xcodegen)" >&2; exit 1; }
command -v xcodebuild >/dev/null 2>&1 || { echo "xcodebuild is required (install Xcode)" >&2; exit 1; }

# stdout is reserved for the two artifact paths this script prints at the end;
# every tool's own chatter (xcodegen's "Generating..." lines, xcodebuild's
# build log) is redirected to stderr so it can never contaminate that
# machine-readable contract.
step "Regenerating the Xcode project (xcodegen)"
( cd "$FILEPROVIDER_ROOT" && xcodegen generate ) >&2

step "Building DuoInputFileProvider.appex + libduofpproto.dylib ($CONFIGURATION)"
xcodebuild \
    -project "$FILEPROVIDER_ROOT/DuoInputFileProvider.xcodeproj" \
    -scheme DuoInputFileProvider \
    -configuration "$CONFIGURATION" \
    -destination "platform=macOS" \
    -derivedDataPath "$DERIVED_DATA" \
    CODE_SIGNING_ALLOWED=YES \
    build \
    >&2

APPEX_PATH="$PRODUCTS_DIR/$APPEX_NAME"
DYLIB_PATH="$PRODUCTS_DIR/$DYLIB_NAME"

[ -d "$APPEX_PATH" ] || { echo "xcodebuild did not produce $APPEX_PATH" >&2; exit 1; }
[ -f "$DYLIB_PATH" ] || { echo "xcodebuild did not produce $DYLIB_PATH" >&2; exit 1; }

step "Verifying the appex + dylib signed on the Personal Team"
for artifact in "$APPEX_PATH" "$DYLIB_PATH"; do
    team="$(codesign -dv "$artifact" 2>&1 | sed -n 's/^TeamIdentifier=//p')"
    if [ "$team" != "4YKVN22BMX" ]; then
        echo "unexpected signing team for $artifact: '$team' (expected 4YKVN22BMX)" >&2
        exit 1
    fi
done

printf '%s\n' "$APPEX_PATH"
printf '%s\n' "$DYLIB_PATH"
