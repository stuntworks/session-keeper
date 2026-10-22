# Last modified: 2026-10-21 | Change: Dark recovery screen opening on Saved recovery; time scrubber loads off the Tk thread | Full history: git log
"""Session Keeper: record terminal groups and offer a safe recovery menu."""

import argparse
import threading
from keeper_coexist import wait_for_native_restore
import sys
from datetime import datetime
import tkinter as tk
from pathlib import Path

from keeper_core import (DATA, capture_snapshot,
                         read_boot_id, read_windows, recovery_rows, save_snapshot)
from keeper_restore import assess_restore, live_session_ids, restore_selected
from keeper_chooser import chooser_entries
from keeper_scrubber import scrubber_stops
from keeper_chrome import SAVED
from keeper_recovery_view import RecoveryScreen
from keeper_theme import GO, IC_CHECK, ROOM, STOP, Theme
from keeper_ask import OccupiedDialog
from keeper_copycmd import build_resume_text
from keeper_display import big_geometry, make_dpi_aware, pointer_monitor, read_monitors
from keeper_settings import auto_restore_enabled
from keeper_history import read_history, startup_recovery
from keeper_background import run_background
from keeper_locks import exclusive_operation
from configure_terminal import terminal_restore_enabled
from keeper_live_view import LiveChatsPanel
from keeper_loading import LoadingWindow, load_then_show
from keeper_single import claim_recover_screen, publish_window
from keeper_start import load_recovery_start

INTERVAL_MS = 120_000


# WHAT IT DOES: Loads the newest usable snapshot, preferring the previous boot.
# DEPENDS ON: Immutable snapshots under the app's data directory.
# AFFECTS: The recovery screen displays the chosen saved layout.
def latest_recovery_snapshot():
    history = read_history()
    return history[0] if history else None


