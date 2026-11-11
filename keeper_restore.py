# Last modified: 2026-11-10 | Change: Slow/late windows waited for and adopted; one hiccup costs one tab or group; maximised windows; a finished real restore is noted | Full history: git log
"""Launch selected absent CLI sessions and position only their verified new windows."""

import os
import subprocess
import time
import uuid
from pathlib import Path
from keeper_locks import exclusive_operation, wait_out_capture
from keeper_display import monitors_settled, read_monitors
from keeper_coords import physical_snapshot

from keeper_core import (ROOT, plan_restore, read_boot_id,
                         read_windows, strip_leading_glyphs)
from keeper_created import group_key, load_created_windows, record_created_window
from keeper_crash_record import note_restore_done
import keeper_newwindow
from keeper_newwindow import (WindowNotSeen, adopt_late_windows, find_new_window, not_maximised,
                             record_pending, still_starting)

PLACE_SCRIPT = ROOT / "place_window.ps1"
STRICT_WAIT_SECONDS = 30
STRICT_POLL_SECONDS = 2.5
MONITOR_RETRIES = 2            # a monitor read that differs is re-read this many times before a group is skipped
MONITOR_RETRY_SECONDS = 2


# WHAT IT DOES: Removes inherited child markers and no-color flags for interactive recovery.
# DEPENDS ON: The current process environment.
# AFFECTS: Inherited Claude child markers can silently turn transcript saving off.
def clean_environment():
    return {key: value for key, value in os.environ.items()
            if not key.upper().startswith("CLAUDE")
            and key.upper() not in {"CODEX_SESSION_ID", "CODEX_THREAD_ID", "CODEX_CI", "NO_COLOR"}
            and not (key.upper() == "TERM" and value.lower() == "dumb")
            and not (key.upper() == "COLORTERM" and not value)}


# WHAT IT DOES: Raised when an open CLI cannot be tied to a session; carries the processes for the fix-it text.
# DEPENDS ON: live_inventory rows (title, folder, kind).
# AFFECTS: The recovery screen shows these and offers "Restore anyway" for a clicked restore only.
class UnidentifiedSessions(RuntimeError):
    def __init__(self, sessions):
        self.sessions = list(sessions)
        names = "; ".join(describe_unidentified(session) for session in self.sessions) or "an open CLI"
        super().__init__(f"Recovery paused so a chat is not opened twice. Session Keeper cannot tell which chat is "
                         f"running in: {names}. To fix it: type a message in that tab (or close it), then press "
                         f"Restart selected again. Or press Restore anyway if you are sure it is not one of the "
                         f"chats you selected.")


# WHAT IT DOES: One plain-words name for an unidentified process: tab title, folder and program.
def describe_unidentified(session):
    program = "Codex" if session.get("kind") == "codex" else "Claude"
    title = session.get("title") or "untitled tab"
    folder = session.get("processCwd") or session.get("cwd") or "unknown folder"
    return f"{program} tab '{title}' in {folder}"


# WHAT IT DOES: The strict= value for a restore the user started by clicking (never the login auto-restore).
# DEPENDS ON: Nothing about the process command line. A /resume inside a running Claude is not visible there.
# AFFECTS: override=True is only ever set by the explicit "Restore anyway" button. Every unidentified CLI still blocks.
class LiveCheck:
    def __init__(self, rows=(), override=False):
        self.rows = list(rows)
        self.override = override

    def __bool__(self):
        return True


# WHAT IT DOES: Reads console titles for just the blocking processes, so the pause can name their tabs.
# DEPENDS ON: keeper_codex.console_title (one short probe per process).
# AFFECTS: Text only; a title that cannot be read stays empty.
def with_titles(sessions):
    from keeper_codex import console_title
    for session in sessions:
        if not session.get("title") and session.get("pid"):
            try:
                session["title"] = console_title(session["pid"])
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
    return sessions


