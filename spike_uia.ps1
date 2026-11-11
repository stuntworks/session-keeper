# Last modified: 2026-11-10 | Change: With -Physical each window also says whether it is maximised | Full history: git log
# spike_uia.ps1 — read-only UIA probe of Windows Terminal windows + tabs.
# Writes JSON to the path given as the first argument (UTF-8, no BOM), to
# avoid console-codepage mangling of emoji/status-glyph tab titles.
# Emits: { "windows": [ { hwnd, x, y, w, h, title, tabs: [ "..." ] }, ... ] }
# With -Physical: rects are per-monitor-v2 physical pixels (same as the monitor list and MoveWindow)
# and the output also carries "coords": "physical", and each window "maximized" (true/false, IsZoomed).
# Without it: system-DPI-aware rects, unchanged,
# so an older Session Keeper still running keeps writing exactly what it always wrote.
# Does NOT launch, close, move, resize, or focus anything.

param(
    [Parameter(Mandatory = $true)]
    [string]$OutFile,
    [switch]$Physical
)

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class SpikeNative {
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
    [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")] public static extern bool IsZoomed(IntPtr hWnd);
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
}
"@

[void][SpikeNative]::SetProcessDPIAware()
# DANGER ZONE FRAGILE: Window rects must match keeper_display monitors / breaks: restore spots / depends on: -4 = per-monitor-v2.
if ($Physical -and [SpikeNative]::SetThreadDpiAwarenessContext([IntPtr]::new(-4)) -eq [IntPtr]::Zero) {
    throw 'Cannot switch to per-monitor DPI awareness'
}
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

$root = [System.Windows.Automation.AutomationElement]::RootElement
$classCond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ClassNameProperty,
    "CASCADIA_HOSTING_WINDOW_CLASS"
)
$wtWindows = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $classCond)

$tabCond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::TabItem
)

$result = @()
$failures = @()

foreach ($w in $wtWindows) {
    try {
        $hwnd = [IntPtr]$w.Current.NativeWindowHandle
        $rect = New-Object SpikeNative+RECT
        if (-not [SpikeNative]::GetWindowRect($hwnd, [ref]$rect)) { throw 'Cannot read terminal rectangle' }

        $tabs = $w.FindAll([System.Windows.Automation.TreeScope]::Descendants, $tabCond)
        $tabTitles = @()
        foreach ($t in $tabs) {
            $tabTitles += [string]$t.Current.Name
        }

        $row = [PSCustomObject]@{
            hwnd  = $hwnd.ToInt64()
            x     = $rect.Left
            y     = $rect.Top
            w     = $rect.Right - $rect.Left
            h     = $rect.Bottom - $rect.Top
            title = [string]$w.Current.Name
            tabs  = $tabTitles
        }
        # Read-only state query; restore maximises the window again after placing it at its monitor's work area.
        if ($Physical) { $row | Add-Member -NotePropertyName maximized -NotePropertyValue ([SpikeNative]::IsZoomed($hwnd)) }
        $result += $row
    } catch {
        # DANGER ZONE HIGH: An unreadable window may occupy the restore spot / breaks: collision checks / depends on: caller rejecting errors.
        $failures += $_.Exception.Message
        continue
    }
}

$out = [PSCustomObject]@{ windows = $result; errors = $failures }
if ($Physical) { $out | Add-Member -NotePropertyName coords -NotePropertyValue 'physical' }
$json = ConvertTo-Json -InputObject $out -Depth 8 -Compress
[System.IO.File]::WriteAllText($OutFile, $json, (New-Object System.Text.UTF8Encoding($false)))