# WHAT IT DOES: Recovery screen: the monitor map is the menu; click tabs to choose what comes back.
# DEPENDS ON: A saved snapshot, live session IDs, and the restore planner.
# AFFECTS: Selected tabs are passed to the restore planner; nothing launches without a click or the opt-in countdown.
class RecoveryWindow(RecoveryScreen):
    # WHAT IT DOES: Builds the screen once (it always opens on Saved recovery); a stop only refreshes the map.
    # DEPENDS ON: Tk root, snapshot data, live process inventory.
    # AFFECTS: The restore button acts only on selected tabs.
    def __init__(self, root, path, snapshot, watch_mode=False, history=None, live=None):
        self.root = root
        self.options = {}
        self.watch_mode = watch_mode
        self.countdown_job = None
        self.native_wait = None
        self.busy = False
        self.pick_token = None
        root._keeper_window = self
        self.root.title("Session Keeper - recover terminals")
        geometry = window_geometry()
        self.root.geometry(geometry)
        self.theme = Theme(root, int(geometry.split("x")[0]))
        self.theme.style_ttk()
        self.root.configure(background=ROOM)
        self.history = history if history is not None else read_history()
        self.entries = chooser_entries(self.history)
        self.stops = scrubber_stops(self.history, self.entries) if self.history else []
        self.auto_restore_default = auto_restore_enabled
        self.build_screen()
        self.live_panel = LiveChatsPanel(self.now_tab, theme=self.theme)
        self.load(path, snapshot, live)
        self.show_view(SAVED)
        root.protocol("WM_DELETE_WINDOW", self.skip)
        if watch_mode and self.auto_choice.get() and not snapshot.get("legacy"):
            if terminal_restore_enabled():
                self.wait_for_terminal()
            else:
                self.countdown(10)

    # WHAT IT DOES: Shows one saved layout on the map, keeping the user on the Saved recovery tab.
    # DEPENDS ON: recovery_rows and a fresh live ID set (passed in when it was read off the Tk thread).
    # AFFECTS: Rebuilds the selection; in watch mode every missing tab starts selected for the countdown.
    def load(self, path, snapshot, live=None):
        self.path, self.snapshot = path, snapshot
        self.rows = {row["key"]: row for row in recovery_rows(snapshot)}
        self.live = live if live is not None else live_session_ids()
        self.options = {key: tk.BooleanVar(value=self.watch_mode and row["session_id"] not in self.live)
                        for key, row in self.rows.items()}
        unmatched = snapshot.get("unmatched_sessions", [])
        workers = sum(1 for session in unmatched if session.get("role") == "worker")
        unknown = len(unmatched) - workers
        notice = [snapshot.get("note") or "", f"{workers} background worker(s) are not restored." if workers else "",
                  f"{unknown} chat(s) with unknown placement are not restored." if unknown else ""]
        self.header.set_notice("  ".join(part for part in notice if part))
        self.map.show(snapshot, self.rows, self.live, self.options)
        current = next((number for number, stop in enumerate(self.stops)
                        if self.history[stop["index"]][0] == path), None)
        if current is not None and current != self.strip.current:
            self.strip.set_current(current)
        self.update_summary()
        self.selection_changed()

    # WHAT IT DOES: Puts the exact resume commands for the selected tabs on the clipboard (manual fallback).
    # DEPENDS ON: Selected keys that are all MATCHED recovery rows; keeper_copycmd.build_resume_text.
    # AFFECTS: Clipboard and the status line only. Launches, moves and closes nothing.
    def copy_command(self):
        keys = sorted(self.selected(), key=lambda key: tuple(int(part) for part in key.split(":")))
        if not keys:
            return
        assert all(key in self.rows for key in keys), "selected tab is not an exactly matched session"
        rows = [self.rows[key] for key in keys]
        try:
            text = build_resume_text(rows)
        except (ValueError, RuntimeError) as error:
            self.copy_status.configure(text=f"Not copied: {error}", foreground=STOP)
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()
        name = rows[0]["title"] if len(rows) == 1 else f"{len(rows)} tabs"
        self.copy_status.configure(text=f"Copied command for {name}", foreground=GO)
        self.copy_button.configure(text="Copied", icon=IC_CHECK)

    # WHAT IT DOES: Gives native restore time to finish while keeping STOP responsive.
    # DEPENDS ON: Background read-only observation and a per-window cancellation event.
    # AFFECTS: Only this recovery screen can start its later countdown.
    def wait_for_terminal(self):
        token = threading.Event()
        self.native_wait = token
        self.countdown_text.configure(text="Waiting for Windows Terminal to restore first. Stop cancels recovery.")
        run_background(self.root, lambda: wait_for_native_restore(token),
                       lambda live: self.terminal_ready(token, live),
                       lambda error: self.terminal_wait_failed(token, error))

    # WHAT IT DOES: Removes already-running chats from the selection, then offers the STOP countdown.
    # DEPENDS ON: A completed scan belonging to this still-active recovery screen.
    # AFFECTS: Late callbacks after STOP or layout changes cannot launch conversations.
    def terminal_ready(self, token, live):
        if token.is_set() or self.native_wait is not token:
            return
        self.native_wait = None
        self.live |= set(live)
        for row in recovery_rows(self.snapshot):
            if row['session_id'] in live and row['key'] in self.options:
                self.options[row['key']].set(False)
        self.map.draw()
        self.selection_changed()
        if self.selected():
            self.countdown(10)
        else:
            self.countdown_text.configure(text="Your saved conversations are already open; nothing to restore.")

    # WHAT IT DOES: Reports uncertain startup without opening any terminals.
    # DEPENDS ON: A failed scan and its still-current cancellation token.
    # AFFECTS: Stopped or replaced screens ignore obsolete errors.
    def terminal_wait_failed(self, token, error):
        if token.is_set() or self.native_wait is not token:
            return
        self.native_wait = None
        self.countdown_text.configure(text=f"Automatic recovery paused: {error}")

    # WHAT IT DOES: Chooser entry callback: maps an entry number to its saved snapshot.
    # DEPENDS ON: chooser_entries.
    # AFFECTS: See choose_layout.
    def choose_entry(self, number):
        if 0 <= number < len(self.entries):
            self.choose_layout(self.entries[number][1])

    # WHAT IT DOES: Time scrubber callback: each stop is one chooser entry.
    def pick_stop(self, number):
        if 0 <= number < len(self.stops):
            self.choose_layout(self.stops[number]["index"])

    # WHAT IT DOES: Switches to a user-selected saved layout without launching sessions or changing views.
    # DEPENDS ON: Saved history; the live scan runs off the Tk thread (keeper_background).
    # AFFECTS: Older crash layouts remain reachable; the user stays on Saved recovery. A late scan for an
    # earlier pick is ignored.
    def choose_layout(self, index):
        if self.busy or index < 0 or index >= len(self.history):
            return
        self.cancel_countdown()
        self.show_view(SAVED)
        token = object()
        self.pick_token = token
        self.countdown_text.configure(text="Checking which saved chats are open now...")
        run_background(self.root, live_session_ids, lambda live: self.layout_loaded(token, index, live),
                       lambda error: self.layout_failed(token, error))

    # WHAT IT DOES: Shows the picked layout once its live scan is back (only the newest pick counts).
    def layout_loaded(self, token, index, live):
        if token is not self.pick_token:
            return
        self.pick_token = None
        self.countdown_text.configure(text="")
        self.load(*self.history[index], live)
        self.show_view(SAVED)

    # WHAT IT DOES: Keeps the old layout when the live scan fails, and says so.
    def layout_failed(self, token, error):
        if token is not self.pick_token:
            return
        self.pick_token = None
        self.countdown_text.configure(text=f"Could not check which chats are open, so the layout did not change: {error}")
        current = next((number for number, stop in enumerate(self.stops)
                        if self.history[stop["index"]][0] == self.path), None)
        if current is not None:
            self.strip.set_current(current)

    # WHAT IT DOES: Stops an outstanding automatic restart timer.
    # DEPENDS ON: Tk timer identifier.
    # AFFECTS: A manual preview or restart cannot be followed by a second automatic restart.
    def cancel_countdown(self):
        if self.native_wait is not None:
            self.native_wait.set()
            self.native_wait = None
        if self.countdown_job is not None:
            self.root.after_cancel(self.countdown_job)
            self.countdown_job = None

    # WHAT IT DOES: Cancels any auto-restore countdown and closes or hides the menu.
    # DEPENDS ON: Watch mode and Tk timer state.
    # AFFECTS: Background snapshots continue when the startup menu is skipped.
    def skip(self):
        self.cancel_countdown()
        if self.busy:
            self.countdown_text.configure(text="Finishing the current check. This window will stay open until it finishes.")
            return
        if self.watch_mode:
            self.root.withdraw()
        else:
            self.root.destroy()

    # WHAT IT DOES: Gives the user ten seconds to stop automatic restore.
    # DEPENDS ON: Explicit opt-in setting and Tk event loop.
    # AFFECTS: Starts selected absent sessions after the countdown; never closes or stacks anything.
    def countdown(self, seconds):
        if seconds <= 0:
            self.countdown_job = None
            self.begin_operation(dry_run=False, ask=False)
            return
        self.countdown_text.configure(text=f"Automatic restore in {seconds} seconds. Press Stop to cancel.")
        self.countdown_job = self.root.after(1000, lambda: self.countdown(seconds - 1))

    # WHAT IT DOES: Selects every missing, identified tab in every window.
    # DEPENDS ON: A fresh live check.
    # AFFECTS: Changes which conversations the user asks to restore.
    def check_all(self):
        self.live = live_session_ids()
        self.map.live = self.live
        for key, option in self.options.items():
            option.set(self.rows[key]["session_id"] not in self.live)
        self.map.draw()
        self.selection_changed()

    # WHAT IT DOES: Returns the current set of selected tab keys.
    # DEPENDS ON: Tk BooleanVar state.
    # AFFECTS: Preview and restore use these exact keys.
    def selected(self):
        return {key for key, option in self.options.items() if option.get()}

    # WHAT IT DOES: Shows what the planner would reopen, add, close or skip, and why.
    # DEPENDS ON: Current process and window inventory.
    # AFFECTS: Does not launch, move or close any window.
    def preview(self):
        self.begin_operation(dry_run=True)

    # WHAT IT DOES: Rechecks safety, asks about covered spots, then launches only the selected absent sessions.
    # DEPENDS ON: Restore planner and the user pressing Restart selected.
    # AFFECTS: Creates and positions terminal windows; closes only windows the user approved.
    def restart(self):
        self.begin_operation(dry_run=False, ask=True)

    # WHAT IT DOES: Drawer "Restart this tab": the same guarded restore, for that one missing tab only.
    # DEPENDS ON: The tab being an exactly matched, missing (not open) tab.
    # AFFECTS: Same as restart; other selected tabs are left alone.
    def restart_one(self, key):
        if key in self.rows and self.map.tab_state(key) in ("missing", "selected"):
            self.begin_operation(dry_run=False, ask=True, keys={key})

    # WHAT IT DOES: Starts a single preview or restore while keeping the UI responsive.
    # DEPENDS ON: Selected keys, background worker, and restore safety checks.
    # AFFECTS: Repeated clicks cannot queue multiple recovery operations. ask=False (countdown) never asks or closes.
    def begin_operation(self, dry_run, ask=False, keys=None):
        self.cancel_countdown()
        if self.busy:
            return
        selected = set(keys) if keys else self.selected()
        if not selected:
            self.countdown_text.configure(text="Nothing is selected. Click a missing tab (amber dot) first.")
            return
        self.busy = True
        self.countdown_text.configure(text="Checking saved sessions and screen positions…")
        if dry_run or not ask:
            self.run_restore(selected, dry_run)
            return
        run_background(self.root, lambda: assess_restore(self.snapshot, selected),
                       lambda blocked: self.ask_about(selected, blocked), self.operation_failed)

    # WHAT IT DOES: Shows the confirm dialog when saved spots are covered, otherwise restores straight away.
    # DEPENDS ON: Classified blocked spots from assess_restore.
    # AFFECTS: Cancel restores nothing.
    def ask_about(self, selected, blocked):
        if not blocked:
            self.run_restore(selected, False)
            return
        self.countdown_text.configure(text="Waiting for your answer about covered spots…")
        OccupiedDialog(self.root, blocked, lambda result: self.answered(selected, result))

    # WHAT IT DOES: Continues or cancels after the confirm dialog.
    # DEPENDS ON: (choices, approved HWNDs) or None.
    # AFFECTS: Starts the guarded restore with exactly the user's choices.
    def answered(self, selected, result):
        if result is None:
            self.busy = False
            self.countdown_text.configure(text="Cancelled. Nothing was opened or closed.")
            return
        choices, approved = result
        self.run_restore(selected, False, choices, approved)

    # WHAT IT DOES: Runs the guarded restore or preview in the background.
    # DEPENDS ON: restore_selected.
    # AFFECTS: See restore_selected.
    def run_restore(self, selected, dry_run, choices=None, approved=()):
        run_background(self.root, lambda: restore_selected(self.snapshot, selected, dry_run, choices, approved),
                       lambda result: self.finish_operation(result, dry_run), self.operation_failed)

    # WHAT IT DOES: Reports verified resumes or preview results after background work finishes.
    # DEPENDS ON: Restore result with successful titles and skip reasons.
    # AFFECTS: Unlocks the screen and refreshes which tabs are live.
    def finish_operation(self, result, dry_run):
        self.busy = False
        self.live_panel.refresh()
        restored, warnings = result
        self.countdown_text.configure(text="Preview ready." if dry_run else "Restart finished.")
        if dry_run:
            lines = [item if item.startswith("Would ") else f"Would open: {item}" for item in restored]
            self.map.drawer.show_message("Preview", lines or ["Would open: nothing"], warnings)
        else:
            self.map.drawer.show_message("Restarted", ["Verified running:"] + (restored or ["Nothing"]), warnings)
            self.refresh_live()
        self.selection_changed()

    # WHAT IT DOES: Re-reads live sessions so restored tabs turn grey.
    # DEPENDS ON: live_session_ids.
    # AFFECTS: Map display and selection.
    def refresh_live(self):
        try:
            self.live = live_session_ids()
        except RuntimeError:
            return
        self.map.live = self.live
        for key, option in self.options.items():
            if self.rows[key]["session_id"] in self.live:
                option.set(False)
        self.map.draw()
        self.selection_changed()

    # WHAT IT DOES: Displays a failed background check without closing Session Keeper.
    # DEPENDS ON: An exception from the background worker.
    # AFFECTS: The user can correct the problem and retry.
    def operation_failed(self, error):
        self.busy = False
        self.countdown_text.configure(text="Recovery paused.")
        self.map.drawer.show_message("Recovery paused", [], [str(error)])
        self.selection_changed()


