# SPIKE helper: открывает настоящее окно Проводника на папке и жмёт Ctrl+V.
# Это ровно тот путь, которым пойдёт пользователь.
param(
    [Parameter(Mandatory = $true)][string]$Dest,
    [int]$WaitSeconds = 70,
    [int]$CancelAfter = 0
)
$ErrorActionPreference = "Continue"

Add-Type -Namespace SpikeE -Name Api -MemberDefinition @'
[DllImport("user32.dll", CharSet=CharSet.Unicode)]
public static extern int GetClassNameW(IntPtr h, System.Text.StringBuilder s, int n);
[DllImport("user32.dll", CharSet=CharSet.Unicode)]
public static extern int GetWindowTextW(IntPtr h, System.Text.StringBuilder s, int n);
[DllImport("user32.dll")]
public static extern bool EnumWindows(EnumProc cb, IntPtr p);
[DllImport("user32.dll")]
public static extern bool IsWindowVisible(IntPtr h);
[DllImport("user32.dll")]
public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
[DllImport("user32.dll")]
public static extern bool PostMessageW(IntPtr h, uint m, IntPtr w, IntPtr l);
public delegate bool EnumProc(IntPtr h, IntPtr p);
'@

function Get-WindowsOfClass([string]$Class) {
    $list = New-Object System.Collections.ArrayList
    $cb = [SpikeE.Api+EnumProc] {
        param($h, $p)
        $sb = New-Object System.Text.StringBuilder 256
        [void][SpikeE.Api]::GetClassNameW($h, $sb, 256)
        if ($sb.ToString() -eq $Class) {
            $t = New-Object System.Text.StringBuilder 512
            [void][SpikeE.Api]::GetWindowTextW($h, $t, 512)
            $pid2 = 0
            [void][SpikeE.Api]::GetWindowThreadProcessId($h, [ref]$pid2)
            [void]$list.Add([pscustomobject]@{
                Hwnd = $h; Pid = $pid2
                Visible = [SpikeE.Api]::IsWindowVisible($h)
                Title = $t.ToString()
            })
        }
        return $true
    }
    [void][SpikeE.Api]::EnumWindows($cb, [IntPtr]::Zero)
    return $list
}

$leaf = Split-Path $Dest -Leaf
Write-Output "EXPLORER-PASTE pid=$PID dest=$Dest leaf=$leaf"
Start-Process explorer.exe -ArgumentList $Dest
Start-Sleep -Seconds 3

$ws = New-Object -ComObject WScript.Shell
$activated = $false
for ($i = 0; $i -lt 15; $i++) {
    if ($ws.AppActivate($leaf)) { $activated = $true; break }
    Start-Sleep -Milliseconds 400
}
Write-Output "APPACTIVATE activated=$activated"
if (-not $activated) { Write-Output "EXPLORER-WINDOW-NOT-FOUND"; exit 3 }
Start-Sleep -Milliseconds 800

$t0 = Get-Date
$ws.SendKeys("^v")
Write-Output "SENDKEYS ^v sent"

$cancelled = $false
$seenProgress = $false
$lastState = ""
$stable = 0
$lastSig = ""
while (((Get-Date) - $t0).TotalSeconds -lt $WaitSeconds) {
    $elapsed = ((Get-Date) - $t0).TotalSeconds
    $prog = Get-WindowsOfClass "OperationStatusWindow"
    $visible = @($prog | Where-Object { $_.Visible })
    if ($visible.Count -gt 0 -and -not $seenProgress) {
        $seenProgress = $true
        Write-Output ("PROGRESS-APPEARED at {0:N1}s" -f $elapsed)
    }
    $state = ($prog | ForEach-Object { "pid=$($_.Pid) vis=$($_.Visible) title='$($_.Title)'" }) -join " ; "
    if ($state -ne $lastState) {
        Write-Output ("  [{0,6:N1}s] PROGRESS-WINDOWS {1}" -f $elapsed, $state)
        $lastState = $state
    }
    if ($CancelAfter -gt 0 -and -not $cancelled -and $elapsed -ge $CancelAfter) {
        $cancelled = $true
        $target = $visible | Select-Object -First 1
        if ($null -ne $target) {
            [void][SpikeE.Api]::PostMessageW($target.Hwnd, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)
            Write-Output ("CANCEL: WM_CLOSE -> hwnd=$($target.Hwnd) pid=$($target.Pid) at {0:N1}s" -f $elapsed)
        } else {
            Write-Output ("CANCEL: нет видимого окна прогресса at {0:N1}s" -f $elapsed)
        }
    }
    $items = @(Get-ChildItem -Recurse -Force -LiteralPath $Dest -ErrorAction SilentlyContinue)
    $sig = ($items | ForEach-Object { "$($_.FullName)=$($_.Length)" }) -join ";"
    if ($sig -ne $lastSig) {
        Write-Output ("  [{0,6:N1}s] FILES {1}" -f $elapsed, $sig)
        $lastSig = $sig
        $stable = 0
    } else { $stable++ }
    if ($stable -ge 12 -and $seenProgress -and $visible.Count -eq 0) { break }
    if ($stable -ge 24) { break }
    Start-Sleep -Milliseconds 500
}
Write-Output "RESULT-BEGIN"
Get-ChildItem -Recurse -Force -LiteralPath $Dest -ErrorAction SilentlyContinue |
    ForEach-Object {
        $kind = if ($_.PSIsContainer) { "DIR " } else { "FILE" }
        $len = if ($_.PSIsContainer) { "-" } else { $_.Length }
        Write-Output "$kind $len $($_.FullName.Substring($Dest.Length))"
    }
Write-Output "RESULT-END"
Write-Output ("EXPLORER-PASTE done after {0:N1}s seenProgress=$seenProgress" -f ((Get-Date) - $t0).TotalSeconds)
