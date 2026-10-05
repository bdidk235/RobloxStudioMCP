# Does PrintWindow capture an OCCLUDED window?
#
# Their claim is "works while occluded", which is what makes it better than a
# screen grab. Three Studios are open and stacked, so the last in Z-order is
# genuinely covered by the others. If that one captures with real content, the
# claim holds; if it comes back black, it does not.
#
# Prints the Z-order index, whether each window is the foreground one, and the
# non-black fraction, so occlusion and blackness are not conflated.
param()

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class PW2 {
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
    public delegate bool EnumProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowTextLengthW(IntPtr h);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
    [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr h, int a, out int v, int cb);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    public static List<IntPtr> All() { var l = new List<IntPtr>(); EnumWindows((h,p) => { l.Add(h); return true; }, IntPtr.Zero); return l; }
    public static string Title(IntPtr h) { int n = GetWindowTextLengthW(h); if (n <= 0) return ""; var s = new StringBuilder(n+1); GetWindowTextW(h, s, s.Capacity); return s.ToString(); }
    public static uint Pid(IntPtr h) { uint p; GetWindowThreadProcessId(h, out p); return p; }
    public static bool Cloaked(IntPtr h) { int c; return DwmGetWindowAttribute(h, 14, out c, 4) == 0 && c != 0; }
}
'@
[void][PW2]::SetProcessDPIAware()

$owners = @{}
foreach ($p in @(Get-Process -Name 'RobloxStudioBeta' -ErrorAction SilentlyContinue)) { $owners[[uint32]$p.Id] = $true }
$fg = [PW2]::GetForegroundWindow()

# EnumWindows yields topmost first, so index 0 is frontmost and the last index is
# the most occluded.
$wins = @()
foreach ($h in [PW2]::All()) {
    $pid_ = [PW2]::Pid($h)
    if (-not $owners.ContainsKey($pid_)) { continue }
    if (-not [PW2]::IsWindowVisible($h)) { continue }
    if ([PW2]::Cloaked($h)) { continue }
    $t = [PW2]::Title($h)
    if ($t.IndexOf('Roblox Studio') -lt 0) { continue }
    $r = New-Object PW2+RECT
    [void][PW2]::GetWindowRect($h, [ref]$r)
    $w = $r.Right - $r.Left; $ht = $r.Bottom - $r.Top
    if ($w -le 0 -or $ht -le 0) { continue }
    $wins += [pscustomobject]@{ h=$h; pid=[int]$pid_; title=$t; w=$w; ht=$ht; fg=($h -eq $fg) }
}

Write-Host ("{0,-3} {1,-7} {2,-6} {3,-9} {4}" -f 'idx','pid','fg','nonblack','ms')
for ($i = 0; $i -lt $wins.Count; $i++) {
    $win = $wins[$i]
    $bmp = New-Object System.Drawing.Bitmap($win.w, $win.ht, [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $hdc = $g.GetHdc()
    try {
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $ok = [PW2]::PrintWindow($win.h, $hdc, 2)
        $sw.Stop()
        if (-not $ok) { Write-Host ("{0,-3} {1,-7} {2,-6} PRINTWINDOW FAILED" -f $i, $win.pid, $win.fg); continue }
        $ms = [math]::Round($sw.Elapsed.TotalMilliseconds, 1)
    } finally { $g.ReleaseHdc($hdc); $g.Dispose() }

    $tot = 0; $nb = 0
    for ($y = 0; $y -lt $bmp.Height; $y += 11) {
        for ($x = 0; $x -lt $bmp.Width; $x += 11) {
            $c = $bmp.GetPixel($x, $y); $tot++
            if (($c.R + $c.G + $c.B) -gt 12) { $nb++ }
        }
    }
    $pct = [math]::Round(100.0 * $nb / $tot, 1)
    # Z-order: index 0 is topmost. A window with a higher index is behind more of them.
    Write-Host ("{0,-3} {1,-7} {2,-6} {3,-9} {4}" -f $i, $win.pid, $win.fg, "$pct%", $ms)
    $bmp.Dispose()
}
Write-Host ""
Write-Host "index 0 = topmost, last index = most occluded"
