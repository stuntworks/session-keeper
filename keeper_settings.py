# Last modified: 2026-10-26 | Change: Dark or light screen colours saved as an append-only event too | Full history: git log
"""Session Keeper settings without replacing an existing file."""

import json
from datetime import datetime, timezone
from pathlib import Path

from keeper_core import DATA

SETTINGS_EVENTS = DATA / "settings_events.jsonl"


# WHAT IT DOES: Reads the most recent valid auto-restore choice.
# DEPENDS ON: Append-only settings event file.
# AFFECTS: Startup prompt behavior; invalid events are ignored.
def auto_restore_enabled(path=SETTINGS_EVENTS):
    state = False
    if not Path(path).exists():
        return state
    try:
        with Path(path).open("r", encoding="utf-8") as file:
            for line in file:
                try:
                    event = json.loads(line)
                    if type(event.get("auto_restore")) is bool:
                        state = event["auto_restore"]
                except json.JSONDecodeError:
                    continue
    except OSError:
        return False
    return state


# WHAT IT DOES: Adds a new opt-in choice without discarding earlier choices.
# DEPENDS ON: Writable Session Keeper data directory.
# AFFECTS: Next startup may begin a stoppable restore countdown.
def record_auto_restore(is_enabled, path=SETTINGS_EVENTS):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    event = {"at": datetime.now(timezone.utc).isoformat(),
             "auto_restore": bool(is_enabled)}
    with Path(path).open("a", encoding="utf-8") as file:
        file.write(json.dumps(event) + "\n")


THEMES = ("dark", "light")


# WHAT IT DOES: Reads the most recent valid screen-colour choice: "dark" (the default) or "light".
# DEPENDS ON: Append-only settings event file; a missing, unreadable or damaged file reads as "dark".
# AFFECTS: keeper_theme picks its palette from this once, when it is first imported.
def theme_mode(path=SETTINGS_EVENTS):
    mode = "dark"
    try:
        with Path(path).open("r", encoding="utf-8") as file:
            for line in file:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and event.get("theme") in THEMES:
                    mode = event["theme"]
    except OSError:
        return "dark"
    return mode


# WHAT IT DOES: Adds a screen-colour choice without discarding earlier settings.
# DEPENDS ON: Writable Session Keeper data directory; mode must be "dark" or "light".
# AFFECTS: The colours used the next time a Session Keeper window starts.
def record_theme(mode, path=SETTINGS_EVENTS):
    if mode not in THEMES:
        raise ValueError(f"Unknown theme {mode!r}")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    event = {"at": datetime.now(timezone.utc).isoformat(), "theme": mode}
    with Path(path).open("a", encoding="utf-8") as file:
        file.write(json.dumps(event) + "\n")
