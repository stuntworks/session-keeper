# Last modified: 2026-10-20 | Change: resume_program shared with Copy command | Full history: git log
"""Launch selected absent CLI sessions and position only their verified new windows."""

import os
import subprocess
import time
import uuid
from pathlib import Path
from keeper_locks import exclusive_operation
from keeper_display import read_monitors

from keeper_core import (ROOT, plan_restore, read_boot_id,
                         read_windows, strip_leading_glyphs)
from keeper_created import group_key, load_created_windows, record_created_window

PLACE_SCRIPT = ROOT / "place_window.ps1"
STRICT_WAIT_SECONDS = 30
STRICT_POLL_SECONDS = 2.5


# WHAT IT DOES: Removes inherited child markers and no-color flags for interactive recovery.
# DEPENDS ON: The current process environment.
# AFFECTS: Inherited Claude child markers can silently turn transcript saving off.
def clean_environment():
    return {key: value for key, value in os.environ.items()
            if not key.upper().startswith("CLAUDE")
            and key.upper() not in {"CODEX_SESSION_ID", "CODEX_THREAD_ID", "CODEX_CI", "NO_COLOR"}
            and not (key.upper() == "TERM" and value.lower() == "dumb")
            and not (key.upper() == "COLORTERM" and not value)}


# WHAT IT DOES: Lists verified live Claude and Codex IDs, optionally rejecting unresolved CLI processes.
# DEPENDS ON: Current process inventory and session records.
# AFFECTS: Duplicate prevention fails if this inventory is stale.
# DANGER ZONE HIGH: Strict wait / breaks: duplicate sessions if it stops failing closed / depends on: a just-launched CLI registering within STRICT_WAIT_SECONDS.
def live_session_ids(strict=False):
    from keeper_inventory import live_inventory
    deadline = time.monotonic() + STRICT_WAIT_SECONDS
    while True:
        sessions = live_inventory(read_titles=False)
        if not (strict and any(session.get("uncertain") and session.get("role") not in ("worker", "service") for session in sessions)):
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("An open CLI session could not be identified. Recovery is paused to avoid duplicating it.")
        time.sleep(STRICT_POLL_SECONDS)
    return {session["sessionId"] for session in sessions if session.get("sessionId")}


# WHAT IT DOES: Waits for exactly one new Windows Terminal handle after launch.
# DEPENDS ON: UI Automation being able to see the launched window.
# AFFECTS: Refuses placement if another terminal appears at the same time.
def find_new_window(previous_handles, expected_title, timeout_seconds=15):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        new_windows = [window for window in read_windows()
                       if window.get("hwnd") not in previous_handles]
        if len(new_windows) == 1 and expected_title.casefold() in strip_leading_glyphs(
                new_windows[0].get("title") or "").casefold():
            return new_windows[0]
        if len(new_windows) > 1:
            raise RuntimeError("Multiple new terminal windows appeared; position is uncertain")
        time.sleep(0.5)
    raise RuntimeError("New terminal window was not detected")


# WHAT IT DOES: Moves one verified new window to the recorded rectangle.
# DEPENDS ON: place_window.ps1 and a handle never present before launch.
# AFFECTS: A bad handle could move an existing terminal, so identity is checked twice.
def place_new_window(handle, rectangle, previous_handles):
    if handle in previous_handles:
        raise RuntimeError("Refusing to move a pre-existing terminal")
    arguments = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                 str(PLACE_SCRIPT), "-Handle", str(handle), "-X", str(rectangle["x"]),
                 "-Y", str(rectangle["y"]), "-Width", str(rectangle["w"]),
                 "-Height", str(rectangle["h"])]
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=20,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError(f"Could not position new terminal: {result.stderr.strip()}")


# WHAT IT DOES: Returns the exact CLI words that resume one saved conversation.
# DEPENDS ON: row["kind"] ("claude" or "codex") and row["session_id"].
# AFFECTS: Nothing itself; launch_tab and the Copy command button both use it so the format is never duplicated.
def resume_program(row):
    if row["kind"] == "codex":
        return ["codex", "resume", row["session_id"]]
    if row["kind"] == "claude":
        return ["claude", "-r", row["session_id"]]
    raise RuntimeError("Unknown session type")


