<#
.SYNOPSIS
    Assemble one Duo Input release folder: two UF2 files, the installer,
    SHA-256 sums and the release notes.

.DESCRIPTION
    A release is a claim about a specific commit. This script refuses to make
    that claim about a tree that has uncommitted changes, because the artefacts
    would then correspond to no commit anyone can check out - and the first
    time that matters is the moment somebody tries to reproduce a bug report.

    It reads exactly one SemVer, builds and tests everything, names the
    artefacts per the specification, and writes SHA256SUMS.txt over what it
    actually produced.

    Code signing is deliberately not attempted here. See docs/release/windows-build.md.

.PARAMETER Version
    The SemVer of this release, e.g. 0.1.0 or 0.1.0-rc1.

.PARAMETER OutputDir
    Where the release folder goes. Defaults to dist/release/<Version>.

.PARAMETER AllowDirty
    Build from a dirty tree anyway. The release notes then say so, in the
    artefacts themselves. For local experiments only.

.PARAMETER InputBackend
    Which U1 USB host path this release's U1 image is built with: CH375 (the
    two CH375 chips, current shipping hardware), PIO_USB (the native
    Pico-PIO-USB/TinyUSB host), or PIO_USB_REFERENCE (the reference-first
    Pico-PIO-USB/TinyUSB integration). Defaults to CH375 - the reference
    backend does not become the default until hardware acceptance decides it
    should.

    U1's artefact name and source build directory both follow this
    parameter: CH375 builds ``pico-release`` and names the image
    ``duo-input-u1-<version>.uf2``; PIO_USB additionally builds
    ``pico-pio-usb-release`` and names its image
    ``duo-input-u1-pio-usb-<version>.uf2``; PIO_USB_REFERENCE builds
    ``pico-pio-usb-reference-release`` and names its image
    ``duo-input-u1-pio-usb-reference-<version>.uf2``. In every case, U2 is
    always taken from the ``pico-release`` (CH375-toolchain) build - see the
    "U2 is one artefact" note below.

.PARAMETER DryRun
    Resolve the firmware plan for -InputBackend - which build directories are
    built, which ones the backend/label guard runs against, and the U1/U2
    artefact names and sources - without running the protocol check, the
    native/Python suites, the configurator or the installer. Existing
    ``build/pico-release`` and the selected PIO build directory are still
    checked and guarded for real: only the expensive, non-firmware-specific
    steps and the cmake configure/build invocations themselves are skipped.
    Writes the resolved plan as JSON to
    ``build/release-dry-run.json`` and to stdout, then exits.

    This exists so the naming/sourcing decisions in this script - which the
    rest of it only exercises by actually building and packaging a whole
    release - have something a test can invoke quickly and assert against.
    See ``tests/build/test_build_release_plan.py``.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools/build_release.ps1 -Version 0.1.0-rc1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools/build_release.ps1 -Version 0.1.0-rc1 -InputBackend PIO_USB

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools/build_release.ps1 -Version 0.1.0-rc1 -InputBackend PIO_USB_REFERENCE

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools/build_release.ps1 -Version 0.1.0 -InputBackend PIO_USB -AllowDirty -DryRun
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Version,
    [string]$OutputDir,
    [switch]$AllowDirty,
    [ValidateSet('CH375', 'PIO_USB', 'PIO_USB_REFERENCE')]
    [string]$InputBackend = 'CH375',
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ToolsRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepositoryRoot = Split-Path -Parent $ToolsRoot
$ConfiguratorRoot = Join-Path $RepositoryRoot 'configurator'

if (-not $OutputDir) {
    $OutputDir = Join-Path $RepositoryRoot "dist/release/$Version"
}

function Write-Step($message) {
    Write-Host ''
    Write-Host "==> $message" -ForegroundColor Cyan
}

function Write-Utf8NoBom($path, $lines) {
    # Windows PowerShell 5.1 writes a BOM with -Encoding utf8, and a BOM in
    # SHA256SUMS.txt breaks every checksum verifier that reads it.
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($path, [string[]]$lines, $encoding)
}

function Get-ProjectPython {
    $local = Join-Path $RepositoryRoot '.venv/Scripts/python.exe'
    if (Test-Path $local) { return $local }
    return 'python'
}
$Python = Get-ProjectPython

