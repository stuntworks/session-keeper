# Last modified: 2026-10-21 | Change: Dark confirm dialog; Cancel keeps default focus | Full history: git log
"""Asks before anything is closed or stacked. Default focus is Cancel. Only manual restores show it."""

import tkinter as tk

from keeper_theme import AMBER, BEZEL, EDGE, FlatButton, Theme, checkbox, label

CLOSE, BESIDE, CONTINUE = "close", "beside", "continue"


# WHAT IT DOES: Says whether every window covering a spot was classified EMPTY.
# DEPENDS ON: Occupant states from keeper_occupancy.classify_blocked.
# AFFECTS: Only such spots can be offered for closing.
def all_empty(spot):
    return bool(spot["occupants"]) and all(o.get("state") == "empty" for o in spot["occupants"])


# WHAT IT DOES: Turns the user's button and ticks into planner choices and the exact HWNDs approved to close.
# DEPENDS ON: Blocked spots, the button pressed, and indexes ticked "Open on top anyway".
# AFFECTS: Nothing closes or stacks unless it is in this result.
def plan_choices(blocked, action, on_top_indexes):
    choices, approved = {}, set()
    for spot in blocked:
        index = spot["window_index"]
        if all_empty(spot):
            if action == CLOSE:
                choices[index] = "close_empty"
                approved |= {o["hwnd"] for o in spot["occupants"]}
            elif action == BESIDE:
                choices[index] = "on_top"
        elif index in on_top_indexes:
            choices[index] = "on_top"
    return choices, approved


# WHAT IT DOES: Describes one covering window in plain words: position and tab titles.
# DEPENDS ON: Occupant geometry and tab titles from the window probe.
# AFFECTS: Dialog text only.
def describe(occupant):
    tabs = ", ".join(occupant.get("tabs") or []) or "no tabs"
    return f"at ({occupant.get('x')}, {occupant.get('y')}): {tabs}"


# WHAT IT DOES: Modal dialog listing empty terminals and in-use spots, returning the user's decision.
# DEPENDS ON: Classified blocked spots and a callback taking (choices, approved) or None for Cancel.
# AFFECTS: Calls on_done exactly once.
class OccupiedDialog(tk.Toplevel):
    def __init__(self, parent, blocked, on_done):
        super().__init__(parent, background=BEZEL)
        self.blocked, self.on_done, self.done = blocked, on_done, False
        theme = Theme(self, parent.winfo_toplevel().winfo_width() or None)
        px = theme.px
        self.title("Session Keeper - some saved spots are covered")
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        body = tk.Frame(self, background=BEZEL, highlightthickness=px(2), highlightbackground=EDGE)
        body.pack(fill="both", expand=True)
        inner = tk.Frame(body, background=BEZEL)
        inner.pack(fill="both", expand=True, padx=px(36), pady=px(30))
        wrap = px(1000)
        empty = [spot for spot in blocked if all_empty(spot)]
        busy = [spot for spot in blocked if not all_empty(spot)]
        self.on_top = {}
        if empty:
            label(inner, theme, "These terminals look empty. Close them and restart in their place?", kind="display",
                  size=28, anchor="w", justify="left", wraplength=wrap).pack(fill="x")
            for spot in empty:
                for occupant in spot["occupants"]:
                    label(inner, theme, f"Window {spot['window_index'] + 1} spot, terminal {describe(occupant)}",
                          kind="mono", size=18, anchor="w", justify="left", wraplength=wrap).pack(
                        fill="x", padx=px(16), pady=(px(10), 0))
            label(inner, theme, "A bare prompt can still hold typed text you have not sent. Closing it loses that "
                  "text.", size=19, colour=AMBER, anchor="w", justify="left", wraplength=wrap).pack(
                fill="x", pady=(px(16), px(20)))
        if busy:
            label(inner, theme, "These spots are covered by a terminal that is in use or could not be checked. "
                  "It is never closed. Tick to open the saved window on top of it anyway:", kind="display",
                  size=24, anchor="w", justify="left", wraplength=wrap).pack(fill="x", pady=(0, px(10)))
            for spot in busy:
                variable = tk.BooleanVar(value=False)
                self.on_top[spot["window_index"]] = variable
                text = (f"Open on top anyway: Window {spot['window_index'] + 1} ({spot['title']}), covered by the terminal "
                        + "; ".join(describe(o) for o in spot["occupants"]))
                box = checkbox(inner, theme, text, variable)
                box.configure(wraplength=wrap - px(40), justify="left")
                box.pack(fill="x", padx=px(16), pady=px(4))
        buttons = tk.Frame(inner, background=BEZEL)
        buttons.pack(fill="x", pady=(px(24), 0))
        self.cancel_button = FlatButton(buttons, theme, "Cancel", self.cancel, kind="line", width=170)
        self.cancel_button.pack(side="right")
        if empty:
            FlatButton(buttons, theme, "Restart beside them", lambda: self.finish(BESIDE), kind="line").pack(
                side="right", padx=px(14))
            FlatButton(buttons, theme, "Close empty ones and restart", lambda: self.finish(CLOSE),
                       kind="go").pack(side="right")
        else:
            FlatButton(buttons, theme, "Continue", lambda: self.finish(CONTINUE), kind="go", width=170).pack(
                side="right", padx=px(14))
        self.bind("<Escape>", lambda event: self.cancel())
        # Default focus is Cancel, so a stray Enter or Space never closes or stacks anything.
        self.default_button = self.cancel_button
        self.cancel_button.focus_set()
        self.after_idle(self.focus_cancel)

    # WHAT IT DOES: Puts keyboard focus on Cancel once the dialog is drawn.
    # DEPENDS ON: Tk idle callback.
    # AFFECTS: Focus only.
    def focus_cancel(self):
        if not self.done:
            self.cancel_button.focus_force()

    # WHAT IT DOES: Returns the decision for the pressed button.
    # DEPENDS ON: plan_choices.
    # AFFECTS: Closes the dialog.
    def finish(self, action):
        ticked = {index for index, variable in self.on_top.items() if variable.get()}
        self.close(plan_choices(self.blocked, action, ticked))

    # WHAT IT DOES: Cancels the whole restore.
    # DEPENDS ON: Nothing.
    # AFFECTS: Closes the dialog; nothing is opened or closed.
    def cancel(self):
        self.close(None)

    def close(self, result):
        if self.done:
            return
        self.done = True
        self.destroy()
        self.on_done(result)
