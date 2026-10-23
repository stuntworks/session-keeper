# Last modified: 2026-10-19 | Change: Plan adding missing tabs to their open window and asked-for occupied spots | Full history: git log
"""Session Keeper's read-only inventory and cautious restore decisions."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path


def is_glyph_char(ch):
    """True for whitespace, symbol/dingbat/emoji chars, and icon-font private-use codepoints."""
    if ch.isspace():
        return True
    if unicodedata.category(ch).startswith("S"):
        return True
    cp = ord(ch)
    if 0xE000 <= cp <= 0xF8FF:
        return True
    if 0xF0000 <= cp <= 0xFFFFD or 0x100000 <= cp <= 0x10FFFD:
        return True
    if 0x1F300 <= cp <= 0x1FAFF:
        return True
    if 0x2190 <= cp <= 0x2BFF:
        return True
    return False


def strip_leading_glyphs(title):
    i = 0
    while i < len(title) and is_glyph_char(title[i]):
        i += 1
    return title[i:].strip()


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
UIA_SCRIPT = ROOT / "spike_uia.ps1"
SESSION_ID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


# WHAT IT DOES: Runs PowerShell without asking another shell to reinterpret arguments.
# DEPENDS ON: Windows PowerShell and the supplied script arguments.
# AFFECTS: Window inventory and session inventory fail if the command fails.
def run_powershell(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", *arguments],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


# WHAT IT DOES: Reads terminal window positions and tab titles into a temporary file.
# DEPENDS ON: The existing read-only UI Automation probe.
# AFFECTS: Snapshots omit geometry if the probe cannot read it.
def read_windows() -> list[dict]:
    with tempfile.TemporaryDirectory(prefix="session_keeper_") as folder:
        output = Path(folder) / "windows.json"
        result = run_powershell(["-File", str(UIA_SCRIPT), "-OutFile", str(output)])
        if result.returncode or not output.exists():
            raise RuntimeError(f"Terminal probe failed: {result.stderr.strip()}")
        result_data = json.loads(output.read_text(encoding="utf-8"))
        if result_data.get("errors"):
            raise RuntimeError("A terminal window could not be inspected. Retry after it settles.")
        return result_data.get("windows", [])


# WHAT IT DOES: Gets a stable boot timestamp so a snapshot from this boot is not called a crash.
# DEPENDS ON: Windows CIM operating-system record.
# AFFECTS: Restart detection fails closed if CIM is unavailable.
def read_boot_id() -> str:
    result = run_powershell(["-Command", "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString('o')"])
    if result.returncode or not result.stdout.strip():
        raise RuntimeError("Cannot determine Windows boot time")
    return result.stdout.strip()


# WHAT IT DOES: Captures live Claude and Codex CLI sessions with their observed terminal groups.
# DEPENDS ON: Verified process records, console titles, and current Windows Terminal UI.
# AFFECTS: Restore eligibility and layout depend on this mapping.
def capture_snapshot() -> dict:
    windows = read_windows()
    from keeper_inventory import live_inventory, match_inventory
    sessions = live_inventory()
    report_windows, unmatched, counts = match_inventory(windows, sessions)
    import keeper_zorder
    try:   # "z" = front-to-back rank (0 = front); the list order alone is not a documented z-order
        keeper_zorder.add_z(report_windows, keeper_zorder.read_z_ranks({w.get("hwnd") for w in report_windows}))
    except (OSError, AttributeError, ValueError):
        pass   # no z: the map and restore fall back to list order
    snapshot = {
        "version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "boot_id": read_boot_id(),
        "windows": report_windows,
        "unmatched_sessions": unmatched,
        "counts": counts,
    }
    from keeper_display import read_monitors
    snapshot["monitors"] = read_monitors()
    return snapshot


# WHAT IT DOES: Saves a new immutable snapshot without replacing an earlier one.
# DEPENDS ON: Writable data directory and a JSON-serializable snapshot.
# AFFECTS: Recovery history is missing if this fails.
def save_snapshot(snapshot: dict, data_dir: Path = DATA) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = data_dir / f"snapshot_{stamp}_{uuid.uuid4().hex[:8]}.json"
    # DANGER ZONE FRAGILE: Exclusive create prevents replacement / breaks: history preservation / depends on: unique names.
    with path.open("x", encoding="utf-8") as file:
        json.dump(snapshot, file, ensure_ascii=False, indent=2)
    return path


# WHAT IT DOES: Builds selectable recovery rows, preserving window and tab order.
# DEPENDS ON: A version-one snapshot with captured groups.
# AFFECTS: The selection screen and restorer use these row keys.
def recovery_rows(snapshot: dict) -> list[dict]:
    rows = []
    for window_index, window in enumerate(snapshot.get("windows", [])):
        for tab_index, tab in enumerate(window.get("tabs", [])):
            if tab.get("role") in ("worker", "service", "unknown"):
                continue
            if tab.get("outcome") != "MATCHED" or not SESSION_ID.fullmatch(tab.get("sessionId", "")):
                continue
            # Never guess the folder: a tab with no saved folder stays shown but is not restorable.
            if not isinstance(tab.get("cwd"), str) or not tab["cwd"].strip():
                continue
            rows.append({"key": f"{window_index}:{tab_index}", "window_index": window_index,
                         "tab_index": tab_index, "session_id": tab["sessionId"],
                         "kind": tab.get("kind", "claude"),
                         "title": tab.get("strippedTitle") or "Claude session",
                         "cwd": tab["cwd"],
                         "window": window})
    return rows


# WHAT IT DOES: Checks selected rows against live sessions and occupied window locations.
# DEPENDS ON: Current process inventory, terminal window geometry, and optional live tab identities.
# AFFECTS: Prevents duplicate sessions and overlapping windows during restore.
# DANGER ZONE HIGH: owned_windows reuse / breaks: moves tabs into a stranger's window / depends on: exact hwnd, group and tab-title match.
# live_groups may be a list or a zero-argument reader; it is read only when a selected group has a live sibling.
# choices maps a saved window index to "on_top" or "close_empty" and is set only by an explicit user click.
# blocked, when a list, receives each occupied spot so the caller can offer to close empty terminals there.
def plan_restore(snapshot: dict, selected_keys: set[str], live_ids: set[str],
                 current_windows: list[dict], monitors=None,
                 owned_windows=None, live_groups=None, choices=None,
                 blocked=None) -> tuple[list[dict], list[str]]:
    all_rows = recovery_rows(snapshot)
    selected = [row for row in all_rows if row["key"] in selected_keys]
    by_window = {}
    warnings = []
    seen_ids = set()
    for row in selected:
        session_id = row["session_id"]
        if session_id in live_ids or session_id in seen_ids:
            warnings.append(f"Not opened twice: '{row['title']}' is already open (or was selected twice). "
                            "Nothing to fix; use its open tab.")
            continue
        seen_ids.add(session_id)
        by_window.setdefault(row["window_index"], []).append(row)
    plans = []
    reader = LazyGroups(live_groups)
    for window_index, rows in by_window.items():
        rect = saved_rect(rows, monitors, warnings)
        if rect is None:
            continue
        occupying = [window for window in current_windows if rectangles_overlap(rect, window)]
        verified_neighbors = True
        if occupying:
            owned_target, verified_neighbors = check_neighbors(snapshot, window_index, occupying, owned_windows)
            if owned_target:
                plans.append({"rect": rect, "tabs": rows, "window_name": owned_target["window_name"]})
                continue
        saved_ids = [row["session_id"] for row in all_rows if row["window_index"] == window_index]
        if live_groups is not None and set(saved_ids) & set(live_ids):
            from keeper_home import SPLIT, home_window
            home = home_window(saved_ids, live_ids, reader.get())
            if home == SPLIT:
                warnings.append(f"Not opened: '{rows[0]['title']}'. Its other tabs are open in more than one window, "
                                "so Session Keeper cannot tell which window it belongs in. To fix: drag those tabs "
                                "into one window, then press Restart selected again, or use Copy command for this tab.")
                continue
            if home:
                plans.append(add_plan(home, rows, owned_windows))
                continue
        if occupying and not verified_neighbors:
            plans.append(occupied_plan(window_index, rect, rows, occupying, choices, blocked, monitors))
            continue
        plans.append({"rect": rect, "tabs": rows})
    return plans, warnings


# WHAT IT DOES: Reads live tab identities at most once per plan, and only when asked.
# DEPENDS ON: A list or a zero-argument reader.
# AFFECTS: Groups without a live sibling never trigger the slower identity scan.
class LazyGroups:
    def __init__(self, source):
        self.source = source
        self.value = None

    def get(self):
        if self.value is None:
            self.value = self.source() if callable(self.source) else list(self.source or [])
        return self.value


# WHAT IT DOES: The fix-it line for a saved window whose position was never recorded.
def missing_position(title):
    return (f"Not opened: '{title}'. Its saved window position is missing, so Session Keeper does not know where "
            "to put it. To fix: select the tab and press Copy command, then paste it into any terminal.")


# WHAT IT DOES: Returns the saved rectangle to restore, moving minimised or off-screen windows onto a monitor.
# DEPENDS ON: The saved window position and current monitors.
# AFFECTS: Warnings explain every position change; None means the group is skipped.
def saved_rect(rows, monitors, warnings):
    from keeper_home import is_minimised, minimised_rect
    original = rows[0]["window"]
    rect = {key: original.get(key) for key in ("x", "y", "w", "h")}
    if all(type(value) is int for value in rect.values()) and is_minimised(rect):
        fallback = minimised_rect(monitors)
        if fallback is None:
            warnings.append(missing_position(rows[0]["title"]))
            return None
        warnings.append(f"Was minimised; opening on the main monitor: {rows[0]['title']}")
        return fallback
    if (not all(type(value) is int for value in rect.values())
            or rect["w"] < 200 or rect["h"] < 150):
        warnings.append(missing_position(rows[0]["title"]))
        return None
    if monitors is not None:
        from keeper_display import keep_visible
        visible = keep_visible(rect, monitors)
        if visible != rect:
            warnings.append(f"Saved monitor is unavailable; using the primary monitor: {rows[0]['title']}")
            rect = visible
    return rect


# WHAT IT DOES: Decides whether every window on a saved spot is a verified window from this same layout.
# DEPENDS ON: Created-window receipts and live tab titles.
# AFFECTS: Only exact same-snapshot receipts permit a saved overlap. Foreign windows still block.
def check_neighbors(snapshot, target_index, occupying, owned_windows):
    owned_target = None
    verified_neighbors = True
    for window in occupying:
        found = None
        for index in range(len(snapshot["windows"])):
            record = owned_window_for_group(snapshot, index, [window], owned_windows)
            if record:
                found = record
                if index == target_index:
                    if owned_target is not None:
                        verified_neighbors = False
                    owned_target = record
                break
        if found is None:
            verified_neighbors = False
    return (owned_target if verified_neighbors else None), verified_neighbors


# WHAT IT DOES: Builds the plan that adds missing tabs into their group's still-open window.
# DEPENDS ON: The verified home window and this boot's created-window receipts.
# AFFECTS: A Session Keeper-made window is targeted by name; any other by its exact handle.
def add_plan(home, rows, owned_windows):
    plan = {"rect": {key: home.get(key) for key in ("x", "y", "w", "h")}, "tabs": rows,
            "mode": "add", "target_hwnd": home["hwnd"]}
    record = next((o for o in owned_windows or [] if o.get("hwnd") == home["hwnd"] and o.get("window_name")), None)
    if record:
        plan["window_name"] = record["window_name"]
    return plan


# WHAT IT DOES: Plans a spot covered by another window: open on top of it, or close it on the user's explicit choice.
# DEPENDS ON: choices from the confirm dialog ("close_empty" only ever comes from a click).
# AFFECTS: Owner rule 2026-10-22: a covered spot never blocks; nothing closes without a recorded click.
def occupied_plan(window_index, rect, rows, occupying, choices, blocked, monitors=None):
    if blocked is not None:
        blocked.append({"window_index": window_index, "title": rows[0]["title"], "rect": rect,
                        "occupants": [dict(window) for window in occupying]})
    if (choices or {}).get(window_index) == "close_empty":
        return {"rect": rect, "tabs": rows, "close_hwnds": [window.get("hwnd") for window in occupying]}
    return {"rect": rect, "tabs": rows, "on_top": True,
            "covering": "; ".join(describe_window(window, monitors) for window in occupying)}


# WHAT IT DOES: Names a window in plain words: its tab titles and which monitor it is on.
# DEPENDS ON: Probe tab titles (strings or tab dicts) and current monitors.
# AFFECTS: Text only.
def describe_window(window, monitors=None):
    titles = [strip_leading_glyphs(tab if isinstance(tab, str) else (tab.get("strippedTitle") or tab.get("rawTitle") or ""))
              for tab in window.get("tabs") or []]
    titles = [title for title in titles if title]
    name = "the window with tabs " + ", ".join(f"'{title}'" for title in titles[:3]) if titles else "an untitled window"
    if len(titles) > 3:
        name += f" and {len(titles) - 3} more"
    where = monitor_label(window, monitors)
    return f"{name} on {where}" if where else name


# WHAT IT DOES: Says which monitor (1, 2, ...) holds a rectangle's centre, or "" when unknown.
# DEPENDS ON: Monitors in keeper_display.read_monitors order.
# AFFECTS: Text only.
def monitor_label(rect, monitors):
    try:
        cx, cy = rect["x"] + rect["w"] / 2, rect["y"] + rect["h"] / 2
    except (KeyError, TypeError):
        return ""
    for number, monitor in enumerate(monitors or [], start=1):
        if monitor["x"] <= cx < monitor["x"] + monitor["w"] and monitor["y"] <= cy < monitor["y"] + monitor["h"]:
            return f"monitor {number}"
    return ""


# WHAT IT DOES: Finds the Session Keeper-made window that is the only occupant of a saved spot and holds only that group's tabs.
# DEPENDS ON: Recorded created windows, live hwnd, and live tab titles versus the saved group's titles.
# AFFECTS: Any other occupant returns None, so it is skipped and never touched.
def owned_window_for_group(snapshot, window_index, occupying, owned_windows):
    if len(occupying) != 1 or not owned_windows:
        return None
    group = f"{snapshot.get('captured_at', 'unknown')}#{window_index}"
    window = occupying[0]
    record = next((o for o in owned_windows
                   if o.get("hwnd") == window.get("hwnd") and o.get("group") == group), None)
    tabs = window.get("tabs")
    if not record or not tabs or not all(isinstance(tab, str) for tab in tabs):
        return None
    saved = [strip_leading_glyphs(t.get("strippedTitle") or "").casefold()
             for t in snapshot["windows"][window_index].get("tabs", [])]
    saved = [title for title in saved if title]
    for tab in tabs:
        live = strip_leading_glyphs(tab).casefold()
        if not live or not any(live.startswith(t) or t.startswith(live) for t in saved):
            return None
    return record


# WHAT IT DOES: Reports whether two window rectangles overlap in a meaningful area.
# DEPENDS ON: Integer x, y, w, h values.
# AFFECTS: Too much tolerance risks stacking; too little skips safe restores.
def rectangles_overlap(first: dict, second: dict) -> bool:
    try:
        left = max(first["x"], second["x"])
        top = max(first["y"], second["y"])
        width = min(first["x"] + first["w"], second["x"] + second["w"]) - left
        height = min(first["y"] + first["h"], second["y"] + second["h"]) - top
        return width > 80 and height > 80
    except (KeyError, TypeError):
        return True
