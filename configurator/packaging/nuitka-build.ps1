<#
.SYNOPSIS
    Build the Duo Input configurator as a Windows standalone folder.

.DESCRIPTION
    Creates a clean Python 3.12 virtual environment, installs the locked build
    requirements, runs the test suite, and only then compiles. A build is not
    allowed to succeed on a tree whose tests fail: an installer that ships a
    known-broken program is worse than no installer.

    Standalone, never onefile. A onefile build unpacks itself into a temporary
    directory on every start, which makes the working set invisible to the
    operator, slows the first launch, and hides what actually shipped.

.PARAMETER SkipTests
    Compile without running the suite. For iterating on packaging only; never
    for a build that will be given to anyone.

.PARAMETER Clean
    Remove the build virtual environment and the output directory first.

.EXAMPLE
    pwsh -File configurator/packaging/nuitka-build.ps1
#>

[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$PackagingRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfiguratorRoot = Split-Path -Parent $PackagingRoot
$RepositoryRoot = Split-Path -Parent $ConfiguratorRoot

$BuildVenv = Join-Path $ConfiguratorRoot '.venv-build'
$DistRoot = Join-Path $ConfiguratorRoot 'dist'
$OutputDir = Join-Path $DistRoot 'DuoInput'
$Requirements = Join-Path $ConfiguratorRoot 'requirements-build.txt'
$Entry = Join-Path $ConfiguratorRoot 'src/duo_input/app.py'
$Translations = Join-Path $ConfiguratorRoot 'src/duo_input/resources/translations'
$Licenses = Join-Path $RepositoryRoot 'docs/release/third-party-licenses.md'

function Write-Step($message) {
    Write-Host ''
    Write-Host "==> $message" -ForegroundColor Cyan
}

function Get-ProjectVersion {
    $initPath = Join-Path $ConfiguratorRoot 'src/duo_input/__init__.py'
    $match = Select-String -Path $initPath -Pattern '__version__\s*=\s*"([^"]+)"'
    if (-not $match) { throw "no __version__ in $initPath" }
    return $match.Matches[0].Groups[1].Value
}

if ($Clean) {
    Write-Step 'Removing the previous build'
    foreach ($path in @($BuildVenv, $OutputDir)) {
        if (Test-Path $path) { Remove-Item -Recurse -Force $path }
    }
}

# --- a clean interpreter -----------------------------------------------------
# The build environment is separate from the development one on purpose: it is
# the only way to notice that something the program needs was never declared.

Write-Step 'Creating the build virtual environment'
if (-not (Test-Path $BuildVenv)) {
    py -3.12 -m venv $BuildVenv
    if ($LASTEXITCODE -ne 0) { throw 'python 3.12 x64 is required to build' }
}
$BuildPython = Join-Path $BuildVenv 'Scripts/python.exe'

Write-Step 'Installing the locked build requirements'
& $BuildPython -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) { throw 'pip could not be upgraded' }
& $BuildPython -m pip install --requirement $Requirements --quiet
if ($LASTEXITCODE -ne 0) { throw 'the locked build requirements did not install' }

# --- the catalogues ----------------------------------------------------------
# Compiled .qm files are build output, so they are regenerated here rather than
# trusted from the tree; a stale catalogue would ship as if it were current.

Write-Step 'Compiling the translation catalogues'
& $BuildPython (Join-Path $RepositoryRoot 'tools/update_translations.py') --check
if ($LASTEXITCODE -ne 0) { throw 'a translation catalogue is incomplete' }
foreach ($language in @('ru', 'en')) {
    $source = Join-Path $Translations "duo_input_$language.ts"
    $compiled = Join-Path $Translations "duo_input_$language.qm"
    & (Join-Path $BuildVenv 'Scripts/pyside6-lrelease.exe') $source -qm $compiled
    if ($LASTEXITCODE -ne 0) { throw "could not compile $source" }
}

# --- the tests ---------------------------------------------------------------

if (-not $SkipTests) {
    Write-Step 'Running the test suite'
    Push-Location $RepositoryRoot
    try {
        $env:QT_QPA_PLATFORM = 'offscreen'
        & $BuildPython -m pip install --editable $ConfiguratorRoot --quiet
        if ($LASTEXITCODE -ne 0) { throw 'the configurator did not install' }
        & $BuildPython -m pytest configurator/tests -q
        if ($LASTEXITCODE -ne 0) { throw 'the test suite failed; nothing was built' }
    }
    finally {
        Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue
        Pop-Location
    }
}

# --- the build ---------------------------------------------------------------

$version = Get-ProjectVersion
Write-Step "Compiling Duo Input $version"
Push-Location $ConfiguratorRoot
try {
    & $BuildPython -m nuitka --standalone --assume-yes-for-downloads `
        --enable-plugin=pyside6 --windows-console-mode=disable `
        --output-dir=dist --output-filename=DuoInput.exe `
        --include-qt-plugins=platforms,styles `
        --include-data-files="src/duo_input/resources/translations/*.qm=duo_input/resources/translations/" `
        --product-name='Duo Input Configurator' `
        --product-version=$version --file-version=$version `
        --file-description='Duo Input configurator' `
        --copyright='Duo Input' `
        src/duo_input/app.py
    if ($LASTEXITCODE -ne 0) { throw 'nuitka failed' }
}
finally {
    Pop-Location
}

# Nuitka names the folder after the entry module; the contract tests, the
# installer and the operator all expect DuoInput.
$produced = Join-Path $DistRoot 'app.dist'
if (Test-Path $produced) {
    if (Test-Path $OutputDir) { Remove-Item -Recurse -Force $OutputDir }
    Move-Item $produced $OutputDir
}

# Only the compiled catalogues travel. The .ts sources and resources.qrc are
# build inputs; shipping them would put a second, editable copy of every
# string beside the one the program actually reads.
Write-Step 'Copying the third-party licence notices'
if (-not (Test-Path $Licenses)) { throw "missing $Licenses" }
Copy-Item $Licenses (Join-Path $OutputDir 'third-party-licenses.md')

# --- the contract ------------------------------------------------------------

Write-Step 'Checking what actually shipped'
Push-Location $RepositoryRoot
try {
    $env:DUO_INPUT_DIST = $OutputDir
    & $BuildPython -m pytest configurator/tests/packaging -q
    if ($LASTEXITCODE -ne 0) { throw 'the built folder does not meet the dist contract' }
}
finally {
    Remove-Item Env:\DUO_INPUT_DIST -ErrorAction SilentlyContinue
    Pop-Location
}

Write-Host ''
Write-Host "Duo Input $version built into $OutputDir" -ForegroundColor Green
