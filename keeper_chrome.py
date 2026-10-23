# Last modified: 2026-10-21 | Change: Header (title, view switch, legend) and bottom action bar of the recovery screen | Full history: git log
"""Widgets around the map: the header and the bottom bar. Display only; every button calls back into
keeper_app.RecoveryWindow, which owns the behaviour and the safety checks."""

import tkinter as tk

from keeper_theme import (AMBER, BEZEL, DIM, EDGE, GO, IC_COPY, IC_PREVIEW, IC_RESTART, INK, OPEN_FILL, ROOM,
                          STEEL, FlatButton, checkbox, label, rounded)

SAVED, NOW = "saved", "now"
GUIDE_STEPS = ("Crashed? Press Restore all (bottom right) to bring everything back.",
               "Want one chat? Click its tab, then press Open it for me.",
               "Grey = already open.  Red dot = missing.  Green = picked.")


# WHAT IT DOES: The header: SESSION KEEPER, the Saved recovery / On screen now switch, the legend,
# a one-line summary and the auto-restore checkbox.
# DEPENDS ON: Theme; on_view(name) callback; the auto-restore BooleanVar and its save callback.
# AFFECTS: Widgets only.
class Header(tk.Frame):
    def __init__(self, parent, theme, on_view, auto_var, on_auto):
        super().__init__(parent, background=ROOM)
        t, px = theme, theme.px
        top = tk.Frame(self, background=ROOM)
        top.pack(fill="x")
        label(top, t, "SESSION KEEPER", kind="display", size=34).pack(side="left", padx=(0, px(36)))
        switch = tk.Frame(top, background=EDGE, padx=px(2), pady=px(2))
        switch.pack(side="left")
        self.saved_btn = FlatButton(switch, t, "Saved recovery", lambda: on_view(SAVED), kind="seg", height=40)
        self.saved_btn.pack(side="left")
        tk.Frame(switch, background=EDGE, width=px(2)).pack(side="left", fill="y")
        self.now_btn = FlatButton(switch, t, "On screen now", lambda: on_view(NOW), kind="seg", height=40)
        self.now_btn.pack(side="left")
        for button in (self.saved_btn, self.now_btn):
            button.configure(highlightthickness=0)
        self.legend = tk.Canvas(top, height=px(30), width=px(470), background=ROOM, highlightthickness=0)
        self.legend.pack(side="right")
        self.draw_legend(t)
        second = tk.Frame(self, background=ROOM)
        second.pack(fill="x", pady=(px(14), 0))
        self.sub_label = label(second, t, size=20, colour=DIM, anchor="w")
        self.sub_label.pack(side="left")
        self.auto_box = checkbox(second, t, "Restore saved chats at next Windows login (10-second Stop countdown)",
                                 auto_var, on_auto)
        self.auto_box.pack(side="right")
        # Where to start, for someone who has never seen the screen. Saved recovery only.
        # Green frame and green step numbers so the eye lands here first.
        self.guide = tk.Frame(self, background=BEZEL, highlightthickness=px(2), highlightbackground=GO,
                              highlightcolor=GO)
        label(self.guide, t, "HOW TO USE", kind="display", size=24, colour=GO, anchor="w").grid(
            row=0, column=0, sticky="w", padx=(px(20), px(28)), pady=px(14))
        for number, text in enumerate(GUIDE_STEPS, start=1):
            step = tk.Frame(self.guide, background=BEZEL)
            step.grid(row=0, column=number, sticky="w", padx=(0, px(28)), pady=px(14))
            label(step, t, str(number), kind="display", size=28, colour=GO).pack(side="left", padx=(0, px(10)))
            label(step, t, text, size=20, colour=INK, anchor="w", justify="left",
                  wraplength=px(620)).pack(side="left")
            self.guide.grid_columnconfigure(number, weight=1)
        self.guide_gap = px(14)
        self.guide.pack(fill="x", pady=(self.guide_gap, 0))
        self.notice_label = label(self, t, size=19, colour=AMBER, anchor="w", justify="left")

    # WHAT IT DOES: Open / Missing / Selected swatches drawn exactly like the tabs they explain.
    def draw_legend(self, t):
        canvas, px = self.legend, t.px
        font = t.font("body", 20)
        x, middle = px(470), px(15)
        for text, fill, line, dash in (("Selected", GO, GO, None), ("Missing", ROOM, AMBER, (4, 3)),
                                       ("Open", OPEN_FILL, STEEL, None)):
            item = canvas.create_text(x, middle, text=text, anchor="e", font=font, fill=INK)
            left = canvas.bbox(item)[0]
            canvas.create_polygon(rounded(left - px(42), middle - px(11), left - px(12), middle + px(11), px(5)),
                                  smooth=True, fill=fill, outline=line, width=px(2), dash=dash)
            x = left - px(70)

    # WHAT IT DOES: Marks the active view in the switch.
    def set_view(self, name):
        self.saved_btn.configure(kind="seg_on" if name == SAVED else "seg")
        self.now_btn.configure(kind="seg_on" if name == NOW else "seg")
        # The legend explains the map's tabs, so it only shows where the map is.
        if name == SAVED:
            self.legend.pack(side="right")
            # Above the notice line when one is showing, so the order never changes.
            place = {"before": self.notice_label} if self.notice_label.winfo_manager() else {}
            self.guide.pack(fill="x", pady=(self.guide_gap, 0), **place)
        else:
            self.legend.pack_forget()
            self.guide.pack_forget()

    # WHAT IT DOES: Shows a notice line (old capture note, workers not restored), or hides it when empty.
    def set_notice(self, text):
        self.notice_label.configure(text=text)
        if text:
            self.notice_label.pack(fill="x", pady=(self.notice_label.winfo_reqheight() // 4, 0))
        else:
            self.notice_label.pack_forget()


# WHAT IT DOES: The bottom bar: Select all, the count, Copy command, Preview, a status line,
# the wide Restart N selected and the red Stop.
# DEPENDS ON: Theme; a dict of callbacks: select, copy, preview, restart, stop.
# AFFECTS: Widgets only.
class ActionBar(tk.Frame):
    def __init__(self, parent, theme, actions):
        super().__init__(parent, background=BEZEL, highlightthickness=0)
        t, px = theme, theme.px
        tk.Frame(self, background=EDGE, height=px(2)).pack(side="top", fill="x")
        row = tk.Frame(self, background=BEZEL)
        row.pack(fill="x", padx=px(40), pady=px(26))
        self.select_btn = FlatButton(row, t, "Select all", actions["select"], width=180)
        self.select_btn.pack(side="left")
        count = tk.Frame(row, background=BEZEL)
        count.pack(side="left", padx=(px(28), px(28)))
        self.count_number = label(count, t, "0", kind="display", size=30)
        self.count_number.pack(side="left", padx=(0, px(8)))
        label(count, t, "selected", size=22, colour=DIM).pack(side="left")
        self.copy_button = FlatButton(row, t, "Copy command", actions["copy"], icon=IC_COPY, width=180)
        self.copy_button.pack(side="left")
        self.preview_btn = FlatButton(row, t, "Preview", actions["preview"], icon=IC_PREVIEW, width=180)
        self.preview_btn.pack(side="left", padx=(px(18), 0))
        self.stop_btn = FlatButton(row, t, "Stop", actions["stop"], kind="red", size=26, height=80, width=170)
        self.stop_btn.pack(side="right")
        self.restart_btn = FlatButton(row, t, "Restart selected", actions["restart"], kind="go", icon=IC_RESTART,
                                      size=26, height=80, width=520)
        self.restart_btn.pack(side="right", padx=(0, px(18)))
        status = tk.Frame(row, background=BEZEL)
        status.pack(side="left", fill="x", expand=True, padx=px(24))
        self.countdown_text = label(status, t, size=20, colour=INK, anchor="w", justify="left",
                                    wraplength=px(560))
        self.countdown_text.pack(fill="x")
        self.copy_status = label(status, t, size=19, colour=GO, anchor="w")
        self.copy_status.pack(fill="x")
