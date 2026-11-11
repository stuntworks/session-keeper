# Last modified: 2026-10-22 | Change: Sort by chooser (last active, started, project) | Full history: git log
"""The All sessions tab. Read-only: it lists transcripts, reads them, and never writes to ~/.claude or
~/.codex, never launches anything.

Speed: the list comes from file metadata alone (keeper_sessions.list_transcripts, well under a second for
thousands of files). Names and folders come from a small peek at each file: visible rows at once, the rest
in a background pass so search finds them. Full facts (keeper_agentinfo.agent_facts) are measured one at a
time on a worker thread, only for rows on screen or clicked, and kept in a cache."""

import queue
import threading
import tkinter as tk
from tkinter import ttk

from keeper_agentinfo import agent_facts
from keeper_restore import live_session_ids
from keeper_session_review import SessionReview
from keeper_sessions import (SORTS, codex_titles, filter_entries, list_transcripts, peek_more, row_text,
                             sort_entries)
from keeper_theme import (BEZEL, BLACK, CLAUDE, CLAUDE_MARK, DIM, EDGE, GO, INK, ROOM, STOP, TERM, FlatButton,
                          label, rounded)

KINDS = (("all", "All"), ("claude", "Claude Code"), ("codex", "Codex"))
PEEK_CHUNK = 150
POLL_MS = 40
UNREADABLE = {"available": False, "reason": "Transcript can't be read"}


# WHAT IT DOES: Measures facts one transcript at a time, off the Tk thread. Clicked rows go first (newest
# click first), then the rows on screen in order; scrolling replaces the on-screen list.
# DEPENDS ON: A facts(row) function; results go into the shared queue as ("facts", path, facts).
# AFFECTS: Read-only; one daemon thread.
class FactsWorker:
    def __init__(self, facts, results):
        self.facts, self.results = facts, results
        self.cond = threading.Condition()
        self.urgent, self.wanted, self.done, self.stopped = [], [], set(), False
        threading.Thread(target=self.run, daemon=True).start()

    def want(self, rows, urgent=False):
        with self.cond:
            if urgent:
                self.urgent.extend(row for row in rows if row["path"] not in self.done)
            else:
                self.wanted = [row for row in rows if row["path"] not in self.done]
            self.cond.notify()

    def stop(self):
        with self.cond:
            self.stopped = True
            self.cond.notify()

    def run(self):
        while True:
            with self.cond:
                while not (self.urgent or self.wanted or self.stopped):
                    self.cond.wait()
                if self.stopped:
                    return
                row = self.urgent.pop() if self.urgent else self.wanted.pop(0)
                if row["path"] in self.done:
                    continue
                self.done.add(row["path"])
            self.results.put(("facts", row["path"], measure(self.facts, row)))


def measure(facts, row):
    try:
        return facts(row)
    except Exception:          # a damaged transcript must never break the list
        return UNREADABLE


def facts_row(entry):
    return {key: entry.get(key) for key in ("kind", "session_id", "path", "cwd")}


