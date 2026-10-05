# Is the window TITLE a per-process record of the URI launch's AutoRecovery counter?
#
# The log records only `placeId`, which every concurrent URI launch of one place
# shares -- that is why the URI route was ambiguous. But the window title carries
# the full unique filename INCLUDING the `_AutoRecovery_N` counter. Titles are
# per-PID and readable host-side with no mesh involvement.
#
# If each title contains a distinct N, then:
#     studio_id -> mesh name -> match against window titles -> PID
# is exact, and the URI route joins after all.
param()

$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class T {
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
    public delegate bool EnumProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowTextLengthW(IntPtr h);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr h, int a, out int v, int cb);
    public static List<IntPtr> All() { var l = new List<IntPtr>(); EnumWindows((h,p) => { l.Add(h); return true; }, IntPtr.Zero); return l; }
    public static string Title(IntPtr h) { int n = GetWindowTextLengthW(h); if (n <= 0) return ""; var s = new StringBuilder(n+1); GetWindowTextW(h, s, s.Capacity); return s.ToString(); }
    public static uint Pid(IntPtr h) { uint p; GetWindowThreadProcessId(h, out p); return p; }
    public static bool Cloaked(IntPtr h) { int c; return DwmGetWindowAttribute(h, 14, out c, 4) == 0 && c != 0; }
}
'@

$owners = @{}
foreach ($p in @(Get-Process -Name 'RobloxStudioBeta' -ErrorAction SilentlyContinue)) { $owners[[uint32]$p.Id] = $true }

Write-Host "=== per-process window titles, and the AutoRecovery counter each carries ==="
$rows = @()
foreach ($h in [T]::All()) {
    $pid_ = [T]::Pid($h)
    if (-not $owners.ContainsKey($pid_)) { continue }
    if (-not [T]::IsWindowVisible($h)) { continue }
    if ([T]::Cloaked($h)) { continue }
    $t = [T]::Title($h)
    if ($t.IndexOf('Roblox Studio') -lt 0) { continue }
    $r = New-Object T+RECT
    [void][T]::GetWindowRect($h, [ref]$r)
    if (($r.Right - $r.Left) -le 0) { continue }
    $m = [regex]::Match($t, 'Template_(\d+)_AutoRecovery_(\d+)\.rbxl')
    $n = if ($m.Success) { [int]$m.Groups[2].Value } else { -1 }
    $rows += [pscustomobject]@{ pid = [int]$pid_; n = $n; title = $t }
}

$rows | Sort-Object pid | ForEach-Object {
    $n = if ($_.n -ge 0) { "N=$($_.n)" } else { "N=- (file route)" }
    Write-Host ("  pid={0,-6} {1,-16} {2}" -f $_.pid, $n, $_.title)
}

$counters = @($rows | Where-Object { $_.n -ge 0 } | ForEach-Object { $_.n })
Write-Host ""
Write-Host "distinct AutoRecovery counters: $(($counters | Sort-Object -Unique) -join ', ')"
if ($counters.Count -gt 0 -and (($counters | Sort-Object -Unique).Count -eq $counters.Count)) {
    Write-Host "VERDICT: every counter is distinct, so the title separates concurrent URI launches"
} else {
    Write-Host "VERDICT: counters collide or none found, so the title does not separate them"
}