# WHAT IT DOES: Size and place the recovery window close to full screen on the monitor under the pointer.
# DEPENDS ON: keeper_display monitor reading; falls back to a fixed size.
# AFFECTS: Window geometry only.
def window_geometry():
    try:
        return big_geometry(pointer_monitor(read_monitors())) or "1400x860"
    except (RuntimeError, OSError):
        return "1400x860"


# WHAT IT DOES: Records a new immutable snapshot whenever a matched session exists.
# DEPENDS ON: Live terminal UI and Claude session inventory.
# AFFECTS: Adds history files without replacing earlier snapshots.
def snapshot_once():
    with exclusive_operation("restore"):
        snapshot = capture_snapshot()
        if snapshot["counts"]["matched"]:
            return save_snapshot(snapshot)
    return None


# WHAT IT DOES: Keeps taking snapshots while Windows is running.
# DEPENDS ON: Tk event loop and snapshot_once.
# AFFECTS: Future crashes can use the latest captured layout.
def watch(root):
    if root.state() != "withdrawn":
        root.after(INTERVAL_MS, lambda: watch(root))
        return
    run_background(root, snapshot_once, lambda path: snapshot_finished(root, path),
                   lambda error: snapshot_failed(root, error))


# WHAT IT DOES: Schedules the next capture once a snapshot finishes, without interrupting the user.
# DEPENDS ON: Tk timer.
# AFFECTS: Recording continues silently.
# DANGER ZONE FRAGILE: Never raise the recovery screen from here / breaks: the user's work / depends on:
# a closed tab and a crashed tab being indistinguishable mid-session. Recovery is offered after a
# Start menu -> Session Keeper only, never because a conversation disappeared.
def snapshot_finished(root, path):
    root.after(INTERVAL_MS, lambda: watch(root))


