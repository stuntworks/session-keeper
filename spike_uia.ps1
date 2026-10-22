# Last modified: 2026-10-03 | Change: Report unreadable windows so restore fails closed | Full history: git log
# spike_uia.ps1 — read-only UIA probe of Windows Terminal windows + tabs.
# Writes JSON to the path given as the first argument (UTF-8, no BOM), to
# avoid console-codepage mangling of emoji/status-glyph tab titles.
# Emits: { "windows": [ { hwnd, x, y, w, h, title, tabs: [ "..." ] }, ... ] }
# Does NOT launch, close, move, resize, or focus anything.

param(
    [Parameter(Mandatory = $true)]
    [string]$OutFile
)

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class SpikeNative {
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
}
"@

[void][SpikeNative]::SetProcessDPIAware()
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

        $result += [PSCustomObject]@{
            hwnd  = $hwnd.ToInt64()
            x     = $rect.Left
            y     = $rect.Top
            w     = $rect.Right - $rect.Left
            h     = $rect.Bottom - $rect.Top
            title = [string]$w.Current.Name
            tabs  = $tabTitles
        }
    } catch {
        # DANGER ZONE HIGH: An unreadable window may occupy the restore spot / breaks: collision checks / depends on: caller rejecting errors.
        $failures += $_.Exception.Message
        continue
    }
}

$out = [PSCustomObject]@{ windows = $result; errors = $failures }
$json = ConvertTo-Json -InputObject $out -Depth 8 -Compress
[System.IO.File]::WriteAllText($OutFile, $json, (New-Object System.Text.UTF8Encoding($false)))