# WHAT IT DOES: The All sessions tab: search box, All / Claude Code / Codex filter, the list and the review.
# DEPENDS ON: Theme. lister/titles/peeker/facts/live default to the real read-only functions; tests pass fakes.
# sync=True does every read inline (tests only).
# AFFECTS: Widgets, the clipboard (Copy command in the review) and background reader threads only.
class AllSessionsView(tk.Frame):
    def __init__(self, parent, theme, lister=None, titles=None, peeker=None, facts=None, live=None, sync=False):
        super().__init__(parent, background=ROOM)
        theme.style_ttk()
        self.theme, self.sync = theme, sync
        self.lister, self.titles_reader = lister or list_transcripts, titles or codex_titles
        self.peeker, self.facts_fn, self.live_fn = peeker or peek_more, facts or agent_facts, live or live_session_ids
        self.entries, self.shown, self.titles, self.peeked, self.cache = [], [], {}, set(), {}
        self.live, self.kind, self.status, self.selected = set(), "all", "Finding saved sessions…", None
        self.sort = "active"                      # the choice lives only as long as this view
        self.results, self.worker, self.poll_job, self.started = queue.Queue(), None, None, False
        t, px = theme, theme.px
        top = tk.Frame(self, background=ROOM)
        top.pack(fill="x", pady=(0, px(18)))
        label(top, t, "Search", kind="display", size=22, colour=DIM).pack(side="left", padx=(0, px(14)))
        self.search_var = tk.StringVar()
        self.search = tk.Entry(top, textvariable=self.search_var, font=t.font("body", 22), background=TERM,
                               foreground=INK, insertbackground=INK, relief="flat", highlightthickness=px(2),
                               highlightbackground=EDGE, highlightcolor=INK, width=34)
        self.search.pack(side="left", ipady=px(8))
        label(top, t, "name, folder or date", size=19, colour=DIM).pack(side="left", padx=(px(14), px(30)))
        switch = tk.Frame(top, background=EDGE, padx=px(2), pady=px(2))
        switch.pack(side="left")
        self.kind_buttons = {}
        for number, (kind, text) in enumerate(KINDS):
            if number:
                tk.Frame(switch, background=EDGE, width=px(2)).pack(side="left", fill="y")
            button = FlatButton(switch, t, text, lambda kind=kind: self.set_kind(kind), kind="seg", height=40)
            button.configure(highlightthickness=0)
            button.pack(side="left")
            self.kind_buttons[kind] = button
        label(top, t, "Sort by", kind="display", size=22, colour=DIM).pack(side="left", padx=(px(30), px(14)))
        order = tk.Frame(top, background=EDGE, padx=px(2), pady=px(2))
        order.pack(side="left")
        self.sort_buttons = {}
        for number, (mode, text) in enumerate(SORTS):
            if number:
                tk.Frame(order, background=EDGE, width=px(2)).pack(side="left", fill="y")
            button = FlatButton(order, t, text, lambda mode=mode: self.set_sort(mode), kind="seg", height=40)
            button.configure(highlightthickness=0)
            button.pack(side="left")
            self.sort_buttons[mode] = button
        self.count_label = label(top, t, "", size=20, colour=DIM, anchor="e")
        self.count_label.pack(side="right")
        content = tk.Frame(self, background=ROOM)
        content.pack(fill="both", expand=True)
        self.review = SessionReview(content, t, on_close=self.close_review)
        self.review_dock = {"side": "right", "fill": "y", "padx": (px(20), 0)}
        self.list = SessionList(content, t, self)
        self.list.pack(side="left", fill="both", expand=True)
        self.search_var.trace_add("write", lambda *args: self.refilter())
        self.paint_kind()
        self.paint_sort()
        self.bind("<Destroy>", self.on_destroy, add="+")

    # WHAT IT DOES: Starts reading, once: the listing and the running-now check run off the Tk thread.
    def start(self):
        if self.started:
            return
        self.started = True
        if self.sync:
            self.titles = self.titles_reader()
            self.listed(self.lister())
            self.live = set(self.live_fn())
            self.list.draw()
            return
        self.worker = FactsWorker(self.facts_fn, self.results)
        self.background(lambda: ("listed", self.lister(), self.titles_reader()))
        self.background(lambda: ("live", set(self.live_fn())))
        self.list.draw()
        self.poll_job = self.after(POLL_MS, self.poll)

    def background(self, action):
        def work():
            try:
                self.results.put(action())
            except Exception as error:
                self.results.put(("error", str(error)))
        threading.Thread(target=work, daemon=True).start()

    # WHAT IT DOES: Takes finished reads from the queue and shows them; only this Tk-thread loop touches widgets.
    def poll(self):
        self.poll_job = None
        changed = False
        try:
            while True:
                changed = self.handle(self.results.get_nowait()) or changed
        except queue.Empty:
            pass
        if changed:
            self.list.draw()
        self.poll_job = self.after(POLL_MS, self.poll)

    def handle(self, message):
        kind = message[0]
        if kind == "listed":
            self.titles = message[2]
            self.listed(message[1])
            self.background(self.peek_all)
            return True
        if kind == "live":
            self.live = message[1]
            self.refresh_review()
            return True
        if kind == "peek":
            self.apply_peeks(message[1])
            if len(message) > 2 and message[2]:           # the whole pass is done: put every row in true order
                self.resort(keep_place=True)
            elif self.search_var.get().strip():
                self.refilter(keep_place=True)
            return True
        if kind == "facts":
            self.cache[message[1]] = message[2]
            if self.selected == message[1]:
                self.refresh_review()
            return True
        if kind == "error":
            self.status = f"Could not list saved sessions: {message[1]}"
            return True
        return False

    def listed(self, entries):
        self.entries = sort_entries(entries, self.sort)
        self.status = "No saved Claude Code or Codex sessions were found on this computer." if not entries else ""
        if self.sync:
            self.apply_peeks(self.peek_all(post=False))
            self.entries = sort_entries(self.entries, self.sort)
        self.refilter()

    # WHAT IT DOES: Names and folders for every row, in chunks, on a reader thread (so search finds them).
    def peek_all(self, post=True):
        found = {}
        for entry in list(self.entries):
            if entry["path"] in self.peeked:
                continue
            found[entry["path"]] = self.peeker(entry, self.titles)
            if post and len(found) >= PEEK_CHUNK:
                self.results.put(("peek", found))
                found = {}
        if not post:
            return found
        self.results.put(("peek", found, True))
        return ("noop",)

    def apply_peeks(self, found):
        for entry in self.entries:
            if entry["path"] in found:
                self.apply_peek(entry, found[entry["path"]])

    def apply_peek(self, entry, result):
        title, cwd, started, last_active = (tuple(result) + (None, None))[:4]
        entry["title"] = title or entry.get("title")
        entry["cwd"] = cwd or entry.get("cwd")
        entry["started"] = started or entry.get("started")
        entry["last_active"] = last_active or entry.get("last_active")
        self.peeked.add(entry["path"])

    # WHAT IT DOES: Called by the list for the rows it is about to draw: peeks them now if needed, and asks
    # the worker for their facts.
    def prepare(self, rows):
        for entry in rows:
            if entry["path"] not in self.peeked:
                self.apply_peek(entry, self.peeker(entry, self.titles))
        missing = [facts_row(entry) for entry in rows if entry["path"] not in self.cache]
        if self.sync:
            for row in missing:
                self.cache[row["path"]] = measure(self.facts_fn, row)
        elif missing and self.worker:
            self.worker.want(missing)

    # WHAT IT DOES: Puts the rows in the chosen order again. keep_place keeps the row that was at the top of
    # the screen exactly where it was, so nothing jumps under the user's eyes.
    def resort(self, keep_place=False):
        anchor = self.list.anchor() if keep_place else None
        self.entries = sort_entries(self.entries, self.sort)
        self.refilter(keep_place=keep_place, anchor=anchor)

    def set_sort(self, mode):
        self.sort = mode
        self.paint_sort()
        self.resort()

    def paint_sort(self):
        for mode, button in self.sort_buttons.items():
            button.configure(kind="seg_on" if mode == self.sort else "seg")

    def refilter(self, keep_place=False, anchor=None):
        self.shown = filter_entries(self.entries, self.search_var.get(), self.kind)
        total, shown = len(self.entries), len(self.shown)
        if not self.entries:
            text = ""
        elif shown == total:
            text = f"{total:,} sessions"
        else:
            text = f"{shown:,} of {total:,} sessions"
        self.count_label.configure(text=text)
        if not keep_place:
            self.list.top = 0
        elif anchor:
            self.list.restore(anchor)
        self.list.draw()

    def set_kind(self, kind):
        self.kind = kind
        self.paint_kind()
        self.refilter()

    def paint_kind(self):
        for kind, button in self.kind_buttons.items():
            button.configure(kind="seg_on" if kind == self.kind else "seg")

    # WHAT IT DOES: Opens the review panel for one row; its facts are read first if they are not cached yet.
    def open_review(self, entry):
        self.selected = entry["path"]
        if entry["path"] not in self.cache:
            row = facts_row(entry)
            if self.sync:
                self.cache[row["path"]] = measure(self.facts_fn, row)
            elif self.worker:
                self.worker.want([row], urgent=True)
        if not self.review.winfo_manager():
            self.review.pack(**self.review_dock, before=self.list)
        self.refresh_review()
        self.list.draw()

    def refresh_review(self):
        entry = next((item for item in self.entries if item["path"] == self.selected), None)
        if entry is not None:
            self.review.show(entry, self.cache.get(entry["path"]), entry["session_id"] in self.live)

    def close_review(self):
        self.selected = None
        self.review.pack_forget()
        self.list.draw()

    def on_destroy(self, event):
        if event.widget is not self:
            return
        if self.worker:
            self.worker.stop()
        if self.poll_job:
            self.after_cancel(self.poll_job)
            self.poll_job = None


