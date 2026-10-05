# Measure PrintWindow against the engine capture path, on this machine.
#
# The claim under test: PW_RENDERFULLCONTENT captures a Studio window in tens of
# milliseconds, works while occluded, and needs no engine round trip. If true it
# replaces a 1.7s mesh round trip for the "quick look" case.
#
# The traps being guarded against, all of which produce a plausible wrong image
# rather than an error:
#   * alpha left at 0 by PrintWindow, which PNG then keeps as full transparency
#   * GetWindowRect DPI-virtualised, so the bitmap does not match the window
#   * bicubic resize bleeding a black border in from the edges
param()

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class PW {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left, Top, Right, Bottom; }
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

[void][PW]::SetProcessDPIAware()

$owners = @{}
foreach ($p in @(Get-Process -Name 'RobloxStudioBeta' -ErrorAction SilentlyContinue)) { $owners[[uint32]$p.Id] = $true }
Write-Host "RobloxStudioBeta processes: $($owners.Count)"

$fg = [PW]::GetForegroundWindow()
$cands = @()
foreach ($h in [PW]::All()) {
    $pid_ = [PW]::Pid($h)
    if (-not $owners.ContainsKey($pid_)) { continue }
    if (-not [PW]::IsWindowVisible($h)) { continue }
    if ([PW]::Cloaked($h)) { continue }
    $t = [PW]::Title($h)
    if ($t.IndexOf('Roblox Studio') -lt 0) { continue }
    $r = New-Object PW+RECT
    [void][PW]::GetWindowRect($h, [ref]$r)
    $w = $r.Right - $r.Left; $ht = $r.Bottom - $r.Top
    if ($w -le 0 -or $ht -le 0) { continue }
    $cands += [pscustomobject]@{ h=$h; pid=[int]$pid_; title=$t; w=$w; ht=$ht; area=$w*$ht; fg=($h -eq $fg); min=[PW]::IsIconic($h) }
}
Write-Host "candidate windows: $($cands.Count)"
foreach ($c in $cands) { Write-Host ("  pid={0} {1}x{2} min={3} fg={4} '{5}'" -f $c.pid, $c.w, $c.ht, $c.min, $c.fg, $c.title) }

if ($cands.Count -eq 0) { Write-Host "NO WINDOW"; exit 1 }

# Foreground wins, else topmost in Z-order; then the largest window of that pid.
$active = @($cands | Where-Object { $_.fg })
$ownerPid = if ($active.Count -gt 0) { $active[0].pid } else { $cands[0].pid }
$win = $cands | Where-Object { $_.pid -eq $ownerPid } | Sort-Object area -Descending | Select-Object -First 1
Write-Host "target: pid=$($win.pid) $($win.w)x$($win.ht) '$($win.title)'"

# 24bpp, because PrintWindow leaves alpha at 0 and PNG would keep that as
# transparency -- a fully transparent image with no error anywhere.
$out = Join-Path $PSScriptRoot 'pw_capture.png'
$times = @()
for ($i = 0; $i -lt 3; $i++) {
    $bmp = New-Object System.Drawing.Bitmap($win.w, $win.ht, [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $hdc = $g.GetHdc()
    try {
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $ok = [PW]::PrintWindow($win.h, $hdc, 2)   # PW_RENDERFULLCONTENT
        $sw.Stop()
        if (-not $ok) { Write-Host "PrintWindow FAILED err=$([Runtime.InteropServices.Marshal]::GetLastWin32Error())"; exit 2 }
        $times += [math]::Round($sw.Elapsed.TotalMilliseconds, 1)
    } finally { $g.ReleaseHdc($hdc); $g.Dispose() }
    $bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    Write-Host ("  run {0}: {1} ms  -> {2} B" -f ($i+1), $times[-1], (Get-Item $out).Length)
}
Write-Host "PrintWindow ms: $($times -join ', ')"

# Is it actually an image, or a black rectangle? Compare a downscaled copy
# against itself to force a decode, and report distinct-ish pixel spread.
$b = [System.Drawing.Bitmap]::FromFile($out)
$sum = 0L; $nonblack = 0
for ($y = 0; $y -lt $b.Height; $y += 7) {
    for ($x = 0; $x -lt $b.Width; $x += 7) {
        $c = $b.GetPixel($x, $y)
        $sum += $c.R + $c.G + $c.B
        if (($c.R + $c.G + $c.B) -gt 12) { $nonblack++ }
    }
}
$total = [math]::Ceiling($b.Height / 7) * [math]::Ceiling($b.Width / 7)
Write-Host ("sampled {0} px: mean sum={1}  non-black={2:P0}" -f $total, [math]::Round($sum / $total, 1), ($nonblack / $total))
$b.Dispose()
Write-Host "wrote $out"
