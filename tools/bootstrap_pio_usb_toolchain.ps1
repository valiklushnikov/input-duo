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
    verified two ways: `git rev-parse HEAD` must equal the pinned SHA, and
    `git status --porcelain` must be empty. HEAD alone is not enough - a file
    edited in place inside an already-correct checkout does not move HEAD, so
    a hand-edited tree would otherwise still read as "verified". A mismatch
    on either check aborts the whole script: silently falling back to `main`,
    or to whatever a stale or hand-edited .deps/ directory already contains,
    is exactly the failure mode this script exists to rule out.
    cmake/pio_usb_toolchain_lock.cmake verifies the same two things again at
    configure time, so a .deps/ tree edited by hand after this script ran
    still cannot be built against silently.

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

# name            - subdirectory under .deps/
# url             - clone source
# revision        - the exact upstream commit SHA to fetch, character for character
# patchedRevision - what HEAD must be after patches/<name>/ has been applied and
#                   committed, or $null for a dependency we do not patch. This is
#                   the revision the build is actually verified against.
#
# The patched SHA is reproducible because everything a git commit hashes is
# fixed below: the tree (the clone is normalised to LF first), the parent (the
# pinned upstream revision), the author and committer identity and date, and
# the message, with signing disabled and hooks redirected to repository
# metadata. Change any hashed input and the SHA changes, which is why these are
# constants here and asserted by tests/build/test_pio_usb_toolchain_patches.py.
$Dependencies = @(
    @{ Name = 'pico-sdk';     Url = 'https://github.com/raspberrypi/pico-sdk';        Revision = '98a542c1a62fb549ffb5d66a3e5892b06276b670'; PatchedRevision = $null }
    @{ Name = 'tinyusb';      Url = 'https://github.com/hathach/tinyusb';             Revision = '86ad6e56c1700e85f1c5678607a762cfe3aa2f47'; PatchedRevision = '507766faf14f38a6752401fb4f324cc00cd145dd' }
    @{ Name = 'pico-pio-usb'; Url = 'https://github.com/sekigon-gonnoc/Pico-PIO-USB'; Revision = '3c1eec341a5232640e4c00628b889b641af34b28'; PatchedRevision = 'e2119238c35f7f16d7e25f5608dc56aa0971db3d' }
)

# Fixed so the patch commit hashes identically on every machine.
$PatchCommitName = 'Duo Input toolchain'
$PatchCommitEmail = 'toolchain@duo-input.invalid'
$PatchCommitDate = '1788691431 +0000'
$PatchCommitMessage = 'Duo Input host fixes'