# WHAT IT DOES: Returns the unidentified interactive processes this check must stop on.
# DEPENDS ON: strict is False for a display refresh, True for login, or a LiveCheck for a clicked restore.
# AFFECTS: Every unidentified Claude or Codex stops the restore. Only Restore anyway (override) lets them through.
# DANGER ZONE HIGH: No launch-command exception / breaks: the same chat opens twice / depends on: uncertain rows from the inventory.
def blocking_sessions(sessions, strict):
    if not strict:
        return []
    found = [session for session in sessions
             if session.get("uncertain") and session.get("role") not in ("worker", "service")]
    if isinstance(strict, LiveCheck) and strict.override:
        return []
    return found


# WHAT IT DOES: Lists verified live Claude and Codex IDs, optionally rejecting unresolved CLI processes.
# DEPENDS ON: Current process inventory and session records.
# AFFECTS: Duplicate prevention fails if this inventory is stale. Login (strict=True) and a clicked restore
# (a LiveCheck) both stop on every unidentified CLI. Only "Restore anyway" passes them.
# DANGER ZONE HIGH: Strict wait / breaks: duplicate sessions if it stops failing closed / depends on: a just-launched CLI registering within STRICT_WAIT_SECONDS.
def live_session_ids(strict=False):
    from keeper_inventory import live_inventory
    deadline = time.monotonic() + STRICT_WAIT_SECONDS
    while True:
        sessions = live_inventory(read_titles=False)
        blockers = blocking_sessions(sessions, strict)
        if not blockers:
            break
        if time.monotonic() >= deadline:
            raise UnidentifiedSessions(with_titles(blockers))
        time.sleep(STRICT_POLL_SECONDS)
    return {session["sessionId"] for session in sessions if session.get("sessionId")}


# WHAT IT DOES: Moves one verified new window to the recorded rectangle.
# DEPENDS ON: place_window.ps1 and a handle never present before launch.
# AFFECTS: A bad handle could move an existing terminal, so identity is checked twice.
# maximize=True (only for a window saved maximised): placed at its normal spot first, then maximised there.
# Returns True; False when the window was placed correctly but did not maximise (place_window.ps1 exit 3).
def place_new_window(handle, rectangle, previous_handles, maximize=False):
    if handle in previous_handles:
        raise RuntimeError("Refusing to move a pre-existing terminal")
    arguments = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                 str(PLACE_SCRIPT), "-Handle", str(handle), "-X", str(rectangle["x"]),
                 "-Y", str(rectangle["y"]), "-Width", str(rectangle["w"]),
                 "-Height", str(rectangle["h"])] + (["-Maximize"] if maximize else [])
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=20,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode == 3 and maximize:
        return False                    # placed and checked; only the maximise did not take
    if result.returncode:
        raise RuntimeError(f"Could not position new terminal: {result.stderr.strip()}")
    return True


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
    cwd = row.get("cwd")
    if not isinstance(cwd, str) or not cwd.strip():
        raise ValueError("its saved project folder is unknown, so Session Keeper will not guess one. To fix: "
                         "select the tab and use Copy command in the right folder")
    if not Path(row["cwd"]).is_dir():
        raise ValueError(f"its project folder {row['cwd']} is missing. To fix: put that folder back (or reconnect "
                         "its drive), then press Restart selected again")
    if any(character in row["cwd"] for character in (';', '\n', '\r', '"')):
        raise ValueError(f"its project folder {row['cwd']} has a character Windows Terminal cannot take. To fix: "
                         "select the tab and use Copy command instead")
    title = row["title"][:70].replace(';', ',').replace('\n', ' ').replace('\r', ' ') or "Recovered session"
    program = resume_program(row)
    arguments = ["wt.exe", "-w", window_name, "new-tab", "-d", row["cwd"],
                 "--title", title, "cmd.exe", "/k", *program]
    # DANGER ZONE HIGH: Clean inherited Claude markers / breaks: transcript saving / depends on: child environment.
    subprocess.Popen(arguments, env=clean_environment(), close_fds=True,
                     creationflags=subprocess.CREATE_NO_WINDOW)