function Test-BackendCache([string]$BuildDir, [string]$ExpectedBackend) {
    # A preset hard-codes DUO_INPUT_BACKEND, so this should always agree -
    # except a shared build directory can be reconfigured by hand, or by
    # another session, between one run of this script and the next. That is
    # exactly the mislabelling this function exists to catch before it ever
    # reaches an artefact name.
    $cachePath = Join-Path $BuildDir 'CMakeCache.txt'
    if (-not (Test-Path $cachePath)) {
        throw "no CMakeCache.txt in $BuildDir; the configure step did not run or failed silently"
    }
    $match = Select-String -Path $cachePath -Pattern '^DUO_INPUT_BACKEND:STRING=(.+)$'
    if (-not $match) {
        throw "DUO_INPUT_BACKEND is absent from $cachePath"
    }
    $actual = $match.Matches[0].Groups[1].Value.Trim()
    if ($actual -ne $ExpectedBackend) {
        throw ("$BuildDir is configured for DUO_INPUT_BACKEND='$actual', not the " +
               "requested '$ExpectedBackend' - refusing to build a release from a " +
               'stale or hand-reconfigured build directory')
    }
}

# Every build directory this run actually passed to Invoke-BackendArtifactGuard,
# in call order. -DryRun reports this list so a test can tell "the guard ran
# for this directory" apart from "the naming logic merely intended it to" -
# see the function itself, which appends before doing anything else.
$Script:GuardCallLog = @()

function Invoke-BackendArtifactGuard([string]$BuildDir) {
    # tests/build/test_firmware_artifacts.py checks the flash-layout contract;
    # tests/build/test_backend_artifacts.py is the guard that a PIO USB U1
    # image cannot be labelled CH375 or vice versa - it cross-checks
    # CMakeCache.txt's declared backend against the symbols actually linked
    # into the ELF. Both run scoped to this one build directory, and a
    # release is never assembled from a directory that fails either.
    #
    # Recorded unconditionally, before anything that could throw or be
    # skipped, so $Script:GuardCallLog reflects every directory this run
    # actually reached this function for - see -DryRun and
    # tests/build/test_build_release_plan.py, which mutation-verify that a
    # deleted call site here is caught rather than silently leaving a
    # backend unguarded.
    $Script:GuardCallLog += $BuildDir

    $previous = $env:DUO_INPUT_PICO_BUILD
    $env:DUO_INPUT_PICO_BUILD = $BuildDir
    try {
        & $Python -m pytest tests/build/test_firmware_artifacts.py tests/build/test_backend_artifacts.py -q
        if ($LASTEXITCODE -ne 0) {
            throw "the firmware in $BuildDir does not meet the build/backend contract"
        }
    }
    finally {
        if ($null -eq $previous) {
            Remove-Item Env:\DUO_INPUT_PICO_BUILD -ErrorAction SilentlyContinue
        }
        else {
            $env:DUO_INPUT_PICO_BUILD = $previous
        }
    }
}