# WHAT IT DOES: Opens the specified Claude or Codex conversation in a named terminal group.
# DEPENDS ON: wt.exe, cmd.exe, the requested CLI, and a validated session row.
# AFFECTS: Starts a new process attached to the selected conversation's saved history.
def launch_tab(window_name, row):
    from keeper_core import SESSION_ID
    if not SESSION_ID.fullmatch(row["session_id"]):
        raise ValueError("Invalid session ID")
    if not Path(row["cwd"]).is_dir() or any(character in row["cwd"] for character in (';', '\n', '\r', '"')):
        raise ValueError("Saved project folder is missing or cannot be passed safely to Windows Terminal")
    title = row["title"][:70].replace(';', ',').replace('\n', ' ').replace('\r', ' ') or "Recovered session"
    program = resume_program(row)
    arguments = ["wt.exe", "-w", window_name, "new-tab", "-d", row["cwd"],
                 "--title", title, "cmd.exe", "/k", *program]
    # DANGER ZONE HIGH: Clean inherited Claude markers / breaks: transcript saving / depends on: child environment.
    subprocess.Popen(arguments, env=clean_environment(), close_fds=True,
                     creationflags=subprocess.CREATE_NO_WINDOW)


# WHAT IT DOES: Confirms the requested conversation appears in the live process inventory.
# DEPENDS ON: Claude and Codex process records updating after launch.
# AFFECTS: The success report counts only conversations actually resumed.
def wait_for_session(session_id, timeout_seconds=20):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if session_id in live_session_ids():
            return True
        time.sleep(0.75)
    return False


# WHAT IT DOES: Reads current terminal windows with each tab tied to its exact live session, when provable.
# DEPENDS ON: The live process inventory with console titles and the window probe result supplied.
# AFFECTS: Used only to find a group's still-open window and to classify windows on saved spots.
def read_live_groups(windows):
    from keeper_inventory import live_inventory, match_inventory
    return match_inventory(windows, live_inventory())[0]


# WHAT IT DOES: Finds occupied saved spots for the selection and labels each covering window live, empty or unknown.
# DEPENDS ON: The same planner as restore, plus read-only process and window scans.
# AFFECTS: Nothing is opened or closed; the result feeds the confirm dialog.
def assess_restore(snapshot, selected_keys):
    with exclusive_operation("restore"):
        return find_blocked(snapshot, selected_keys)


# WHAT IT DOES: Runs the planner once to collect blocked spots, then classifies their occupants.
# DEPENDS ON: Window probe, live inventory, monitors and created-window receipts.
# AFFECTS: Read-only.
def find_blocked(snapshot, selected_keys, live_ids=None, windows=None):
    from keeper_occupancy import classify_blocked, read_shells
    windows = read_windows() if windows is None else windows
    live_ids = live_session_ids(strict=True) if live_ids is None else live_ids
    blocked = []
    groups = LazyReader(lambda: read_live_groups(windows))
    plan_restore(snapshot, selected_keys, live_ids, windows, read_monitors(),
                 load_created_windows(read_boot_id()), live_groups=groups.get, blocked=blocked)
    if blocked:
        classify_blocked(blocked, groups.get(), read_shells())
    return blocked


# WHAT IT DOES: Caches one slow read so planning and classification share it.
# DEPENDS ON: A zero-argument reader.
# AFFECTS: One identity scan per planning pass.
class LazyReader:
    def __init__(self, reader):
        self.reader = reader
        self.value = None

    def get(self):
        if self.value is None:
            self.value = self.reader()
        return self.value


