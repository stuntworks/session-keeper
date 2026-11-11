# Last modified: 2026-11-10 | Change: Summary shows the layout's date and age ("Saved Oct 29, 2:44 PM - 12 days ago") | Full history: git log
"""Layout and bar state for keeper_app.RecoveryWindow. Display only: selection rules live in keeper_picker,
restore and safety checks stay in keeper_app."""

import gc
import tkinter as tk
from datetime import datetime

from keeper_allsessions import AllSessionsView
from keeper_background import run_background
from keeper_chrome import ALL, NOW, SAVED, ActionBar, Header
from keeper_desktop_look import read_desktop_look
from keeper_picker import SnapshotMap
from keeper_recorder_health import saved_when
from keeper_scrubber import FilmStrip, reboot_marks, tick_positions
from keeper_settings import record_auto_restore, record_theme
from keeper_single import relaunch_recover_screen
from keeper_theme import GO_TEXT, IC_COPY, MODE, ROOM


# WHAT IT DOES: Mixin for RecoveryWindow: builds the screen and keeps the header and bar in step.
# DEPENDS ON: RecoveryWindow attributes (root, theme, history, stops, options, snapshot, busy) and its
# callbacks (copy_command, preview, restart, restart_one, skip, check_all, pick_stop, selection_changed).
# AFFECTS: Widgets only.
class RecoveryScreen:
    # WHAT IT DOES: Lays out the header, the two views (Saved recovery and On screen now) and the bottom bar.
    # DEPENDS ON: keeper_chrome, keeper_scrubber, keeper_picker.
    # AFFECTS: Widgets only.
    def build_screen(self):
        px = self.theme.px
        self.auto_choice = tk.BooleanVar(value=self.auto_restore_default())
        self.header = Header(self.root, self.theme, self.show_view, self.auto_choice,
                             lambda: record_auto_restore(self.auto_choice.get()), on_theme=self.switch_theme)
        self.header.pack(fill="x", padx=px(40), pady=(px(28), px(18)))
        self.bar = ActionBar(self.root, self.theme, {"select": self.select_or_clear, "copy": self.copy_command,
                                                     "preview": self.preview, "restart": self.restart,
                                                     "stop": self.skip})
        self.bar.pack(fill="x", side="bottom")
        for name in ("countdown_text", "copy_status", "copy_button"):
            setattr(self, name, getattr(self.bar, name))
        body = tk.Frame(self.root, background=ROOM)
        body.pack(fill="both", expand=True, padx=px(40), pady=(0, px(24)))
        self.recovery_tab = tk.Frame(body, background=ROOM)
        self.now_tab = tk.Frame(body, background=ROOM)
        self.all_tab = tk.Frame(body, background=ROOM)
        self.all_view = None          # built the first time All sessions is opened
        self.strip = FilmStrip(self.recovery_tab, self.theme, self.pick_stop)
        self.strip.pack(fill="x", pady=(0, px(26)))
        self.strip.set_stops(self.stops, tick_positions(self.history, self.stops), reboot_marks(self.stops), 0)
        self.map = SnapshotMap(self.recovery_tab, on_change=self.selection_changed, theme=self.theme,
                               on_restart=self.restart_one)
        self.map.pack(fill="both", expand=True)

    # WHAT IT DOES: Reads this PC's monitor names and wallpapers off the Tk thread, then shows them on the map.
    # DEPENDS ON: keeper_desktop_look (never raises; [] keeps the tinted desktops and "Monitor N").
    # AFFECTS: Display only. Started by the app's real entry points, not by building the screen.
    def read_desktop_look(self):
        def show(look):
            if self.map.winfo_exists():
                self.map.set_look(look)
        gc.collect()      # free old Tk objects here on the Tk thread, so the worker never has to
        run_background(self.root, read_desktop_look, show, lambda error: None)

    # WHAT IT DOES: Shows Saved recovery, On screen now or All sessions. Only Saved recovery can select or restart.
    # DEPENDS ON: The three view frames; All sessions is built (and starts reading) the first time it opens.
    # AFFECTS: Which frame is packed, the switch and the bar's buttons.
    def show_view(self, name):
        self.current_view = name
        frames = {SAVED: self.recovery_tab, NOW: self.now_tab, ALL: self.all_tab}
        for key, frame in frames.items():
            if key != name:
                frame.pack_forget()
        frames[name].pack(fill="both", expand=True)
        if name == ALL and self.all_view is None:
            self.all_view = AllSessionsView(self.all_tab, self.theme)
            self.all_view.pack(fill="both", expand=True)
            self.all_view.start()
        self.header.set_view(name)
        self.update_summary()
        self.selection_changed()

    # WHAT IT DOES: The one-line summary under the title.
    def update_summary(self):
        if getattr(self, "current_view", SAVED) == NOW:
            self.header.sub_label.configure(text="What is open on your screen right now. Nothing here is restarted.")
            return
        if getattr(self, "current_view", SAVED) == ALL:
            self.header.sub_label.configure(text="Every Claude Code and Codex conversation saved on this computer. "
                                                 "Nothing here is restarted.")
            return
        windows = self.snapshot.get("windows", [])
        tabs = sum(len(window.get("tabs", [])) for window in windows)
        monitors = len(self.snapshot.get("monitors") or [])
        parts = [f"Saved {saved_when(self.snapshot)}"] + ([f"{monitors} monitors"] if monitors > 1 else [])
        parts += [f"{len(windows)} windows", f"{tabs} tabs"]
        self.header.sub_label.configure(text="  ·  ".join(parts))

    # WHAT IT DOES: Updates the count and which bar buttons can be used.
    # DEPENDS ON: options, the current view and the map's tab states.
    # AFFECTS: Bar widgets only.
    def selection_changed(self):
        count = len(self.selected())
        saved = getattr(self, "current_view", SAVED) == SAVED
        ready = "normal" if saved and count and not self.busy else "disabled"
        choosable = [key for key in self.options if self.map.tab_state(key) in ("missing", "selected")]
        all_chosen = bool(choosable) and all(self.options[key].get() for key in choosable)
        self.bar.count_number.configure(text=str(count))
        self.bar.select_btn.configure(text="Clear all" if all_chosen else "Select all",
                                      state="normal" if saved and choosable else "disabled")
        self.copy_button.configure(state=ready, text="Copy command", icon=IC_COPY)
        self.bar.preview_btn.configure(state=ready)
        # Nothing picked: the big button restores every missing tab, and the note says so.
        restore_all = saved and not count and choosable and not self.busy
        if restore_all:
            self.bar.restart_btn.configure(state="normal", text=f"Restore all {len(choosable)} missing")
        else:
            self.bar.restart_btn.configure(state=ready, text=f"Restart {count} selected" if count else "Restart selected")
        note = ("To bring everything back, press Restore all. To bring back only some, click those tabs first."
                if restore_all else "")
        if saved and not choosable and not self.busy:
            note = "Every chat here is already open, so there is nothing to restore."
        self.copy_status.configure(text=note if saved else "Switch to Saved recovery to pick tabs", foreground=GO_TEXT)

    # WHAT IT DOES: Dark mode / Light mode. Saves the choice, then reopens this screen so every part of it is
    # drawn in the new colours (colours are fixed when the app starts; nothing is recoloured live).
    # DEPENDS ON: keeper_settings.record_theme; keeper_single.relaunch_recover_screen (recover mode only).
    # AFFECTS: The saved setting; this window closes and a fresh one opens. Never during a restore or a
    # countdown, and never from the login (watch) screen, which only saves the choice for next time.
    def switch_theme(self, mode, relaunch=relaunch_recover_screen):
        if mode == MODE:
            return
        if self.busy or getattr(self, "countdown_job", None) or getattr(self, "native_wait", None):
            self.countdown_text.configure(text="Wait until the restore or countdown finishes, then switch.")
            return
        record_theme(mode)
        name = "Light mode" if mode == "light" else "Dark mode"
        if getattr(self, "watch_mode", False):
            self.countdown_text.configure(text=f"{name} is saved. You will see it next time Session Keeper opens.")
            return
        try:
            relaunch()
        except (OSError, RuntimeError):
            self.countdown_text.configure(text=f"{name} is saved. Close and reopen Session Keeper to see it.")
            return
        self.root.destroy()

    # WHAT IT DOES: Select all, or Clear all when every missing tab is already selected.
    # DEPENDS ON: The map's tab states.
    # AFFECTS: Selection only.
    def select_or_clear(self):
        choosable = [key for key in self.options if self.map.tab_state(key) in ("missing", "selected")]
        if choosable and all(self.options[key].get() for key in choosable):
            for key in choosable:
                self.options[key].set(False)
            self.map.draw()
            self.selection_changed()
        else:
            self.check_all()


# WHAT IT DOES: Formats a snapshot's capture time for the header.
# DEPENDS ON: ISO capture time.
# AFFECTS: Label only.
def saved_time(snapshot):
    try:
        moment = datetime.fromisoformat(snapshot['captured_at'].replace('Z', '+00:00')).astimezone()
        return f"{moment:%b %d}, {moment:%I:%M %p}".replace(", 0", ", ")
    except (KeyError, TypeError, ValueError):
        return snapshot.get("captured_at", "unknown time")