function Invoke-Patches([string]$Dir, [string]$Name, [string]$ExpectedRevision) {
    $patchDir = Join-Path $RepositoryRoot (Join-Path 'patches' $Name)
    $patches = @(Get-ChildItem -Path $patchDir -Filter '*.patch' -ErrorAction Stop | Sort-Object Name)
    if ($patches.Count -eq 0) { throw "no patches found for $Name under $patchDir" }

    Push-Location $Dir
    try {
        # A checkout under core.autocrlf=true stores CRLF in the working tree,
        # so an LF patch will not apply and, worse, a commit made over it would
        # hash differently than the same patch applied on Linux. Normalise the
        # clone before touching it.
        & git config core.autocrlf false
        & git config core.eol lf
        & git rm --cached -r -q . | Out-Null
        & git reset --hard -q HEAD
        if ($LASTEXITCODE -ne 0) { throw "could not normalise line endings in $Name" }

        foreach ($patch in $patches) {
            Write-Host "applying $($patch.Name)" -ForegroundColor DarkGray
            & git apply $patch.FullName
            if ($LASTEXITCODE -ne 0) { throw "git apply of $($patch.Name) failed for $Name" }
        }

        $env:GIT_AUTHOR_NAME = $PatchCommitName
        $env:GIT_AUTHOR_EMAIL = $PatchCommitEmail
        $env:GIT_AUTHOR_DATE = $PatchCommitDate
        $env:GIT_COMMITTER_NAME = $PatchCommitName
        $env:GIT_COMMITTER_EMAIL = $PatchCommitEmail
        $env:GIT_COMMITTER_DATE = $PatchCommitDate
        try {
            # A signed commit has a different object ID, and user/global hooks
            # may reject or mutate this generated commit. Point Git at a known
            # empty directory inside its own metadata and disable signing for
            # this invocation so the pinned SHA is a pure function of the
            # reviewed tree, parent, identity, date and message.
            $emptyHooksDir = Join-Path (Join-Path $Dir '.git') 'duo-input-empty-hooks'
            New-Item -ItemType Directory -Path $emptyHooksDir -Force | Out-Null
            & git -c 'commit.gpgSign=false' -c "core.hooksPath=$emptyHooksDir" `
                commit -q -a -m $PatchCommitMessage
            if ($LASTEXITCODE -ne 0) { throw "committing the patches failed for $Name" }
        }
        finally {
            Remove-Item Env:GIT_AUTHOR_NAME, Env:GIT_AUTHOR_EMAIL, Env:GIT_AUTHOR_DATE, `
                        Env:GIT_COMMITTER_NAME, Env:GIT_COMMITTER_EMAIL, Env:GIT_COMMITTER_DATE `
                        -ErrorAction SilentlyContinue
        }
    }
    finally {
        Pop-Location
    }

    $actual = Get-CurrentRevision $Dir
    if ($actual -ne $ExpectedRevision) {
        # The patches applied but produced a different commit than the one the
        # lock pins. Building against it would be building against something
        # nobody reviewed, so stop here and say exactly what differs.
        throw ("$Name is $actual after applying patches, not the expected " +
               "$ExpectedRevision. Either the patches changed, or something " +
               "the commit SHA depends on did (tree, identity, date, message).")
    }
}

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

function Test-WorkingTreeClean([string]$Dir) {
    # HEAD matching the pin says nothing about a file edited in place without
    # being committed - `git status --porcelain` is what actually answers
    # "does the tree on disk still match that commit".
    Push-Location $Dir
    try {
        $status = (& git status --porcelain 2>$null)
        if ($LASTEXITCODE -ne 0) { return $false }
        return [string]::IsNullOrEmpty($status)
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

    $wanted = if ($dependency.PatchedRevision) { $dependency.PatchedRevision } else { $revision }
    if ($dependency.PatchedRevision) {
        Write-Step "$name @ $revision + patches/$name -> $wanted"
    }
    else {
        Write-Step "$name @ $revision"
    }

    $current = Get-CurrentRevision $dir
    if ($current -eq $wanted -and -not $Force) {
        if (Test-WorkingTreeClean $dir) {
            Write-Host "already at $wanted - skipping" -ForegroundColor DarkGray
            continue
        }
        Write-Host "at $wanted but has uncommitted changes (git status --porcelain is not empty) - re-cloning" -ForegroundColor DarkGray
    }

    if (Test-Path $dir) {
        Write-Host "removing existing $dir (stale, dirty or -Force)" -ForegroundColor DarkGray
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
    if (-not (Test-WorkingTreeClean $dir)) {
        # Also should be unreachable right after a fresh checkout - but if
        # something in this script or the environment left the tree dirty,
        # that must fail loudly rather than be reported as verified.
        throw "$name at $dir has uncommitted changes immediately after checkout"
    }

    if ($dependency.PatchedRevision) {
        Write-Host "applying tracked patches from patches/$name" -ForegroundColor DarkGray
        Invoke-Patches -Dir $dir -Name $name -ExpectedRevision $dependency.PatchedRevision
        $actual = Get-CurrentRevision $dir
        if (-not (Test-WorkingTreeClean $dir)) {
            throw "$name at $dir has uncommitted changes after patching"
        }
        Write-Host "verified: $name is at $actual (patched, clean)" -ForegroundColor Green
    }
    else {
        Write-Host "verified: $name is at $actual (clean)" -ForegroundColor Green
    }
}

Write-Step 'PIO USB toolchain ready'
Write-Host "All three dependencies are cloned and verified under $DepsRoot" -ForegroundColor Green
Write-Host 'Configure with: cmake --preset pico-pio-usb-release'
