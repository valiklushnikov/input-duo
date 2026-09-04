# SPIKE helper: вставляет содержимое буфера обмена в папку средствами оболочки.
param(
    [Parameter(Mandatory = $true)][string]$Dest,
    [int]$WaitSeconds = 30,
    [int]$CancelAfter = 0
)
$ErrorActionPreference = "Continue"
Write-Output "PASTE-HELPER pid=$PID dest=$Dest"
$shell = New-Object -ComObject Shell.Application
$folder = $shell.NameSpace($Dest)
if ($null -eq $folder) { Write-Output "NAMESPACE-FAILED"; exit 2 }
$verbs = @($folder.Self.Verbs() | ForEach-Object { $_.Name })
Write-Output ("VERBS " + ($verbs -join " | "))
$t0 = Get-Date
try {
    $folder.Self.InvokeVerb("Paste")
    Write-Output "INVOKEVERB-RETURNED after $((Get-Date) - $t0)"
} catch {
    Write-Output "INVOKEVERB-THREW $($_.Exception.Message)"
}
$cancelled = $false
$deadline = (Get-Date).AddSeconds($WaitSeconds)
$lastSig = ""
$stable = 0
while ((Get-Date) -lt $deadline) {
    if ($CancelAfter -gt 0 -and -not $cancelled -and ((Get-Date) - $t0).TotalSeconds -ge $CancelAfter) {
        $cancelled = $true
        $sig = New-Object -TypeName System.Text.StringBuilder
        Add-Type -Namespace SpikeW -Name Api -MemberDefinition @'
[DllImport("user32.dll", CharSet=CharSet.Unicode)]
public static extern IntPtr FindWindowW(string cls, string title);
[DllImport("user32.dll")]
public static extern bool PostMessageW(IntPtr h, uint msg, IntPtr w, IntPtr l);
'@ -ErrorAction SilentlyContinue
        $h = [SpikeW.Api]::FindWindowW("OperationStatusWindow", $null)
        Write-Output "CANCEL: OperationStatusWindow=$h at $(((Get-Date) - $t0).TotalSeconds)s"
        if ($h -ne [IntPtr]::Zero) {
            [void][SpikeW.Api]::PostMessageW($h, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)  # WM_CLOSE
            Write-Output "CANCEL: WM_CLOSE posted"
        } else {
            Write-Output "CANCEL: progress window not found"
        }
    }
    $items = @(Get-ChildItem -Recurse -Force -LiteralPath $Dest -ErrorAction SilentlyContinue)
    $sig = ($items | ForEach-Object { "$($_.FullName)=$($_.Length)" }) -join ";"
    if ($sig -eq $lastSig -and $items.Count -gt 0) { $stable++ } else { $stable = 0 }
    $lastSig = $sig
    if ($stable -ge 6) { break }
    Start-Sleep -Milliseconds 250
}
Write-Output "RESULT-BEGIN"
Get-ChildItem -Recurse -Force -LiteralPath $Dest -ErrorAction SilentlyContinue |
    ForEach-Object {
        $kind = if ($_.PSIsContainer) { "DIR " } else { "FILE" }
        $len = if ($_.PSIsContainer) { "-" } else { $_.Length }
        Write-Output "$kind $len $($_.FullName.Substring($Dest.Length))"
    }
Write-Output "RESULT-END"
Write-Output "PASTE-HELPER done after $(((Get-Date) - $t0).TotalSeconds)s"