# WHAT IT DOES: Opens one more copy of a chat that is already running, in a new window of its own.
# DEPENDS ON: launch_tab; called ONLY from the details drawer's explicit "Open a second copy" click.
# AFFECTS: Deliberately skips the duplicate check. Restore and auto-restore never call this.
def open_second_copy(row):
    launch_tab("SessionKeeper-" + uuid.uuid4().hex, row)
    return row["title"]


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
# DEPENDS ON: The same planner as restore, plus read-only process and window scans. lock_name selects the Windows lock.
# AFFECTS: Nothing is opened or closed; the result feeds the confirm dialog. The default lock is the real per-user restore lock.
def assess_restore(snapshot, selected_keys, override=False, *, lock_name="restore"):
    with wait_out_capture(lock_name, lock=exclusive_operation):   # a capture finishes; another restore fails
        return find_blocked(snapshot, selected_keys, check=live_check(snapshot, selected_keys, True, override))


# WHAT IT DOES: Runs the planner once to collect blocked spots, then classifies their occupants.
# DEPENDS ON: Window probe, live inventory, monitors and created-window receipts.
# AFFECTS: Read-only.
def find_blocked(snapshot, selected_keys, live_ids=None, windows=None, check=True):
    from keeper_occupancy import classify_blocked, read_shells
    windows = read_windows() if windows is None else windows
    live_ids = live_session_ids(strict=check) if live_ids is None else live_ids
    blocked = []
    groups = LazyReader(lambda: read_live_groups(windows))
    monitors = read_monitors()
    snapshot = physical_snapshot(snapshot, monitors)[0]
    plan_restore(snapshot, selected_keys, live_ids, windows, monitors,
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
                         f"Restart selected will ask before closing it, or opens on top of it. Nothing to fix: "
                         f"{spot['title']}")
        else:
            lines.append(f"Window {number}'s spot is covered by a terminal that is in use or unknown ({tabs}). "
                         f"It will open on top of it; nothing is closed. Nothing to fix: {spot['title']}")
    return lines