# WHAT IT DOES: The scrolling list. Only the rows on screen are drawn, so thousands of sessions scroll smoothly.
# DEPENDS ON: The view's shown list, cache, live set and selected path.
# AFFECTS: Its canvas only; a click opens the review.
class SessionList(tk.Frame):
    def __init__(self, parent, theme, view):
        super().__init__(parent, background=ROOM)
        self.theme, self.view, self.top, self.size = theme, view, 0, (1600, 900)
        px = theme.px
        self.row_height, self.gap = px(150), px(14)
        self.canvas = tk.Canvas(self, background=ROOM, highlightthickness=0)
        self.scroll = ttk.Scrollbar(self, orient="vertical", command=self.yview, style="Keeper.Vertical.TScrollbar")
        self.scroll.pack(side="right", fill="y", padx=(px(10), 0))
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda event: self.draw())
        self.canvas.bind("<MouseWheel>", lambda event: self.scroll_by(-event.delta / 120 * self.row_height))
        self.canvas.bind("<Button-1>", self.click)

    def viewport(self):
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        return (width, height) if width > 1 and height > 1 else self.size

    def total(self):
        return len(self.view.shown) * self.row_height

    def scroll_by(self, amount):
        height = self.viewport()[1]
        self.top = int(max(0, min(self.top + amount, self.total() - height)))
        self.draw()

    # WHAT IT DOES: The scrollbar's moveto / scroll commands.
    def yview(self, *args):
        height = self.viewport()[1]
        if args and args[0] == "moveto":
            self.top = int(float(args[1]) * self.total())
            self.scroll_by(0)
        elif args and args[0] == "scroll":
            step = height if args[2] == "pages" else self.row_height
            self.scroll_by(int(args[1]) * step)

    # WHAT IT DOES: Remembers which row is at the top of the screen and how far it is scrolled, and puts it
    # back there after the order changes. A row that is no longer listed leaves the scroll where it was.
    def anchor(self):
        number = int(self.top // self.row_height)
        if 0 <= number < len(self.view.shown):
            return self.view.shown[number]["path"], self.top - number * self.row_height
        return None

    def restore(self, anchor):
        for number, entry in enumerate(self.view.shown):
            if entry["path"] == anchor[0]:
                self.top = number * self.row_height + anchor[1]
                return

    def click(self, event):
        number = int((self.top + event.y) // self.row_height)
        if 0 <= number < len(self.view.shown):
            self.view.open_review(self.view.shown[number])

    # WHAT IT DOES: Redraws the rows on screen (plus one row either side) and updates the scrollbar.
    def draw(self):
        canvas, view, t, px = self.canvas, self.view, self.theme, self.theme.px
        width, height = self.viewport()
        canvas.delete("all")
        total = self.total()
        self.top = int(max(0, min(self.top, total - height)))
        self.scroll.set(*((self.top / total, min(1.0, (self.top + height) / total)) if total else (0, 1)))
        if not view.shown:
            message = view.status or ("No sessions match this search." if view.entries else "")
            canvas.create_text(width // 2, px(90), text=message, font=t.font("body", 22), fill=DIM,
                               width=width - px(80), justify="center")
            return
        first = max(0, self.top // self.row_height)
        last = min(len(view.shown), (self.top + height) // self.row_height + 1)
        rows = view.shown[first:last]
        view.prepare(rows)
        for number, entry in enumerate(rows, start=first):
            self.draw_row(entry, number * self.row_height - self.top, width)

    def draw_row(self, entry, y, width):
        canvas, view, t, px = self.canvas, self.view, self.theme, self.theme.px
        facts = view.cache.get(entry["path"])
        text = row_text(entry, facts)
        chosen = view.selected == entry["path"]
        canvas.create_polygon(rounded(px(2), y + px(2), width - px(2), y + self.row_height - self.gap, px(12)),
                              smooth=True, fill=BEZEL, outline=INK if chosen else EDGE, width=px(3 if chosen else 2))
        pad, line1, line2, line3 = px(28), y + px(38), y + px(80), y + px(114)
        if entry["kind"] == "claude":
            canvas.create_text(pad, line1, text=CLAUDE_MARK, anchor="w", font=t.font("symbol", 26), fill=CLAUDE)
        else:
            canvas.create_oval(pad + px(2), line1 - px(10), pad + px(22), line1 + px(10), outline=INK, width=px(3))
        right = width - pad
        when_font = t.font("body", 20)
        stamp = canvas.create_text(right, line1, text="Last active " + text["last_active"], anchor="e",
                                   font=when_font, fill=INK)
        right = canvas.bbox(stamp)[0] - px(24)
        if entry["session_id"] in view.live:
            pill_font = t.font("display", 19)
            left = right - pill_font.measure("Running now") - px(32)
            canvas.create_polygon(rounded(left, line1 - px(17), right, line1 + px(17), px(16)), smooth=True,
                                  fill=GO, outline=GO)
            canvas.create_text(left + px(16), line1, text="Running now", anchor="w", font=pill_font, fill=BLACK)
            right = left - px(20)
        title_font = t.font("body", 24, bold=True)
        left = pad + px(44)
        canvas.create_text(left, line1, text=fit(title_font, text["title"], right - left), anchor="w",
                           font=title_font, fill=INK)
        mono = t.font("mono", 18)
        canvas.create_text(left, line2, text=fit(mono, text["folder"], width - pad - left), anchor="w", font=mono,
                           fill=DIM)
        program = "Claude Code" if entry["kind"] == "claude" else "Codex"
        parts = [program] + [f"Started {text['started']}"] * bool(text["started"])
        parts += [f"Active for {text['active_for']}"] * bool(text["active_for"]) + [text["size"], text["work"]]
        body = t.font("body", 19)
        failed = facts is not None and not facts.get("available")
        canvas.create_text(left, line3, text=fit(body, "  ·  ".join(parts), width - pad - left), anchor="w",
                           font=body, fill=STOP if failed else DIM)


# WHAT IT DOES: Shortens text with "…" so it fits a pixel width in the given font.
def fit(font, text, width):
    if width <= 0:
        return ""
    if font.measure(text) <= width:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if font.measure(text[:middle] + "…") <= width:
            low = middle
        else:
            high = middle - 1
    return text[:low] + "…"
