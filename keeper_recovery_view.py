# Last modified: 2026-10-22 | Change: Reads monitor names and wallpapers for the map off the Tk thread | Full history: git log
"""Layout and bar state for keeper_app.RecoveryWindow. Display only: selection rules live in keeper_picker,
restore and safety checks stay in keeper_app."""

import gc
import tkinter as tk
from datetime import datetime

from keeper_background import run_background
from keeper_chrome import NOW, SAVED, ActionBar, Header
from keeper_desktop_look import read_desktop_look
from keeper_picker import SnapshotMap
from keeper_scrubber import FilmStrip, reboot_marks, tick_positions
from keeper_settings import record_auto_restore
from keeper_theme import GO, IC_COPY, ROOM


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
                             lambda: record_auto_restore(self.auto_choice.get()))
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

    # WHAT IT DOES: Shows Saved recovery or On screen now. Only Saved recovery can select or restart.
    # DEPENDS ON: The two view frames.
    # AFFECTS: Which frame is packed, the switch and the bar's buttons.
    def show_view(self, name):
        self.current_view = name
        (self.now_tab if name == SAVED else self.recovery_tab).pack_forget()
        (self.recovery_tab if name == SAVED else self.now_tab).pack(fill="both", expand=True)
        self.header.set_view(name)
        self.update_summary()
        self.selection_changed()

    # WHAT IT DOES: The one-line summary under the title.
    def update_summary(self):
        if getattr(self, "current_view", SAVED) == NOW:
            self.header.sub_label.configure(text="What is open on your screen right now. Nothing here is restarted.")
            return
        windows = self.snapshot.get("windows", [])
        tabs = sum(len(window.get("tabs", [])) for window in windows)
        monitors = len(self.snapshot.get("monitors") or [])
        parts = [f"Saved {saved_time(self.snapshot)}"] + ([f"{monitors} monitors"] if monitors > 1 else [])
        parts += [f"{len(windows)} windows", f"{tabs} tabs", "Click a tab to see how to bring it back"]
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
        self.bar.restart_btn.configure(state=ready, text=f"Restart {count} selected" if count else "Restart selected")
        self.copy_status.configure(text="" if saved else "Switch to Saved recovery to pick tabs", foreground=GO)

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
