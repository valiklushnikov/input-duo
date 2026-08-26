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

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools/build_release.ps1 -Version 0.1.0-rc1
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Version,
    [string]$OutputDir,
    [switch]$AllowDirty
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

# --- the firmware ------------------------------------------------------------

Write-Step 'Building the Pico firmware'
Push-Location $RepositoryRoot
try {
    & cmake --build --preset pico-release --parallel
    if ($LASTEXITCODE -ne 0) {
        throw 'the Pico firmware build failed; PICO_SDK_PATH and the arm-none-eabi toolchain are required'
    }
}
finally {
    Pop-Location
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

$artefacts = @(
    @{ Source = "build/pico-release/firmware/u1/duo_input_u1.uf2"; Name = "duo-input-u1-$Version.uf2" }
    @{ Source = "build/pico-release/firmware/u2/duo_input_u2.uf2"; Name = "duo-input-u2-$Version.uf2" }
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

$notes = @"
# Duo Input $Version

- Commit: ``$commit`` on ``$branch``
- Built: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')
- Working tree: $(if ($dirty) { 'DIRTY - these artefacts match no commit' } else { 'clean' })

## Artefacts

$($artefacts | ForEach-Object { "- ``$($_.Name)``" } | Out-String)
Verify with ``SHA256SUMS.txt``.

## What is verified

- Generated protocol identifiers match ``protocol/schema.json``.
- Native firmware tests and the fuzz corpus smoke tests pass.
- The configurator suite passes, including the dist contract over the built folder.

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