# WHAT IT DOES: Restores selected groups only where the saved position is free, or where the user explicitly chose.
# DEPENDS ON: Fresh window and process checks before each new window and tab.
# AFFECTS: Starts Claude processes, moves only windows created by this run, closes only approved empty windows.
def perform_restore(snapshot, selected_keys, dry_run=False, choices=None, approved_close=(), manual=False,
                    override=False):
    check = live_check(snapshot, selected_keys, manual, override)
    monitors = read_monitors()
    snapshot, coord_warnings = physical_snapshot(snapshot, monitors)   # older snapshots: system-DPI spots
    boot_id = read_boot_id()
    owned = load_created_windows(boot_id)
    blocked = []
    late = [] if dry_run else adopt_late(owned, boot_id)    # a window that opened after its group gave up
    # DANGER ZONE HIGH: A chat whose window is still starting is not live yet / breaks: a second copy of that
    # chat / depends on: still_starting holding back its whole saved window for PENDING_MINUTES.
    selected_keys, starting = still_starting(snapshot, selected_keys, boot_id)
    late += starting
    try:
        windows = read_windows()
        live = live_session_ids(strict=check)
        groups = LazyReader(lambda: read_live_groups(windows))
        plans, warnings = plan_restore(snapshot, selected_keys, live, windows, monitors, owned,
                                       live_groups=groups.get, choices=choices, blocked=blocked)
    except UnidentifiedSessions:
        raise  # the screen shows the fix-it text and the explicit "Restore anyway" button
    except RuntimeError as error:
        return [], [f"Recovery paused: {error}. To fix: press Restart selected again in a moment."]
    warnings = coord_warnings + late + warnings
    # Back windows first, front window last: each new window opens on top, so a window that was hidden
    # behind another ends up behind it again. Only the order changes; the plans themselves do not.
    from keeper_zorder import back_to_front
    plans = back_to_front(plans, snapshot)
    if dry_run and blocked:
        from keeper_occupancy import classify_blocked, read_shells
        try:
            warnings.extend(describe_blocked(classify_blocked(blocked, groups.get(), read_shells())))
        except RuntimeError as error:
            warnings.append(f"Could not check the windows on the saved spots: {error}. To fix: press Preview again.")
    restored = []
    state = {"adds_stopped": False, "pending": False, "monitors_changed": False}
    for plan in plans:
        rows = plan["tabs"]
        if not monitors_settled(monitors, state, read_monitors, time.sleep, MONITOR_RETRIES, MONITOR_RETRY_SECONDS):
            warnings.append(f"Not opened: '{rows[0]['title']}'. The monitor layout changed during recovery. To fix: "
                            "wait until every display is on and settled, then press Restart selected again.")
            continue
        if plan.get("close_hwnds"):
            if dry_run:
                restored.extend(f"Would close the empty terminal and open in its place: {row['title']}" for row in rows)
                continue
            if not free_spot(plan, approved_close, warnings, check):
                continue
        try:
            windows = read_windows()
            groups = LazyReader(lambda: read_live_groups(windows))
            fresh_plans, fresh_warnings = plan_restore(snapshot, {row["key"] for row in rows},
                live_session_ids(strict=check), windows, monitors, owned,
                live_groups=groups.get, choices=choices)
        except Exception as error:      # a probe hiccup (or an unidentified CLI) costs this group only
            warnings.append(stop_line(rows[0]["title"], error))
            continue
        warnings.extend(fresh_warnings)
        if not fresh_plans:
            continue
        fresh = fresh_plans[0]
        if fresh.get("mode") == "add":
            add_to_open_window(fresh, dry_run, restored, warnings, state, check)
            continue
        if fresh.get("close_hwnds"):
            warnings.append(f"Not opened: '{rows[0]['title']}'. The empty terminal on its saved spot did not close. "
                            "To fix: close that terminal yourself (Windows Terminal may be asking 'close all tabs?'), "
                            "then press Restart selected again.")
            continue
        if dry_run:
            prefix = f"Would open on top of {fresh['covering']}: " if fresh.get("on_top") else ""
            restored.extend(prefix + row["title"] for row in fresh["tabs"])
            continue
        before = len(restored)
        state["pending"] |= open_group(snapshot, fresh, owned, boot_id, restored, warnings, check)
        if fresh.get("on_top") and len(restored) > before:
            warnings.append(f"Opened '{fresh['tabs'][0]['title']}' on top of {fresh['covering']} at its saved spot; "
                            "drag it where you want. Nothing was closed.")
    if state["pending"]:
        warnings.extend(adopt_late(owned, boot_id))   # a slow window may have turned up while later groups ran
    return restored, warnings


# WHAT IT DOES: Adopts provably-ours late windows (keeper_newwindow); returns its plain lines. Never raises.
# DEPENDS ON: read_windows, read_live_groups, place_new_window (looked up here so tests can fake them).
# AFFECTS: owned gains each adopted window; that window is moved to its saved spot.
def adopt_late(owned, boot_id):
    lines = []
    try:
        adopt_late_windows(owned, boot_id, read_windows, read_live_groups, place_new_window, lines)
    except Exception as error:
        lines.append(f"Could not check for a terminal window that opened late: {error}. Nothing to fix.")
    return lines



# WHAT IT DOES: Chooses the live check. Login auto-restore and a clicked restore both stop on every
# unidentified CLI. Only the explicit "Restore anyway" click passes them.
# DEPENDS ON: recovery_rows for the selected keys.
# AFFECTS: Which unidentified processes stop recovery.
def live_check(snapshot, selected_keys, manual, override):
    if not manual:
        return True
    from keeper_core import recovery_rows
    return LiveCheck([row for row in recovery_rows(snapshot) if row["key"] in selected_keys], override)


