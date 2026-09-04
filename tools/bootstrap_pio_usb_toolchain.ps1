<#
.SYNOPSIS
    Clone the three dependencies the PIO USB firmware backend is pinned to -
    Pico SDK, TinyUSB and Pico-PIO-USB - into the git-ignored .deps/
    directory, at their exact locked revisions.

.DESCRIPTION
    The CH375 build keeps resolving PICO_SDK_PATH from the shell environment,
    unmodified by anything here (Task 1's baseline recorded that as Pico SDK
    2.1.0, revision 95ea6ac). The PIO USB backend instead needs its own,
    independently pinned toolchain - Pico SDK 2.3.0, plus TinyUSB and
    Pico-PIO-USB at exact revisions - so that build is reproducible without
    depending on whatever a developer happens to have on PICO_SDK_PATH.

    Each dependency is fetched by exact commit SHA (GitHub serves any
    reachable commit this way, not only branch tips), then checked out and
    verified with `git rev-parse HEAD`. A mismatch aborts the whole script:
    silently falling back to `main`, or to whatever a stale .deps/ directory
    already contains, is exactly the failure mode this script exists to rule
    out. cmake/pio_usb_toolchain_lock.cmake verifies the same three revisions
    again at configure time, so a .deps/ tree edited by hand after this
    script ran still cannot be built against silently.

    No submodule of any of the three repositories is initialised. At these
    pinned revisions:
      - Pico SDK's only relevant submodule is its own copy of TinyUSB
        (lib/tinyusb); this build overrides that via PICO_TINYUSB_PATH, so
        that submodule is never populated. Its other submodules
        (cyw43-driver, lwip, mbedtls, btstack) are Pico W wireless support,
        irrelevant to the wired Waveshare RP2040-Zero this project targets.
      - TinyUSB and Pico-PIO-USB declare no submodules at all at these
        revisions (neither ships a .gitmodules file).
    If a future revision bump changes any of that, extend the per-dependency
    table below rather than blindly running `git submodule update --init`.

.PARAMETER Force
    Re-clone a dependency even if its directory already exists and is
    already at the correct revision. Without it, an already-correct clone is
    left alone (this script is safe to re-run).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools/bootstrap_pio_usb_toolchain.ps1
#>

[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ToolsRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepositoryRoot = Split-Path -Parent $ToolsRoot
$DepsRoot = Join-Path $RepositoryRoot '.deps'

function Write-Step($message) {
    Write-Host ''
    Write-Host "==> $message" -ForegroundColor Cyan
}

# name       - subdirectory under .deps/
# url        - clone source
# revision   - the exact pinned commit SHA (task-2-brief.md, character for character)
$Dependencies = @(
    @{ Name = 'pico-sdk';     Url = 'https://github.com/raspberrypi/pico-sdk';        Revision = '98a542c1a62fb549ffb5d66a3e5892b06276b670' }
    @{ Name = 'tinyusb';      Url = 'https://github.com/hathach/tinyusb';             Revision = '86ad6e56c1700e85f1c5678607a762cfe3aa2f47' }
    @{ Name = 'pico-pio-usb'; Url = 'https://github.com/sekigon-gonnoc/Pico-PIO-USB'; Revision = '3c1eec341a5232640e4c00628b889b641af34b28' }
)

function Get-CurrentRevision([string]$Dir) {
    if (-not (Test-Path (Join-Path $Dir '.git'))) { return $null }
    Push-Location $Dir
    try {
        $revision = (& git rev-parse HEAD 2>$null)
        if ($LASTEXITCODE -ne 0) { return $null }
        return $revision.Trim()
    }
    finally {
        Pop-Location
    }
}

foreach ($dependency in $Dependencies) {
    $name = $dependency.Name
    $url = $dependency.Url
    $revision = $dependency.Revision
    $dir = Join-Path $DepsRoot $name

    Write-Step "$name @ $revision"

    $current = Get-CurrentRevision $dir
    if ($current -eq $revision -and -not $Force) {
        Write-Host "already at the pinned revision - skipping" -ForegroundColor DarkGray
        continue
    }

    if (Test-Path $dir) {
        Write-Host "removing existing $dir (stale or -Force)" -ForegroundColor DarkGray
        Remove-Item -Recurse -Force $dir
    }

    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    Push-Location $dir
    try {
        & git init -q .
        if ($LASTEXITCODE -ne 0) { throw "git init failed for $name" }

        & git remote add origin $url
        if ($LASTEXITCODE -ne 0) { throw "git remote add failed for $name" }

        # GitHub serves any reachable commit by SHA, not only branch tips, so
        # this fetches exactly the pinned commit without cloning full history.
        # Large repositories (Pico SDK especially) can still take a while on
        # a slow link, hence a generous timeout around the whole script when
        # it is invoked from CI or an agent.
        & git fetch --depth 1 origin $revision
        if ($LASTEXITCODE -ne 0) { throw "git fetch of $revision failed for $name ($url)" }

        & git checkout -q FETCH_HEAD
        if ($LASTEXITCODE -ne 0) { throw "git checkout of $revision failed for $name" }
    }
    finally {
        Pop-Location
    }

    $actual = Get-CurrentRevision $dir
    if ($actual -ne $revision) {
        # This should be unreachable given the checks above, but the whole
        # point of this script is to never let a revision mismatch pass
        # silently - so it is verified once more, explicitly, before success
        # is declared.
        throw "$name at $dir is $actual after checkout, not the pinned $revision"
    }

    Write-Host "verified: $name is at $actual" -ForegroundColor Green
}

Write-Step 'PIO USB toolchain ready'
Write-Host "All three dependencies are cloned and verified under $DepsRoot" -ForegroundColor Green
Write-Host 'Configure with: cmake --preset pico-pio-usb-release'
