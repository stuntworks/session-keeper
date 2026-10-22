# Last modified: 2026-10-19 | Change: Also create a desktop shortcut to the recovery screen | Full history: git log
# WHAT IT DOES: Creates a new login shortcut for Session Keeper's background watcher.
# DEPENDS ON: pythonw.exe, keeper_app.py, and the current user's Startup folder.
# AFFECTS: Session Keeper will record snapshots and offer recovery at Windows login.
param([switch]$Apply)

$app = Join-Path $PSScriptRoot 'keeper_app.py'
$python = (Get-Command pythonw.exe -ErrorAction Stop).Source
$startup = [Environment]::GetFolderPath('Startup')
$shortcut = Join-Path $startup 'Session Keeper.lnk'
$programs = [Environment]::GetFolderPath('Programs')
$recoveryShortcut = Join-Path $programs 'Session Keeper.lnk'
$desktopShortcut = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Session Keeper.lnk'

if (-not (Test-Path -LiteralPath $app)) { throw 'Session Keeper app is missing' }
if (Test-Path -LiteralPath $shortcut) { throw 'Startup shortcut already exists; refusing to overwrite it' }
if (Test-Path -LiteralPath $recoveryShortcut) { throw 'Recovery shortcut already exists; refusing to overwrite it' }
if (Test-Path -LiteralPath $desktopShortcut) { throw 'Desktop shortcut already exists; refusing to overwrite it' }

Write-Output "New shortcut: $shortcut"
Write-Output "Target: $python"
Write-Output "Arguments: `"$app`" watch"
Write-Output "Recovery menu shortcut: $recoveryShortcut"
Write-Output "Desktop shortcut: $desktopShortcut"
if (-not $Apply) { Write-Output 'Preview only. No shortcut created.'; return }

# DANGER ZONE HIGH: Adds a login program / breaks: startup behavior / depends on: Python and app path.
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($shortcut)
$link.TargetPath = $python
$link.Arguments = "`"$app`" watch"
$link.WorkingDirectory = $PSScriptRoot
$link.Description = 'Record Claude and Codex terminal layout and offer crash recovery'
$link.Save()
$recoveryLink = $shell.CreateShortcut($recoveryShortcut)
$recoveryLink.TargetPath = $python
$recoveryLink.Arguments = "`"$app`" recover"
$recoveryLink.WorkingDirectory = $PSScriptRoot
$recoveryLink.Description = 'Choose a saved terminal layout and recover selected conversations'
$recoveryLink.Save()
$desktopLink = $shell.CreateShortcut($desktopShortcut)
$desktopLink.TargetPath = $python
$desktopLink.Arguments = "`"$app`" recover"
$desktopLink.WorkingDirectory = $PSScriptRoot
$desktopLink.Description = 'Choose a saved terminal layout and recover selected conversations'
$desktopLink.Save()
Write-Output 'Session Keeper will start at the next Windows login.'
