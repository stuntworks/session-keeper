# Last modified: 2026-10-21 | Change: Right-side details drawer for the recovery map (dark, slides in) | Full history: git log
"""The details drawer: a hint, one tab's details and exact resume command, or a preview/restart report.
Display only. Restart this tab calls back into the recovery window, which runs the normal guarded restore."""

import tkinter as tk

from keeper_background import run_background
from keeper_theme import (AMBER, BEZEL, CLAUDE, CLAUDE_MARK, DIM, EDGE, GO, IC_CHECK, IC_CLOSE, IC_COPY, IC_RESTART,
                          INK, OPEN_FILL, STEEL, TERM, FlatButton, label, reduced_motion, rounded)

try:  # built separately; the drawer works without it
    from keeper_agentinfo import agent_facts, format_facts
except ImportError:
    agent_facts = format_facts = None

READING = "Reading…"
PILLS = {"missing": ("Missing", None, AMBER, AMBER, (5, 3)), "selected": ("Missing · selected", GO, "#000000", GO, None),
         "live": ("Open", OPEN_FILL, INK, STEEL, None), "unknown": ("Can't be identified", None, INK, STEEL, (2, 3))}


# WHAT IT DOES: The drawer frame with its three modes; the map fills it.
# DEPENDS ON: Theme; on_restart(key) and on_close() callbacks.
# AFFECTS: Its own widgets and the clipboard (Copy command) only.
class Drawer(tk.Frame):
    def __init__(self, parent, theme, on_restart=None, on_close=None):
        self.theme, self.on_restart, self.on_close = theme, on_restart, on_close
        super().__init__(parent, background=BEZEL, highlightthickness=theme.px(2), highlightbackground=EDGE,
                         width=theme.px(470))
        self.pack_propagate(False)
        self.inner = tk.Frame(self, background=BEZEL)
        self.inner.place(x=0, y=0, relwidth=1, relheight=1)
        self.key, self.shown_command, self.mode, self.slide_job, self.flash_job = None, "", None, None, None
        self.dock = {}         # pack options set by the map; the drawer is only packed while it has content
        self.facts_token = None
        self.build_detail()
        self.build_hint()
        self.build_message()
        self.show_hint("Click a tab to see\nhow to bring it back")

    def pad(self):
        return self.theme.px(30)

    # WHAT IT DOES: Builds the hint view: a dashed ghost tab and one line of direction.
    def build_hint(self):
        t, px = self.theme, self.theme.px
        self.hint = tk.Frame(self.inner, background=BEZEL)
        box = tk.Frame(self.hint, background=BEZEL)
        box.place(relx=0.5, rely=0.45, anchor="center")
        ghost = tk.Canvas(box, width=px(156), height=px(52), background=BEZEL, highlightthickness=0)
        ghost.create_polygon(rounded(px(3), px(3), px(153), px(49), px(8)), smooth=True, fill=BEZEL,
                             outline=AMBER, width=px(2), dash=(5, 3))
        ghost.create_oval(px(52), px(21), px(62), px(31), fill=AMBER, width=0)
        ghost.create_text(px(72), px(26), text="tab", anchor="w", font=t.font("body", 20), fill=INK)
        ghost.pack(pady=(0, px(22)))
        self.hint_label = label(box, t, size=20, colour=DIM, justify="center")
        self.hint_label.pack()

    # WHAT IT DOES: Builds the detail view widgets once; show_detail fills them.
    def build_detail(self):
        t, px, pad = self.theme, self.theme.px, self.pad()
        self.detail = frame = tk.Frame(self.inner, background=BEZEL)
        buttons = tk.Frame(frame, background=BEZEL)
        buttons.pack(side="bottom", fill="x", padx=pad, pady=(px(10), pad))
        self.restart_btn = FlatButton(buttons, t, "Restart this tab", self.restart_one, kind="go", icon=IC_RESTART)
        self.restart_btn.pack(side="bottom", fill="x", pady=(px(14), 0))
        self.copy_btn = FlatButton(buttons, t, "Copy command", self.copy_detail, kind="line", icon=IC_COPY)
        self.copy_btn.pack(side="bottom", fill="x")
        self.copy_status = self.copy_btn            # the button itself says "Copied"
        head = tk.Frame(frame, background=BEZEL)
        head.pack(fill="x", padx=pad, pady=(pad, 0))
        close = label(head, t, IC_CLOSE, kind="icons", size=16, colour=DIM, cursor="hand2", padx=px(12), pady=px(10))
        close.pack(side="right", anchor="n")
        close.bind("<Button-1>", lambda event: self.close())
        self.head_label = label(head, t, kind="display", size=34, anchor="w", justify="left",
                                wraplength=px(340))
        self.head_label.pack(side="left", fill="x")
        self.badge = tk.Canvas(frame, height=px(40), background=BEZEL, highlightthickness=0)
        self.badge.pack(fill="x", padx=pad, pady=(px(8), 0))
        self.rows = tk.Frame(frame, background=BEZEL)
        self.rows.pack(fill="x", padx=pad, pady=(px(22), px(6)))
        self.values = {}
        for number, name in enumerate(("FOLDER", "WINDOW", "LAST SEEN", "STATUS")):
            label(self.rows, t, name, kind="display", size=18, colour=DIM, anchor="w").grid(
                row=number, column=0, sticky="nw", pady=px(6), padx=(0, px(14)))
            value = label(self.rows, t, kind="mono" if name == "FOLDER" else "body", size=18 if name == "FOLDER" else 19,
                          anchor="w", justify="left", wraplength=px(290))
            value.grid(row=number, column=1, sticky="w", pady=px(6))
            self.values[name] = value
        self.status_pill = tk.Canvas(self.rows, height=px(36), width=px(260), background=BEZEL, highlightthickness=0)
        self.values["STATUS"].grid_forget()
        self.status_pill.grid(row=3, column=1, sticky="w", pady=px(6))
        self.facts = tk.Frame(frame, background=BEZEL)
        self.facts.pack(fill="x", padx=pad)
        label(frame, t, "COMMAND", kind="display", size=18, colour=DIM, anchor="w").pack(
            fill="x", padx=pad, pady=(px(14), px(8)))
        self.command_box = tk.Text(frame, height=3, wrap="char", font=t.font("mono", 17), background=TERM,
                                   foreground=INK, insertbackground=INK, selectbackground=OPEN_FILL,
                                   relief="flat", highlightthickness=1, highlightbackground=EDGE,
                                   highlightcolor=EDGE, padx=px(14), pady=px(12))
        self.command_box.pack(fill="x", padx=pad)
        # Read-only but selectable: swallow typing, allow Ctrl+C / Ctrl+A / arrows.
        self.command_box.bind("<Key>", lambda event: None if (event.state & 4 and event.keysym.lower() in "ca")
                              or event.keysym in ("Left", "Right", "Up", "Down", "Home", "End") else "break")
        self.note_label = label(frame, t, size=19, colour=AMBER, anchor="w", justify="left", wraplength=px(390))
        self.note_label.pack(fill="x", padx=pad, pady=(px(14), 0))

    # WHAT IT DOES: Builds the report view used for Preview and restart results.
    def build_message(self):
        t, px, pad = self.theme, self.theme.px, self.pad()
        self.message = frame = tk.Frame(self.inner, background=BEZEL)
        head = tk.Frame(frame, background=BEZEL)
        head.pack(fill="x", padx=pad, pady=(pad, px(10)))
        close = label(head, t, IC_CLOSE, kind="icons", size=16, colour=DIM, cursor="hand2", padx=px(12), pady=px(10))
        close.pack(side="right", anchor="n")
        close.bind("<Button-1>", lambda event: self.close())
        self.message_title = label(head, t, kind="display", size=34, anchor="w")
        self.message_title.pack(side="left")
        self.message_text = tk.Text(frame, wrap="word", font=t.font("body", 19), background=BEZEL, foreground=INK,
                                    relief="flat", highlightthickness=0, padx=0, pady=0, spacing1=px(6),
                                    spacing3=px(6))
        self.message_text.pack(fill="both", expand=True, padx=pad, pady=(0, pad))
        self.message_text.tag_configure("warn", foreground=AMBER)
        self.message_text.tag_configure("dim", foreground=DIM)

    # WHAT IT DOES: Swaps the visible view; slides it in from the right unless motion is reduced.
    def switch(self, mode, slide):
        for view in (self.hint, self.detail, self.message):
            view.pack_forget()
        {"hint": self.hint, "detail": self.detail, "message": self.message}[mode].pack(fill="both", expand=True)
        self.mode = mode
        # Empty drawer: hidden, so the map gets the full width. It docks again when there is content.
        if mode == "hint":
            self.pack_forget()
            return
        if self.dock and not self.winfo_manager():
            first = self.master.pack_slaves()
            self.pack(**self.dock, **({"before": first[0]} if first else {}))
        if slide and not reduced_motion():
            self.slide(4)

    def slide(self, frames_left):
        if self.slide_job:
            self.after_cancel(self.slide_job)
        self.inner.place_configure(x=self.theme.px(40) * frames_left / 4)
        self.slide_job = self.after(45, lambda: self.slide(frames_left - 1)) if frames_left else None

    def show_hint(self, text):
        self.key, self.shown_command = None, ""
        self.hint_label.configure(text=text)
        self.switch("hint", False)

    # WHAT IT DOES: Fills the detail view from a description built by the map (see SnapshotMap.describe).
    def show_detail(self, info):
        _t, _px = self.theme, self.theme.px
        changed = self.mode != "detail" or self.key != info["key"]
        self.key = info["key"]
        self.reset_copy()
        self.head_label.configure(text=info["name"])
        self.draw_badge(info["program"], info["kind"])
        self.values["FOLDER"].configure(text=info["folder"] or "(unknown)")
        self.values["WINDOW"].configure(text=info["where"])
        self.values["LAST SEEN"].configure(text=info["seen"])
        self.draw_pill(info["state"])
        self.command_box.delete("1.0", "end")
        self.command_box.insert("1.0", info["command"])
        lines = max(2, min(5, int(self.command_box.count("1.0", "end", "displaylines")[0])
                           if info["command"] else 2))
        self.command_box.configure(height=lines)
        self.shown_command = info["command"]
        note = info["note"]
        self.note_label.configure(text=note or "")
        self.copy_btn.configure(state="normal" if info["can_copy"] else "disabled")
        self.restart_btn.configure(state="normal" if info["can_restart"] else "disabled")
        self.show_facts(info.get("row"))
        self.switch("detail", changed)

    def draw_badge(self, program, kind):
        canvas, t, px = self.badge, self.theme, self.theme.px
        canvas.delete("all")
        if not program:
            return
        name = "Claude Code" if kind == "claude" else "Codex CLI"
        font = t.font("body", 19)
        width = font.measure(name) + px(60)
        canvas.create_polygon(rounded(px(2), px(2), width, px(38), px(18)), smooth=True, fill=BEZEL, outline=EDGE,
                              width=px(2))
        if kind == "claude":
            canvas.create_text(px(16), px(20), text=CLAUDE_MARK, anchor="w", font=t.font("symbol", 19), fill=CLAUDE)
        else:
            canvas.create_oval(px(16), px(13), px(30), px(27), outline=DIM, width=px(2.5))
        canvas.create_text(px(40), px(20), text=name, anchor="w", font=font, fill=INK)

    def draw_pill(self, state):
        canvas, t, px = self.status_pill, self.theme, self.theme.px
        canvas.delete("all")
        text, fill, colour, line, dash = PILLS[state]
        font = t.font("display", 19)
        prefix = IC_CHECK + " " if state == "selected" else ""
        width = font.measure(prefix + text) + px(32)
        canvas.create_polygon(rounded(px(2), px(2), width, px(34), px(16)), smooth=True, fill=fill or BEZEL,
                              outline=line, width=px(2), dash=dash)
        x = px(16)
        if prefix:
            item = canvas.create_text(x, px(18), text=IC_CHECK, anchor="w", font=t.font("icons", 14), fill=colour)
            x = canvas.bbox(item)[2] + px(6)
        canvas.create_text(x, px(18), text=text, anchor="w", font=font, fill=colour)

    # WHAT IT DOES: Shows LAST MESSAGE and the other agent facts, read off the Tk thread (keeper_agentinfo).
    # DEPENDS ON: keeper_agentinfo, if present; otherwise this section stays empty.
    # AFFECTS: The facts section only; a late answer for another tab is ignored.
    def show_facts(self, row):
        for child in self.facts.winfo_children():
            child.destroy()
        self.facts_token = None
        if agent_facts is None or not row:
            return
        token = object()
        self.facts_token = token
        label(self.facts, self.theme, READING, size=19, colour=DIM, anchor="w").pack(fill="x", pady=self.theme.px(6))
        run_background(self.winfo_toplevel(), lambda: format_facts(agent_facts(row)),
                       lambda facts: self.fill_facts(token, facts), lambda error: self.fill_facts(token, []))

    def fill_facts(self, token, facts):
        if token is not self.facts_token or not self.winfo_exists():
            return
        t, px = self.theme, self.theme.px
        for child in self.facts.winfo_children():
            child.destroy()
        grid = tk.Frame(self.facts, background=BEZEL)
        for number, (name, value) in enumerate(facts):
            if name.upper() == "LAST MESSAGE":
                label(self.facts, t, name.upper(), kind="display", size=18, colour=DIM, anchor="w").pack(
                    fill="x", pady=(px(8), px(4)))
                text = str(value)
                label(self.facts, t, text if len(text) <= 420 else text[:419] + "…", size=19, anchor="nw",
                      justify="left", wraplength=px(400)).pack(fill="x")
                continue
            label(grid, t, name.upper(), kind="display", size=18, colour=DIM, anchor="w").grid(
                row=number, column=0, sticky="w", pady=px(4), padx=(0, px(14)))
            label(grid, t, str(value), size=19, anchor="w", justify="left", wraplength=px(290)).grid(
                row=number, column=1, sticky="w", pady=px(4))
        grid.pack(fill="x", pady=(px(8), 0))

    # WHAT IT DOES: Shows a report (Preview or restart result). tone "warn" colours the lines amber.
    def show_message(self, title, lines, notes=()):
        self.key = None
        self.message_title.configure(text=title)
        self.message_text.configure(state="normal")
        self.message_text.delete("1.0", "end")
        for line in lines:
            self.message_text.insert("end", line + "\n")
        for note in notes:
            self.message_text.insert("end", note + "\n", "warn")
        self.message_text.configure(state="disabled")
        self.switch("message", True)

    # WHAT IT DOES: Copies exactly the command text shown; the button says "Copied" for 1.5 s.
    def copy_detail(self):
        if not self.shown_command:
            return
        self.clipboard_clear()
        self.clipboard_append(self.shown_command)
        self.update()
        self.copy_btn.configure(text="Copied", icon=IC_CHECK)
        self.flash_job = self.after(1500, self.reset_copy)

    def reset_copy(self):
        if self.flash_job:
            self.after_cancel(self.flash_job)
            self.flash_job = None
        self.copy_btn.configure(text="Copy command", icon=IC_COPY)

    def restart_one(self):
        if self.key is not None and self.on_restart:
            self.on_restart(self.key)

    def close(self):
        if self.on_close:
            self.on_close()
