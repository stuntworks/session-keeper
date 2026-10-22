# Last modified: 2026-10-03 | Change: Prepare a backed-up single-setting change for Terminal recovery | Full history: git log
"""Default is preview only. --apply requires the user's approval of this exact settings change."""

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

SETTINGS = Path(os.environ["LOCALAPPDATA"]) / "Packages/Microsoft.WindowsTerminal_8wekyb3d8bbwe/LocalState/settings.json"
BACKUPS = Path(__file__).resolve().parent / "data" / "terminal_settings_backups"
RESTORE_PROPERTY = re.compile(rb'("firstWindowPreference"\s*:\s*")([^"\r\n]+)(")')


# WHAT IT DOES: Reads the current Terminal setting without changing the file.
# DEPENDS ON: The current user's Windows Terminal settings file.
# AFFECTS: Automatic recovery is held when Terminal is also configured to restore windows.
def terminal_restore_enabled(path=SETTINGS):
    try:
        matches = list(RESTORE_PROPERTY.finditer(Path(path).read_bytes()))
        if len(matches) > 1:
            raise RuntimeError("Windows Terminal has duplicate restore settings")
        return bool(matches and matches[0].group(2) == b"persistedWindowLayout")
    except FileNotFoundError:
        return False


# WHAT IT DOES: Prepares the exact one-value change while preserving every other byte.
# DEPENDS ON: A settings file with one persistedWindowLayout property.
# AFFECTS: Stops Terminal's old automatic restore while Session Keeper handles recovery.
def proposed_settings(original):
    matches = list(RESTORE_PROPERTY.finditer(original))
    if len(matches) != 1 or matches[0].group(2) != b"persistedWindowLayout":
        raise RuntimeError("Expected exactly one persistedWindowLayout setting; nothing changed")
    match = matches[0]
    return original[:match.start(2)] + b"defaultProfile" + original[match.end(2):]


# WHAT IT DOES: Previews or applies the approved single-setting change with a byte-for-byte backup.
# DEPENDS ON: the user's approval before --apply and an unchanged source file during the write.
# AFFECTS: Windows Terminal startup behavior; backup and receipt record rollback evidence.
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    original = SETTINGS.read_bytes()
    proposed = proposed_settings(original)
    report = {"file": str(SETTINGS), "setting": "firstWindowPreference",
              "before": "persistedWindowLayout", "after": "defaultProfile", "applied": False}
    if arguments.apply:
        BACKUPS.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = BACKUPS / f"settings_before_{stamp}.json"
        with backup.open("xb") as file:
            file.write(original)
        temporary = SETTINGS.with_name(f"session_keeper_{stamp}.tmp")
        with temporary.open("xb") as file:
            file.write(proposed)
            file.flush()
            os.fsync(file.fileno())
        if SETTINGS.read_bytes() != original:
            raise RuntimeError("Terminal settings changed during preparation; source was not replaced")
        # DANGER ZONE HIGH: Replaces the settings file / breaks: Terminal startup behavior / depends on: explicit approval and saved backup.
        os.replace(temporary, SETTINGS)
        if SETTINGS.read_bytes() != proposed:
            raise RuntimeError(f"Settings verification failed. Backup: {backup}")
        report.update({"applied": True, "backup": str(backup),
                       "before_sha256": hashlib.sha256(original).hexdigest(),
                       "after_sha256": hashlib.sha256(proposed).hexdigest()})
        with (BACKUPS / f"receipt_{stamp}.json").open("x", encoding="utf-8") as file:
            json.dump(report, file, indent=2)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