# WHAT IT DOES: One fix-it line for a group that was stopped before it opened.
# DEPENDS ON: The error; UnidentifiedSessions already carries its own fix text.
# AFFECTS: Text only.
def stop_line(title, error):
    if isinstance(error, UnidentifiedSessions):
        return f"Not opened: '{title}'. {error}"
    text = str(error).rstrip(".")
    fix = "" if "To fix" in text else (". To fix: press Restart selected again in a moment; if it keeps happening, "
                                       "use the command shown below")
    return f"Not opened: '{title}': {text}{fix}."


# WHAT IT DOES: Fix-it line for a tab that was launched but whose session never showed up.
def unverified_line(title):
    return (f"Opened '{title}' but could not confirm its chat started. To fix: look at that new tab; if it shows "
            "an error, close it and press Restart selected again. Nothing to fix if the chat is there.")


# WHAT IT DOES: Closes the approved empty windows on one spot before its group is opened there.
# DEPENDS ON: keeper_occupancy.close_empty_spot with fresh probes.
# AFFECTS: Returns False (spot skipped) unless every covering window closed and no live chat was lost.
def free_spot(plan, approved_close, warnings, check=True):
    from keeper_occupancy import close_empty_spot, read_shells
    title = plan["tabs"][0]["title"]
    try:
        freed, reason = close_empty_spot(plan["close_hwnds"], set(approved_close), read_windows,
                                         read_live_groups, read_shells, lambda: live_session_ids(strict=check))
    except RuntimeError as error:
        warnings.append(stop_line(title, error))
        return False
    if not freed:
        warnings.append(f"Not opened: '{title}': {reason}. To fix: close that terminal yourself, or press "
                        "Restart selected again and choose 'Restart on top of them'.")
    return freed


# WHAT IT DOES: Adds each missing tab into its group's open window, verifying every landing.
# DEPENDS ON: keeper_target.add_tab and strict live checks before each launch.
# AFFECTS: A missed or unproven landing (any error after the tab was launched) stops all further adds to open
# windows for this run. A failed identity check stops this window. An error before launch costs only that tab.
def add_to_open_window(plan, dry_run, restored, warnings, state, check=True):
    import keeper_target
    for row in plan["tabs"]:
        if dry_run:
            restored.append(f"Would add to its open window: {row['title']}")
            continue
        if state["adds_stopped"]:
            warnings.append(f"Not added: '{row['title']}'. Adding stopped after a tab landed in the wrong window. "
                            "To fix: check that window, then press Restart selected again.")
            continue
        try:
            # DANGER ZONE HIGH: Identity failure stops this window / breaks: a chat opened twice / depends on: break.
            if row["session_id"] in live_session_ids(strict=check):
                warnings.append(f"Not opened twice: '{row['title']}' is already open. Nothing to fix.")
                continue
        except Exception as error:
            warnings.append(stop_line(row["title"], error))
            break
        launched = []

        # WHAT IT DOES: launch_tab, noting that this tab really was launched. AFFECTS: launched only.
        def launch(name, launch_row):
            launched.append(launch_row["session_id"])
            launch_tab(name, launch_row)
        try:
            verified = keeper_target.add_tab(plan["target_hwnd"], row, plan.get("window_name"),
                                             launch, wait_for_session, read_windows)
        except keeper_target.TargetMissed as error:
            state["adds_stopped"] = True
            warnings.append(str(error))
            continue
        except Exception as error:
            # DANGER ZONE HIGH: Launched but landing unproven stops all adds / breaks: next `wt -w 0` lands in
            # the wrong window / depends on: launched being set by the launch wrapper.
            state["adds_stopped"] |= bool(launched)
            warnings.append(stop_line(row["title"], error))
            continue
        if verified:
            restored.append(row["title"])
        else:
            warnings.append(unverified_line(row["title"]))