# WHAT IT DOES: Records a watcher error and schedules the next read without discarding history.
# DEPENDS ON: Append-only diagnostic log in the app's data folder.
# AFFECTS: Background errors remain visible after a hidden Python launch.
def snapshot_failed(root, error):
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / "watcher_errors.log").open("a", encoding="utf-8") as file:
        file.write(f"{datetime.now().isoformat()} {error}\n")
    root.after(INTERVAL_MS, lambda: watch(root))


# WHAT IT DOES: Starts a one-time snapshot, background watcher, or recovery menu.
# DEPENDS ON: Command-line mode and a saved snapshot for recovery.
# AFFECTS: Watch records state; recover opens the selection UI.
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("snapshot", "watch", "recover"))
    arguments = parser.parse_args()
    if arguments.mode == "snapshot":
        print(snapshot_once() or "No matched live sessions; nothing recorded")
        return
    if arguments.mode == "recover" and not claim_recover_screen():
        return  # a recovery screen is already open; it was brought to the front
    # DPI awareness must be set before Tk starts, so Tk pixels equal screen pixels.
    make_dpi_aware()
    root = tk.Tk()
    root.withdraw()
    if arguments.mode == "watch":
        with exclusive_operation("watch"):
            # DANGER ZONE HIGH: Only opted-in previous-boot recovery may show on login.
            # Same-boot restarts and normal tab closures never reopen old work.
            if auto_restore_enabled():
                recovery = startup_recovery(read_boot_id())
                if recovery:
                    root.deiconify()
                    RecoveryWindow(root, *recovery, watch_mode=True)
            root.after(1000, lambda: watch(root))
            root.mainloop()
        return
    # Show the loading window first; read snapshots and live sessions off the Tk thread.
    loading = LoadingWindow(root)
    publish_window(loading.top)
    load_then_show(root, loading, load_recovery_start, lambda loaded: open_recovery(root, *loaded))
    root.mainloop()


# WHAT IT DOES: Opens the recovery screen from the loaded history, or says nothing is saved yet.
# DEPENDS ON: Tk main thread; the loading window is closed by the caller after this returns.
# AFFECTS: Shows RecoveryWindow and records its window for a second launch to raise; no history exits.
def open_recovery(root, history, live=None):
    if history:
        root.deiconify()
        RecoveryWindow(root, *history[0], history=history, live=live)
        publish_window(root)
    else:
        print("No Session Keeper snapshot exists yet.")
        root.after(0, root.destroy)


if __name__ == "__main__":
    main()
