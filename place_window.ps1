# Last modified: 2026-11-10 | Change: -Maximize maximises after placing; a failed maximise exits 3 (placed, warning only) | Full history: git log
# WHAT IT DOES: Moves one supplied Windows Terminal handle and checks the result.
# DEPENDS ON: A handle identified as new by keeper_restore.py and the Win32 window API.
# AFFECTS: Moves the supplied window; must never receive a pre-existing handle. -Maximize (a window saved
# maximised): after the checked move, maximise it on the monitor it now sits on. Exit 3 = placed, not maximised.
param(
    [Parameter(Mandatory=$true)][long]$Handle,
    [Parameter(Mandatory=$true)][int]$X,
    [Parameter(Mandatory=$true)][int]$Y,
    [Parameter(Mandatory=$true)][int]$Width,
    [Parameter(Mandatory=$true)][int]$Height,
    [switch]$Maximize
)

if ($Width -lt 200 -or $Height -lt 150) { throw 'Refusing invalid window size' }

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class KeeperNative {
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int W, int H, bool repaint);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rectangle);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int command);
    [DllImport("user32.dll")] public static extern bool IsZoomed(IntPtr hWnd);
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
}
"@

$pointer = [IntPtr]::new($Handle)
[void][KeeperNative]::SetProcessDPIAware()
# DANGER ZONE FRAGILE: Both monitors use physical pixel coordinates / breaks: exact placement / depends on: DPI context.
[void][KeeperNative]::SetThreadDpiAwarenessContext([IntPtr]::new(-4))
if (-not [KeeperNative]::IsWindow($pointer)) { throw 'Window handle no longer exists' }

# DANGER ZONE HIGH: Moves a live UI window / breaks: layout / depends on: Python verifying this handle is new.
if (-not [KeeperNative]::MoveWindow($pointer, $X, $Y, $Width, $Height, $true)) {
    throw 'MoveWindow failed'
}
$actual = New-Object KeeperNative+RECT
if (-not [KeeperNative]::GetWindowRect($pointer, [ref]$actual)) { throw 'Cannot verify window position' }
if ([Math]::Abs($actual.Left - $X) -gt 2 -or [Math]::Abs($actual.Top - $Y) -gt 2 -or
    [Math]::Abs(($actual.Right - $actual.Left) - $Width) -gt 2 -or
    [Math]::Abs(($actual.Bottom - $actual.Top) - $Height) -gt 2) {
    throw 'Window landed too far from its recorded position'
}

if ($Maximize) {
    [void][KeeperNative]::ShowWindow($pointer, 3)   # 3 = SW_MAXIMIZE, on the monitor the window was just moved to
    $tries = 0
    while (-not [KeeperNative]::IsZoomed($pointer) -and $tries -lt 20) { Start-Sleep -Milliseconds 100; $tries++ }
    # Exit 3 = placed and checked, only the maximise did not take: Python reports it as a warning, not a failure.
    if (-not [KeeperNative]::IsZoomed($pointer)) { [Console]::Error.WriteLine('Window was placed but did not maximise'); exit 3 }
}
