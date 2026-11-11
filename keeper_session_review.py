# Last modified: 2026-10-22 | Change: Review panel for one saved session in the All sessions tab | Full history: git log
"""The review panel: one saved conversation's facts, what it worked on and its exact resume command.
Display only. There is no Restart here: a session outside a saved layout has no guarded restore path, and a
session that is running now must not be opened twice. The command is for the user to paste themselves."""

import tkinter as tk
from tkinter import ttk

from keeper_agentinfo import format_facts, when
from keeper_copycmd import build_resume_text
from keeper_theme import (AMBER, BEZEL, BLACK, CLAUDE, CLAUDE_MARK, DIM, EDGE, GO, IC_CHECK, IC_CLOSE, IC_COPY, INK,
                          OPEN_FILL, TERM, FlatButton, label, rounded)

READING = "Reading…"
SOON = "Ask about this session: coming soon"
RUNNING_NOTE = "This one is running now; opening it again would make a duplicate."
NO_FOLDER_NOTE = "The folder this session ran in is not known, so there is no command to copy."


# WHAT IT DOES: The right-hand panel of the All sessions tab, in the details drawer's look.
# DEPENDS ON: Theme; on_close() callback; entries from keeper_sessions and facts from keeper_agentinfo.
# AFFECTS: Its own widgets and the clipboard (Copy command) only.
class SessionReview(tk.Frame):
    def __init__(self, parent, theme, on_close=None):
        self.theme, self.on_close = theme, on_close
        t, px = theme, theme.px
        super().__init__(parent, background=BEZEL, highlightthickness=px(2), highlightbackground=EDGE,
                         highlightcolor=EDGE, width=px(720))
        self.pack_propagate(False)
        self.path, self.command, self.flash_job = None, "", None
        pad = px(30)
        head = tk.Frame(self, background=BEZEL)
        head.pack(fill="x", padx=pad, pady=(pad, 0))
        close = label(head, t, IC_CLOSE, kind="icons", size=16, colour=DIM, cursor="hand2", padx=px(12), pady=px(10))
        close.pack(side="right", anchor="n")
        close.bind("<Button-1>", lambda event: self.close())
        self.head_label = label(head, t, kind="display", size=32, anchor="w", justify="left", wraplength=px(580))
        self.head_label.pack(side="left", fill="x")
        self.badge = tk.Canvas(self, height=px(40), background=BEZEL, highlightthickness=0)
        self.badge.pack(fill="x", padx=pad, pady=(px(10), 0))
        bottom = tk.Frame(self, background=BEZEL)
        bottom.pack(side="bottom", fill="x", padx=pad, pady=(px(10), pad))
        label(bottom, t, "COMMAND", kind="display", size=18, colour=DIM, anchor="w").pack(fill="x", pady=(0, px(8)))
        self.command_box = tk.Text(bottom, height=2, wrap="char", font=t.font("mono", 17), background=TERM,
                                   foreground=INK, insertbackground=INK, selectbackground=OPEN_FILL, relief="flat",
                                   highlightthickness=1, highlightbackground=EDGE, highlightcolor=EDGE,
                                   padx=px(14), pady=px(12))
        self.command_box.pack(fill="x")
        # Read-only but selectable: swallow typing, allow Ctrl+C / Ctrl+A / arrows.
        self.command_box.bind("<Key>", lambda event: None if (event.state & 4 and event.keysym.lower() in "ca")
                              or event.keysym in ("Left", "Right", "Up", "Down", "Home", "End") else "break")
        self.note_label = label(bottom, t, size=19, colour=AMBER, anchor="w", justify="left", wraplength=px(640))
        self.note_label.pack(fill="x", pady=(px(10), 0))
        self.copy_btn = FlatButton(bottom, t, "Copy command", self.copy, kind="line", icon=IC_COPY)
        self.copy_btn.pack(fill="x", pady=(px(14), 0))
        # Placeholder for step 2 (asking a saved session a question): always in view, clearly not working yet.
        soon = tk.Frame(self, background=OPEN_FILL, padx=px(18), pady=px(12))
        soon.pack(side="bottom", fill="x", padx=pad, pady=(px(16), 0))
        self.soon_label = label(soon, t, SOON, kind="display", size=21, anchor="w")
        self.soon_label.pack(fill="x")
        label(soon, t, "Asking a saved session a question is planned for a later version.", size=18, colour=DIM,
              anchor="w", justify="left", wraplength=px(620)).pack(fill="x")
        area = tk.Frame(self, background=BEZEL)
        area.pack(fill="both", expand=True, padx=(pad, px(8)), pady=(px(18), 0))
        self.body = tk.Text(area, wrap="word", font=t.font("body", 19), background=BEZEL, foreground=INK,
                            relief="flat", highlightthickness=0, padx=0, pady=0, spacing1=px(3), spacing3=px(3),
                            cursor="arrow", selectbackground=OPEN_FILL)
        scroll = ttk.Scrollbar(area, orient="vertical", command=self.body.yview, style="Keeper.Vertical.TScrollbar")
        self.body.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.body.pack(side="left", fill="both", expand=True)
        self.body.tag_configure("head", font=t.font("display", 18), foreground=DIM, spacing1=px(14))
        self.body.tag_configure("section", font=t.font("display", 22), foreground=INK, spacing1=px(26))
        self.body.tag_configure("mono", font=t.font("mono", 17))
        self.body.tag_configure("dim", foreground=DIM)
        column = px(190)
        self.body.tag_configure("pair_name", font=t.font("display", 18), foreground=DIM, tabs=(column,),
                                lmargin2=column, spacing1=px(8))
        self.body.tag_configure("pair", lmargin2=column, tabs=(column,))

    # WHAT IT DOES: Shows one session. facts is None while it is still being read; running marks it as open now.
    # DEPENDS ON: A keeper_sessions entry (title/cwd filled by peek) and its agent_facts.
    # AFFECTS: This panel only.
    def show(self, entry, facts, running):
        self.path = entry["path"]
        self.reset_copy()
        title = (facts or {}).get("title") or entry.get("title") or f"Untitled session {entry['session_id'][:8]}"
        self.head_label.configure(text=title)
        self.draw_badge(entry["kind"], running)
        self.fill_body(entry, facts)
        command, note = "", ""
        if entry.get("cwd"):
            try:
                command = build_resume_text([{"session_id": entry["session_id"], "kind": entry["kind"],
                                              "cwd": entry["cwd"], "title": title}])
            except (ValueError, KeyError, RuntimeError) as error:
                note = f"No command: {error}."
        else:
            note = NO_FOLDER_NOTE
        if running:
            note = RUNNING_NOTE + ("  " + note if note else "")
        self.command = command
        self.command_box.delete("1.0", "end")
        self.command_box.insert("1.0", command)
        self.copy_btn.configure(state="normal" if command else "disabled")
        self.note_label.configure(text=note)

    def fill_body(self, entry, facts):
        body = self.body
        body.configure(state="normal")
        body.delete("1.0", "end")
        self.add("FOLDER", entry.get("cwd") or entry.get("folder") or "Folder unknown", mono=True)
        if facts is None:
            body.insert("end", "\n" + READING + "\n", "dim")
        elif facts.get("available"):
            self.add_work(facts)                    # what it did comes first; the counts follow
        body.insert("end", "DETAILS\n", "section")
        self.pair("SESSION ID", entry["session_id"], mono=True)
        for name, value in format_facts(facts) if facts is not None else ():
            if name not in ("LAST MESSAGE", "WHEN"):   # the replies above show the last one, with its time
                self.pair(name, str(value))
        body.configure(state="disabled")
        body.yview_moveto(0)

    # WHAT IT DOES: One "NAME   value" line; long values wrap under the value column.
    def pair(self, name, value, mono=False):
        self.body.insert("end", name + "\t", "pair_name")
        self.body.insert("end", value + "\n", ("pair", "mono") if mono else "pair")

    def add(self, name, value, mono=False):
        self.body.insert("end", name + "\n", "head")
        self.body.insert("end", value + "\n", "mono" if mono else ())

    # WHAT IT DOES: "What it worked on": the last replies (newest first, with times) and the files it changed.
    def add_work(self, facts):
        body = self.body
        body.insert("end", "WHAT IT WORKED ON\n", "section")
        body.insert("end", "LAST REPLIES\n", "head")
        replies = facts.get("recent_replies") or []
        if not replies:
            body.insert("end", "No reply text in the transcript\n", "dim")
        for moment, text in replies:
            body.insert("end", when(moment) + "\n", "dim")
            body.insert("end", text + "\n")
        body.insert("end", "FILES IT CHANGED\n", "head")
        files = facts.get("recent_files") or []
        if not files:
            body.insert("end", "No files changed\n", "dim")
        for name in files:
            body.insert("end", name + "\n", "mono")
        if files and facts.get("files_changed", 0) > len(files):
            body.insert("end", f"Showing the {len(files)} most recent of {facts['files_changed']:,} files\n", "dim")

    def draw_badge(self, kind, running):
        canvas, t, px = self.badge, self.theme, self.theme.px
        canvas.delete("all")
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
        if running:
            pill = t.font("display", 19)
            left = width + px(14)
            right = left + pill.measure("Running now") + px(32)
            canvas.create_polygon(rounded(left, px(2), right, px(38), px(18)), smooth=True, fill=GO, outline=GO)
            canvas.create_text(left + px(16), px(20), text="Running now", anchor="w", font=pill, fill=BLACK)

    # WHAT IT DOES: Copies exactly the command shown; the button says "Copied" for 1.5 s.
    def copy(self):
        if not self.command:
            return
        self.clipboard_clear()
        self.clipboard_append(self.command)
        self.update()
        self.copy_btn.configure(text="Copied", icon=IC_CHECK)
        self.flash_job = self.after(1500, self.reset_copy)

    def reset_copy(self):
        if self.flash_job:
            self.after_cancel(self.flash_job)
            self.flash_job = None
        self.copy_btn.configure(text="Copy command", icon=IC_COPY)

    def close(self):
        if self.on_close:
            self.on_close()