# WHAT IT DOES: Writes plain-language Preview lines for spots that a real restore would ask about.
# DEPENDS ON: Classified blocked spots.
# AFFECTS: Preview text only.
def describe_blocked(blocked):
    lines = []
    for spot in blocked:
        number = spot["window_index"] + 1
        states = [occupant.get("state") for occupant in spot["occupants"]]
        tabs = ", ".join(tab for occupant in spot["occupants"] for tab in occupant.get("tabs") or []) or "no tabs"
        if states and all(state == "empty" for state in states):
            lines.append(f"Window {number}'s spot is covered by an empty-looking terminal ({tabs}). "
                         f"Restart selected will ask before closing it: {spot['title']}")
        else:
            lines.append(f"Window {number}'s spot is covered by a terminal that is in use or unknown ({tabs}). "
                         f"It opens there only if you click 'Open on top anyway': {spot['title']}")
    return lines


# WHAT IT DOES: Restores selected groups only where the saved position is free, or where the user explicitly chose.
# DEPENDS ON: Fresh window and process checks before each new window and tab.
# AFFECTS: Starts Claude processes, moves only windows created by this run, closes only approved empty windows.
def perform_restore(snapshot, selected_keys, dry_run=False, choices=None, approved_close=()):
    monitors = read_monitors()
    boot_id = read_boot_id()
    owned = load_created_windows(boot_id)
    blocked = []
    try:
        windows = read_windows()
        live = live_session_ids(strict=True)
        groups = LazyReader(lambda: read_live_groups(windows))
        plans, warnings = plan_restore(snapshot, selected_keys, live, windows, monitors, owned,
                                       live_groups=groups.get, choices=choices, blocked=blocked)
    except RuntimeError as error:
        return [], [f"Recovery paused: {error}"]
    # Back windows first, front window last: each new window opens on top, so a window that was hidden
    # behind another ends up behind it again. Only the order changes; the plans themselves do not.
    from keeper_zorder import back_to_front
    plans = back_to_front(plans, snapshot)
    if dry_run and blocked:
        from keeper_occupancy import classify_blocked, read_shells
        try:
            warnings.extend(describe_blocked(classify_blocked(blocked, groups.get(), read_shells())))
        except RuntimeError as error:
            warnings.append(f"Could not check the covering windows: {error}")
    restored = []
    state = {"adds_stopped": False}
    for plan in plans:
        rows = plan["tabs"]
        if read_monitors() != monitors:
            warnings.append("Monitor layout changed during recovery. Retry after the displays settle.")
            break
        if plan.get("close_hwnds"):
            if dry_run:
                restored.extend(f"Would close the empty terminal and open in its place: {row['title']}" for row in rows)
                continue
            if not free_spot(plan, approved_close, warnings):
                continue
        try:
            windows = read_windows()
            groups = LazyReader(lambda: read_live_groups(windows))
            fresh_plans, fresh_warnings = plan_restore(snapshot, {row["key"] for row in rows},
                live_session_ids(strict=True), windows, monitors, owned,
                live_groups=groups.get, choices=choices)
        except RuntimeError as error:
            warnings.append(f"Stopped before {rows[0]['title']}: {error}")
            continue
        warnings.extend(fresh_warnings)
        if not fresh_plans:
            continue
        fresh = fresh_plans[0]
        if fresh.get("mode") == "add":
            add_to_open_window(fresh, dry_run, restored, warnings, state)
            continue
        if fresh.get("close_hwnds"):
            warnings.append(f"Skipped: its spot is still covered: {rows[0]['title']}")
            continue
        if dry_run:
            prefix = "Would open on top of the covered spot: " if fresh.get("on_top") else ""
            restored.extend(prefix + row["title"] for row in fresh["tabs"])
            continue
        open_group(snapshot, fresh, owned, boot_id, restored, warnings)
    return restored, warnings


# WHAT IT DOES: Closes the approved empty windows on one spot before its group is opened there.
# DEPENDS ON: keeper_occupancy.close_empty_spot with fresh probes.
# AFFECTS: Returns False (spot skipped) unless every covering window closed and no live chat was lost.
def free_spot(plan, approved_close, warnings):
    from keeper_occupancy import close_empty_spot, read_shells
    title = plan["tabs"][0]["title"]
    try:
        freed, reason = close_empty_spot(plan["close_hwnds"], set(approved_close), read_windows,
                                         read_live_groups, read_shells, lambda: live_session_ids(strict=True))
    except RuntimeError as error:
        warnings.append(f"Stopped before {title}: {error}")
        return False
    if not freed:
        warnings.append(f"Skipped {title}: {reason}")
    return freed


