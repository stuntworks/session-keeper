# Last modified: 2026-11-10 | Change: Reload also refreshes the crash facts and opens on Before the crash when preselected | Full history: git log
"""Opening Session Keeper while its recovery screen is already open only brings that screen to the front
(keeper_single). That screen still showed the layouts it read when it first opened, possibly days ago. The
second launch now leaves a note (keeper_single.request_reload); this watches for the note and reloads.

Never while a restore, preview, question, countdown or layout pick is in progress: the note waits and is
acted on once the screen is idle, so an in-progress restore is never touched.
"""

from keeper_background import run_background
from keeper_chooser import chooser_entries
from keeper_chrome import SAVED
from keeper_crash import start_index
from keeper_scrubber import reboot_marks, scrubber_stops, tick_positions
from keeper_single import reload_note

POLL_MS = 1000


# WHAT IT DOES: True while the screen is doing something a reload could disturb.
# DEPENDS ON: RecoveryWindow's busy / countdown_job / native_wait / pick_token.
# AFFECTS: Nothing.
def screen_busy(view):
    return bool(view.busy or view.countdown_job or view.native_wait or view.pick_token)


# WHAT IT DOES: Swaps in freshly read history and shows its newest layout, like opening the screen anew.
# DEPENDS ON: history (newest first) and the live session IDs, both read off the Tk thread.
# AFFECTS: The chooser, the time strip, the map and the selection (the user re-launched, so a fresh start).
# extras: fresh keeper_crash.crash_facts; the screen opens on "Before the crash" when that is preselected.
def reload_history(view, history, live, extras=None):
    view.history, view.extras = history, extras
    view.entries = chooser_entries(history, crash=(extras or {}).get("crash"))
    view.stops = scrubber_stops(history, view.entries)
    view.strip.set_stops(view.stops, tick_positions(history, view.stops), reboot_marks(view.stops), 0)
    view.load(*history[start_index(history, extras)], live)
    view.show_view(SAVED)


# WHAT IT DOES: Polls the "please reload" note once a second and reloads the screen when it changes.
# DEPENDS ON: A RecoveryWindow in recover mode; load (keeper_start.load_recovery_start) runs off the Tk
# thread; after_reload(view) refreshes anything else that depends on the newest layout (recorder warning).
# AFFECTS: The screen's history and display only. Never launches, closes or restores anything.
class ReloadWatch:
    def __init__(self, view, path, load, after_reload=None, every=POLL_MS, run=run_background):
        self.view, self.path, self.load, self.every, self.run = view, path, load, every, run
        self.after_reload = after_reload
        self.seen = reload_note(path)        # a note left before this screen existed is already served
        self.loading = False

    def start(self):
        self.view.root.after(self.every, self.poll)

    # WHAT IT DOES: One look at the note; reloads only when it is new and the screen is idle.
    def poll(self):
        try:
            if not self.view.root.winfo_exists():
                return
        except Exception:
            return
        note = reload_note(self.path)
        if note is not None and note != self.seen and not self.loading and not screen_busy(self.view):
            self.seen = note
            self.loading = True
            self.view.countdown_text.configure(text="Loading the newest saved layouts...")
            self.run(self.view.root, self.load, self.loaded, self.failed)
        self.view.root.after(self.every, self.poll)

    # WHAT IT DOES: Shows the reloaded history, unless the user started something while it was being read.
    def loaded(self, result):
        self.loading = False
        history, live, *extras = result         # extras: (crash facts,) from load_recovery_start
        if screen_busy(self.view):
            self.seen = None                 # try again once the screen is idle
            return
        self.view.countdown_text.configure(text="")
        if history:
            reload_history(self.view, history, live, *extras)
            if self.after_reload:
                self.after_reload(self.view)

    def failed(self, error):
        self.loading = False
        if not screen_busy(self.view):
            self.view.countdown_text.configure(text=f"Could not load the newest saved layouts: {error}")