function Get-PioUsbToolchainRevisions {
    # cmake/pio_usb_toolchain_lock.cmake is the single source of truth for
    # these three revisions (see that file's own header comment); reading
    # them out of it here, rather than repeating the hashes in this script,
    # is what keeps the release notes from drifting out of sync with what
    # configuration actually pins and verifies.
    $lockFile = Join-Path $RepositoryRoot 'cmake/pio_usb_toolchain_lock.cmake'
    $text = Get-Content -Raw $lockFile
    $revisions = [ordered]@{}
    foreach ($pair in @(
            @{ Label = 'Pico SDK'; Var = 'DUO_PIO_USB_PICO_SDK_REVISION' }
            @{ Label = 'TinyUSB'; Var = 'DUO_PIO_USB_TINYUSB_REVISION' }
            @{ Label = 'Pico-PIO-USB'; Var = 'DUO_PIO_USB_PICO_PIO_USB_REVISION' }
        )) {
        if ($text -match "set\($($pair.Var)\s+`"([0-9a-f]{40})`"\)") {
            $revisions[$pair.Label] = $Matches[1]
        }
        else {
            throw "could not find $($pair.Var) in $lockFile"
        }
    }
    return $revisions
}

# --- one version, and it has to look like one --------------------------------

if ($Version -notmatch '^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$') {
    throw "'$Version' is not a SemVer; expected 1.2.3 or 1.2.3-rc1"
}

$declared = (Select-String `
    -Path (Join-Path $ConfiguratorRoot 'src/duo_input/__init__.py') `
    -Pattern '__version__\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
$core = ($Version -split '-')[0]
if ($declared -ne $core) {
    throw "duo_input.__version__ is $declared but this release is $Version; " +
          'set one and only one version, in the source'
}

# --- a release names a commit ------------------------------------------------

Write-Step 'Checking the working tree'
Push-Location $RepositoryRoot
try {
    $status = & git status --porcelain
    $dirty = [bool]$status
    if ($dirty -and -not $AllowDirty) {
        Write-Host ($status -join [Environment]::NewLine)
        throw 'the working tree has uncommitted changes; commit them or pass -AllowDirty'
    }
    $commit = (& git rev-parse HEAD).Trim()
    $branch = (& git rev-parse --abbrev-ref HEAD).Trim()
}
finally {
    Pop-Location
}

# --- everything that can be verified, is ------------------------------------

if ($DryRun) {
    Write-Step 'Skipping the protocol check and the native/Python suites (-DryRun)'
}
else {
    Write-Step 'Verifying the generated protocol'
    Push-Location $RepositoryRoot
    try {
        & $Python tools/generate_protocol.py --check
        if ($LASTEXITCODE -ne 0) { throw 'the generated protocol is out of date' }

        Write-Step 'Building and running the native tests'
        & cmake --build build/native
        if ($LASTEXITCODE -ne 0) { throw 'the native build failed' }
        & ctest --test-dir build/native --output-on-failure
        if ($LASTEXITCODE -ne 0) { throw 'the native tests failed' }

        Write-Step 'Running the Python suites'
        $env:QT_QPA_PLATFORM = 'offscreen'
        & $Python -m pytest configurator/tests tests -q
        if ($LASTEXITCODE -ne 0) { throw 'the Python suites failed' }
    }
    finally {
        Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue
        Pop-Location
    }
}

# --- the firmware ------------------------------------------------------------
#
# U1's backend is selectable (-InputBackend); U2 is not - see "U2 is one
# artefact" in docs/release/firmware-build.md for why every release, PIO_USB
# ones included, takes its U2 from the CH375-toolchain pico-release build
# rather than from whichever preset built U1.
#
# -DryRun skips the cmake configure/build calls themselves (so it needs no
# toolchain and does not spend build time) but still runs Test-BackendCache
# and Invoke-BackendArtifactGuard for real, against whatever is already in
# each build directory - the point of -DryRun is to prove these call sites
# are actually reached for the right directories, not to fake that they were.

Write-Step "Building the Pico firmware (U1 backend: $InputBackend)"
Push-Location $RepositoryRoot
try {
    $ch375BuildDir = Join-Path $RepositoryRoot 'build/pico-release'
    if ($DryRun) {
        Write-Step 'Skipping the CH375 configure/build (-DryRun); using what is already in build/pico-release'
    }
    else {
        & cmake --preset pico-release
        if ($LASTEXITCODE -ne 0) {
            throw 'configuring the Pico build (CH375) failed; see docs/release/firmware-build.md'
        }
        & cmake --build --preset pico-release --parallel
        if ($LASTEXITCODE -ne 0) { throw 'the Pico firmware build (CH375) failed' }
    }

    Test-BackendCache $ch375BuildDir 'CH375'

    Write-Step 'Checking the CH375 firmware meets the build and backend contract'
    Invoke-BackendArtifactGuard $ch375BuildDir

    $pioBuildDir = $null
    if ($InputBackend -in @('PIO_USB', 'PIO_USB_REFERENCE')) {
        $isReference = $InputBackend -eq 'PIO_USB_REFERENCE'
        $pioPreset = if ($isReference) {
            'pico-pio-usb-reference-release'
        }
        else {
            'pico-pio-usb-release'
        }
        $pioBuildDir = Join-Path $RepositoryRoot "build/$pioPreset"
        $pioLabel = if ($isReference) { 'PIO USB reference' } else { 'PIO USB' }
        if ($DryRun) {
            Write-Step "Skipping the $pioLabel configure/build (-DryRun); using what is already in build/$pioPreset"
        }
        else {
            Write-Step "Building the Pico firmware ($pioLabel)"
            & cmake --preset $pioPreset
            if ($LASTEXITCODE -ne 0) {
                throw "configuring the Pico build ($pioLabel) failed; see docs/release/firmware-build.md"
            }
            & cmake --build --preset $pioPreset --parallel
            if ($LASTEXITCODE -ne 0) { throw "the Pico firmware build ($pioLabel) failed" }
        }

        Test-BackendCache $pioBuildDir $InputBackend

        Write-Step "Checking the $pioLabel firmware meets the build and backend contract"
        Invoke-BackendArtifactGuard $pioBuildDir
    }
}
finally {
    Pop-Location
}

# U1 comes from whichever preset -InputBackend selected; U2 always comes from
# the CH375-toolchain pico-release directory - see "U2 is one artefact" in
# docs/release/firmware-build.md. Computed here, immediately after the
# firmware section rather than only at assembly time, so -DryRun can report
# exactly this naming/sourcing decision without running the configurator or
# installer, and so the assemble step below has one source of truth for it
# instead of recomputing the same branch a second time.
if ($InputBackend -eq 'PIO_USB_REFERENCE') {
    $u1Source = 'build/pico-pio-usb-reference-release/firmware/u1_reference/duo_u1_reference.uf2'
    $u1Name = "duo-input-u1-pio-usb-reference-$Version.uf2"
}
elseif ($InputBackend -eq 'PIO_USB') {
    $u1Source = 'build/pico-pio-usb-release/firmware/u1_main/duo_u1_main.uf2'
    $u1Name = "duo-input-u1-pio-usb-$Version.uf2"
}
else {
    $u1Source = 'build/pico-release/firmware/u1_main/duo_u1_main.uf2'
    $u1Name = "duo-input-u1-$Version.uf2"
}
# Not selectable by -InputBackend, deliberately - see "U2 is one artefact".
$u2Source = 'build/pico-release/firmware/u2_endpoint/duo_u2_endpoint.uf2'

if ($DryRun) {
    $plan = [ordered]@{
        InputBackend     = $InputBackend
        GuardedBuildDirs = @($Script:GuardCallLog)
        U1Source         = $u1Source
        U1Name           = $u1Name
        U2Source         = $u2Source
    }
    $planJson = $plan | ConvertTo-Json
    $planPath = Join-Path $RepositoryRoot 'build/release-dry-run.json'
    Set-Content -Path $planPath -Value $planJson -Encoding utf8
    Write-Step "Dry run: resolved release plan (also written to $planPath)"
    Write-Host $planJson
    return
}

# --- the configurator --------------------------------------------------------

Write-Step 'Building the configurator'
# Invoked in this host rather than spawned: PowerShell 7 is not a requirement
# for building Duo Input, and stock Windows only has 5.1.
& (Join-Path $ConfiguratorRoot 'packaging/nuitka-build.ps1')

Write-Step 'Building the installer'
& ISCC.exe "/DAppVersion=$Version" (Join-Path $ConfiguratorRoot 'packaging/duo-input.iss')
if ($LASTEXITCODE -ne 0) { throw 'the installer build failed' }

# --- assemble ----------------------------------------------------------------

Write-Step "Assembling $OutputDir"
if (Test-Path $OutputDir) { Remove-Item -Recurse -Force $OutputDir }
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

# $u1Source, $u1Name and $u2Source were resolved right after the firmware
# section above - the same values -DryRun reports - so there is exactly one
# place in this script that decides U1's name/source per backend and U2's
# (backend-independent) source, not two that could drift apart.
$artefacts = @(
    @{ Source = $u1Source; Name = $u1Name }
    @{ Source = $u2Source; Name = "duo-input-u2-$Version.uf2" }
    @{ Source = "configurator/dist/DuoInput-Setup-$Version-x64.exe"; Name = "DuoInput-Setup-$Version-x64.exe" }
)

foreach ($artefact in $artefacts) {
    $source = Join-Path $RepositoryRoot $artefact.Source
    if (-not (Test-Path $source)) { throw "missing artefact: $source" }
    Copy-Item $source (Join-Path $OutputDir $artefact.Name)
}

Copy-Item (Join-Path $RepositoryRoot 'docs/release/third-party-licenses.md') $OutputDir
Copy-Item (Join-Path $RepositoryRoot 'docs/release/compatibility-matrix.md') $OutputDir
Copy-Item (Join-Path $RepositoryRoot 'docs/user/quick-start-ru.md') $OutputDir
Copy-Item (Join-Path $RepositoryRoot 'docs/user/uf2-update-ru.md') $OutputDir

# --- hashes over what was actually produced ----------------------------------

Write-Step 'Hashing the release folder'
$lines = Get-ChildItem -Path $OutputDir -File | Sort-Object Name | ForEach-Object {
    $hash = (Get-FileHash -Algorithm SHA256 -Path $_.FullName).Hash.ToLower()
    "$hash  $($_.Name)"
}
Write-Utf8NoBom (Join-Path $OutputDir 'SHA256SUMS.txt') $lines

# --- notes -------------------------------------------------------------------

$firmwareNotes = if ($InputBackend -in @('PIO_USB', 'PIO_USB_REFERENCE')) {
    $revisions = Get-PioUsbToolchainRevisions
    $backendDescription = if ($InputBackend -eq 'PIO_USB_REFERENCE') {
        'reference-first Pico-PIO-USB/TinyUSB host on Core 1'
    }
    else {
        'native Pico-PIO-USB/TinyUSB host on Core 1'
    }
    @"
- U1 backend: ``$InputBackend`` ($backendDescription)
- PIO USB toolchain (pinned by ``cmake/pio_usb_toolchain_lock.cmake``):
  - Pico SDK: ``$($revisions['Pico SDK'])``
  - TinyUSB: ``$($revisions['TinyUSB'])``
  - Pico-PIO-USB: ``$($revisions['Pico-PIO-USB'])``
- U2 endpoint: built from the CH375-toolchain ``pico-release`` directory
  (Pico SDK 2.1.0), not from the PIO USB toolchain above - U2's source is
  identical either way, and shipping one U2 binary regardless of which U1
  backend a release contains is a deliberate choice; see "U2 is one
  artefact" in ``docs/release/firmware-build.md``.
"@
}
else {
    @"
- U1 backend: ``CH375`` (the two CH375 USB host chips)
- Toolchain: Pico SDK 2.1.0 (``PICO_SDK_PATH``, environment-provided)
"@
}

$notes = @"
# Duo Input $Version

- Commit: ``$commit`` on ``$branch``
- Built: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')
- Working tree: $(if ($dirty) { 'DIRTY - these artefacts match no commit' } else { 'clean' })

## Firmware

$firmwareNotes

## Artefacts

$($artefacts | ForEach-Object { "- ``$($_.Name)``" } | Out-String)
Verify with ``SHA256SUMS.txt``.

## What is verified

- Generated protocol identifiers match ``protocol/schema.json``.
- Native firmware tests and the fuzz corpus smoke tests pass.
- The configurator suite passes, including the dist contract over the built folder.
- The U1 image actually links the backend this file says it does, not just
  the one it was asked to build - ``tests/build/test_backend_artifacts.py``
  cross-checks the build directory's declared backend against the linked
  ELF's symbols before this script names or copies the artefact.

## What is not

- Hardware acceptance. Latency, soak, power-cut and compatibility figures are
  recorded in ``compatibility-matrix.md`` only when the rig has actually
  produced them. An empty row means untested, not passed.
- Code signing. This installer is unsigned; SmartScreen will warn.
- The Qt for Python licensing route. See ``third-party-licenses.md``.

Commercial distribution remains blocked until the two items above are settled
and the device carries a legitimate USB VID/PID.
"@
Write-Utf8NoBom (Join-Path $OutputDir 'RELEASE-NOTES.md') ($notes -split "`r?`n")

Write-Host ''
Write-Host "Duo Input $Version assembled into $OutputDir" -ForegroundColor Green
Get-Content (Join-Path $OutputDir 'SHA256SUMS.txt')