# WHAT IT DOES: Adds each missing tab into its group's open window, verifying every landing.
# DEPENDS ON: keeper_target.add_tab and strict live checks before each launch.
# AFFECTS: A missed landing stops all further adds to open windows for this run.
def add_to_open_window(plan, dry_run, restored, warnings, state):
    import keeper_target
    for row in plan["tabs"]:
        if dry_run:
            restored.append(f"Would add to its open window: {row['title']}")
            continue
        if state["adds_stopped"]:
            warnings.append(f"Not added (stopped after an uncertain tab): {row['title']}")
            continue
        try:
            if row["session_id"] in live_session_ids(strict=True):
                warnings.append(f"Skipped already open: {row['title']}")
                continue
            verified = keeper_target.add_tab(plan["target_hwnd"], row, plan.get("window_name"),
                                             launch_tab, wait_for_session, read_windows)
        except keeper_target.TargetMissed as error:
            state["adds_stopped"] = True
            warnings.append(str(error))
            continue
        except Exception as error:
            warnings.append(f"Could not add {row['title']} to its open window: {error}")
            break
        if verified:
            restored.append(row["title"])
        else:
            warnings.append(f"Opened a tab but could not verify its session: {row['title']}")


# WHAT IT DOES: Opens one group in a new (or Session Keeper-owned) window at its saved spot.
# DEPENDS ON: Launch, new-window detection, placement, and strict live checks.
# AFFECTS: Starts processes and moves only the window this run created.
def open_group(snapshot, plan, owned, boot_id, restored, warnings):
    rows = plan["tabs"]
    existing_window = plan.get("window_name")
    previous_handles = {window.get("hwnd") for window in read_windows()}
    window_name = existing_window or "SessionKeeper-" + uuid.uuid4().hex
    first = rows[0]
    if not existing_window:
        try:
            launch_tab(window_name, first)
            expected = first["title"][:70].replace(';', ',').replace('\n', ' ').replace('\r', ' ') or "Recovered session"
            new_window = find_new_window(previous_handles, expected)
            try:
                record_created_window(window_name, new_window["hwnd"], group_key(snapshot, first["window_index"]), boot_id)
                owned.append({"window_name": window_name, "hwnd": new_window["hwnd"],
                              "group": group_key(snapshot, first["window_index"]), "boot_id": boot_id})
            except OSError as error:
                warnings.append(f"Could not record the new window, so it cannot be refilled later: {error}")
            place_new_window(new_window["hwnd"], plan["rect"], previous_handles)
            if wait_for_session(first["session_id"]):
                restored.append(first["title"])
            else:
                warnings.append(f"Opened a tab but could not verify its session: {first['title']}")
        except Exception as error:
            warnings.append(f"Could not restore {first['title']}: {error}")
            return
    for row in (rows if existing_window else rows[1:]):
        try:
            if row["session_id"] in live_session_ids(strict=True):
                warnings.append(f"Skipped already open: {row['title']}")
                continue
        except RuntimeError as error:
            warnings.append(f"Stopped this window before {row['title']}: {error}")
            break
        try:
            launch_tab(window_name, row)
            if wait_for_session(row["session_id"]):
                restored.append(row["title"])
            else:
                warnings.append(f"Opened a tab but could not verify its session: {row['title']}")
            time.sleep(1.5)
        except Exception as error:
            warnings.append(f"Could not restore {row['title']}: {error}")


# WHAT IT DOES: Runs one restore at a time across every open Session Keeper screen.
# DEPENDS ON: Windows operation lock and the guarded restore implementation.
# AFFECTS: Concurrent clicks cannot duplicate a conversation.
def restore_selected(snapshot, selected_keys, dry_run=False, choices=None, approved_close=()):
    with exclusive_operation("restore"):
        return perform_restore(snapshot, selected_keys, dry_run, choices, approved_close)