# WHAT IT DOES: Opens one group in a new (or Session Keeper-owned) window at its saved spot.
# DEPENDS ON: Launch, new-window detection, placement, and strict live checks.
# AFFECTS: Starts processes and moves only the window this run created. Returns True when a launched window
# did not show up in time and was recorded as pending (keeper_newwindow adopts it if it turns up later).
def open_group(snapshot, plan, owned, boot_id, restored, warnings, check=True):
    rows = plan["tabs"]
    existing_window = plan.get("window_name")
    window_name = existing_window or "SessionKeeper-" + uuid.uuid4().hex
    first = rows[0]
    maximize = bool(first["window"].get("maximized"))
    if not existing_window:
        launched = False
        try:
            previous_handles = {window.get("hwnd") for window in read_windows()}
            baseline = keeper_newwindow.terminal_handles()      # same lister the wait polls with
            launch_tab(window_name, first)
            launched = True
            expected = first["title"][:70].replace(';', ',').replace('\n', ' ').replace('\r', ' ') or "Recovered session"
            new_window = find_new_window(previous_handles, expected, read_windows, baseline=baseline,
                                         session_id=first["session_id"], groups=read_live_groups)
            launched = False
            try:
                record_created_window(window_name, new_window["hwnd"], group_key(snapshot, first["window_index"]), boot_id,
                                      rect=plan["rect"], converted_from=first["window"].get("converted_from"))
                owned.append({"window_name": window_name, "hwnd": new_window["hwnd"],
                              "group": group_key(snapshot, first["window_index"]), "boot_id": boot_id})
            except OSError as error:
                warnings.append(f"Could not record the new window, so it cannot be refilled later: {error}")
            if place_new_window(new_window["hwnd"], plan["rect"], previous_handles, maximize=maximize) is False:
                warnings.append(not_maximised(first["title"]))
            if wait_for_session(first["session_id"]):
                restored.append(first["title"])
            else:
                warnings.append(unverified_line(first["title"]))
        except Exception as error:
            text = f"Not opened yet: '{first['title']}': {error}." if isinstance(error, WindowNotSeen) else None
            warnings.append(text or stop_line(first["title"], error))
            return launched and record_pending(window_name, snapshot, first["window"], first["window_index"],
                                               first["session_id"], first["title"], plan["rect"], previous_handles,
                                               boot_id, maximize)
    for row in (rows if existing_window else rows[1:]):
        try:
            if row["session_id"] in live_session_ids(strict=check):
                warnings.append(f"Not opened twice: '{row['title']}' is already open. Nothing to fix.")
                continue
        except RuntimeError as error:
            warnings.append(stop_line(row["title"], error))
            break
        try:
            launch_tab(window_name, row)
            if wait_for_session(row["session_id"]):
                restored.append(row["title"])
            else:
                warnings.append(unverified_line(row["title"]))
            time.sleep(1.5)
        except Exception as error:
            warnings.append(stop_line(row["title"], error))
    return False


# WHAT IT DOES: Runs one restore at a time across every open Session Keeper screen.
# DEPENDS ON: Windows operation lock and the guarded restore implementation. lock_name selects that lock.
# AFFECTS: Concurrent clicks cannot duplicate a conversation. The default lock is the real per-user restore lock.
# A real (not preview) restore that opened at least one chat is noted in data\restore_done.json; a paused
# or empty one is not, so the screen still preselects "Before the crash".
def restore_selected(snapshot, selected_keys, dry_run=False, choices=None, approved_close=(), manual=False,
                     override=False, *, lock_name="restore"):
    with wait_out_capture(lock_name, lock=exclusive_operation):   # a capture finishes; another restore fails
        result = perform_restore(snapshot, selected_keys, dry_run, choices, approved_close, manual, override)
    if not dry_run and result[0]:
        note_restore_done()         # the screen stops opening on "Before the crash" (keeper_crash_record)
    return result
