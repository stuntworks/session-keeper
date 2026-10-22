# Last modified: 2026-10-03 | Change: Prepare a backed-up single-setting change for Terminal recovery | Full history: git log
"""Default is preview only. --apply requires the user's approval of this exact settings change."""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Every place Windows Terminal keeps its settings: Store, Preview, Canary, and unpackaged installs (scoop, zip, winget portable).
LOCATIONS = (
    ("Windows Terminal (Store)", "Packages/Microsoft.WindowsTerminal_8wekyb3d8bbwe/LocalState/settings.json"),
    ("Windows Terminal Preview", "Packages/Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe/LocalState/settings.json"),
    ("Windows Terminal Canary", "Packages/Microsoft.WindowsTerminalCanary_8wekyb3d8bbwe/LocalState/settings.json"),
    ("Windows Terminal (unpackaged)", "Microsoft/Windows Terminal/settings.json"),
)
SETTINGS = Path(os.environ.get("LOCALAPPDATA", ".")) / LOCATIONS[0][1]
BACKUPS = Path(__file__).resolve().parent / "data" / "terminal_settings_backups"
BAD_FILE_LOG = Path(__file__).resolve().parent / "data" / "watcher_errors.log"
RESTORE_PROPERTY = re.compile(rb'("firstWindowPreference"\s*:\s*")([^"\r\n]+)(")')


# WHAT IT DOES: Lists every Windows Terminal settings file that exists, as (label, path).
# DEPENDS ON: LOCALAPPDATA (or the base folder passed in).
# AFFECTS: Read-only. Preview and --apply act on each file found.
def settings_files(base=None):
    root = Path(base if base is not None else os.environ.get("LOCALAPPDATA", ""))
    if not str(root):
        return []
    return [(label, root / relative) for label, relative in LOCATIONS if (root / relative).is_file()]


# WHAT IT DOES: Reads the current Terminal setting without changing the file; with no path, true if any installed Terminal restores windows.
# DEPENDS ON: The current user's Windows Terminal settings file(s).
# AFFECTS: Automatic recovery is held when Terminal is also configured to restore windows.
def terminal_restore_enabled(path=None):
    if path is None:
        return any(restore_possibly_on(found) for _, found in settings_files())
    try:
        matches = list(RESTORE_PROPERTY.finditer(Path(path).read_bytes()))
        if len(matches) > 1:
            raise RuntimeError("Windows Terminal has duplicate restore settings")
        return bool(matches and matches[0].group(2) == b"persistedWindowLayout")
    except FileNotFoundError:
        return False


# WHAT IT DOES: Like terminal_restore_enabled for one file, but never raises: a duplicate key, unreadable file or
# bad content is logged and counts as True ("Terminal restore might be on"), so login waits for Terminal first.
# DEPENDS ON: terminal_restore_enabled; BAD_FILE_LOG (append-only).
# AFFECTS: One line in watcher_errors.log per bad file.
def restore_possibly_on(path):
    try:
        return terminal_restore_enabled(path)
    except Exception as error:
        try:
            BAD_FILE_LOG.parent.mkdir(parents=True, exist_ok=True)
            with BAD_FILE_LOG.open("a", encoding="utf-8") as file:
                file.write(f"{datetime.now().isoformat()} terminal settings {path}: {error}; treating restore as possibly on\n")
        except OSError:
            pass
        return True


# WHAT IT DOES: Prepares the exact one-value change while preserving every other byte.
# DEPENDS ON: A settings file with one persistedWindowLayout property.
# AFFECTS: Stops Terminal's old automatic restore while Session Keeper handles recovery.
def proposed_settings(original):
    matches = list(RESTORE_PROPERTY.finditer(original))
    if len(matches) != 1 or matches[0].group(2) != b"persistedWindowLayout":
        raise RuntimeError("Expected exactly one persistedWindowLayout setting; nothing changed")
    match = matches[0]
    return original[:match.start(2)] + b"defaultProfile" + original[match.end(2):]


# WHAT IT DOES: Backs up and rewrites one settings file with the approved single-setting change.
# DEPENDS ON: the user's approval before --apply and an unchanged source file during the write.
# AFFECTS: Windows Terminal startup behavior; backup and receipt record rollback evidence.
def apply_one(path, original, proposed, report, backups, tag):
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = backups / f"settings_before_{tag}{stamp}.json"
    with backup.open("xb") as file:
        file.write(original)
    temporary = path.with_name(f"session_keeper_{stamp}.tmp")
    with temporary.open("xb") as file:
        file.write(proposed)
        file.flush()
        os.fsync(file.fileno())
    if path.read_bytes() != original:
        temporary.unlink()
        raise RuntimeError("Terminal settings changed during preparation; source was not replaced")
    # DANGER ZONE HIGH: Replaces the settings file / breaks: Terminal startup behavior / depends on: explicit approval and saved backup.
    os.replace(temporary, path)
    if path.read_bytes() != proposed:
        raise RuntimeError(f"Settings verification failed. Backup: {backup}")
    report.update({"applied": True, "backup": str(backup),
                   "before_sha256": hashlib.sha256(original).hexdigest(),
                   "after_sha256": hashlib.sha256(proposed).hexdigest()})
    with (backups / f"receipt_{tag}{stamp}.json").open("x", encoding="utf-8") as file:
        json.dump(report, file, indent=2)


# WHAT IT DOES: Previews or applies the change to every settings file found; returns (reports, exit code).
# DEPENDS ON: settings_files; exit code is 1 when none is found or any file could not be changed.
# AFFECTS: Each file found is handled separately; one bad file never stops the others.
def run(apply, files, backups=BACKUPS):
    if not files:
        return [], 1
    reports, code = [], 0
    for number, (label, path) in enumerate(files):
        report = {"install": label, "file": str(path), "setting": "firstWindowPreference",
                  "before": "persistedWindowLayout", "after": "defaultProfile", "applied": False}
        try:
            original = Path(path).read_bytes()
            proposed = proposed_settings(original)
            if apply:
                apply_one(Path(path), original, proposed, report, backups, f"{number}_")
        except (RuntimeError, OSError) as error:
            report["error"] = str(error)
            code = 1
        reports.append(report)
    return reports, code


# WHAT IT DOES: One readable line for one file's report, so a bad file shows a clear message, not a traceback.
# ISOLATED
def describe(report):
    if "error" in report:
        return f"{report['install']}: {report['file']}: NOT CHANGED - {report['error']}"
    return json.dumps(report, indent=2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    reports, code = run(arguments.apply, settings_files())
    if not reports:
        print("No Windows Terminal settings file found (looked for Store, Preview, Canary and unpackaged installs). Nothing changed.")
    else:
        print("\n".join(describe(report) for report in reports))
    sys.exit(code)


if __name__ == "__main__":
    main()
