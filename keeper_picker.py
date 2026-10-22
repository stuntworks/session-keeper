# Last modified: 2026-10-22 | Change: Exact map: real window spots, hidden/minimised windows on per-monitor taskbars | Full history: git log
"""The monitor map IS the menu. Display only: it never launches, moves or closes anything."""

import tkinter as tk
from tkinter import ttk

from keeper_chooser import captured, clock
from keeper_drawer import Drawer
from keeper_map_layout import exact_layout, split_windows
from keeper_tabdetails import tab_details
from keeper_tabmenu import draw_menu
from keeper_terminal_view import Painter
from keeper_theme import AMBER, BEZEL, DIM, EDGE, GO, INK, ROOM, TINTS, Theme
from keeper_zorder import hidden_windows, stack_order, taskbar_buttons

HINT = "Click a tab to see\nhow to bring it back"
TIP_SIZE = 30              # hover popovers: big enough to read from across the room


# WHAT IT DOES: Draws the saved layout as monitors holding Windows Terminal windows, and tracks selection.
# DEPENDS ON: A snapshot, recovery rows by key, the live session ID set, and key -> BooleanVar options.
# AFFECTS: Changes only the BooleanVars; the recovery window acts on them.
class SnapshotMap(tk.Frame):
    def __init__(self, parent, on_change=None, theme=None, on_restart=None):
        super().__init__(parent, background=ROOM)
        self.theme = theme or Theme(self.winfo_toplevel())
        self.on_change = on_change
        self.detail_key = None
        self.drawer = Drawer(self, self.theme, on_restart=on_restart, on_close=self.close_detail)
        self.drawer.dock = {"side": "right", "fill": "y", "padx": (self.theme.px(28), 0)}
        self.canvas = tk.Canvas(self, background=ROOM, highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        # Shown only if the layout ever ends up taller than the view (the exact map is scaled to fit).
        self.scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview,
                                    style="Keeper.Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.painter = Painter(self.canvas, self.theme)
        # The drawer's widgets, under the names the rest of the app (and its tests) use.
        self.command_box, self.copy_btn = self.drawer.command_box, self.drawer.copy_btn
        self.copy_status, self.note_label = self.drawer.copy_status, self.drawer.note_label
        self.snapshot, self.rows, self.live, self.options = {"windows": []}, {}, set(), {}
        self.tab_boxes, self.homes, self.redraw_job, self.size = {}, {}, None, (1400, 700)
        self.bar_boxes, self.hidden, self.overflow = {}, set(), {}
        self.menu_window, self.menu_rows, self.menu_hit = None, {}, False
        self.canvas.bind("<Button-1>", self.outside_click, add="+")
        self.winfo_toplevel().bind("<Escape>", lambda event: self.close_menu(), add="+")
        self.canvas.bind("<Configure>", self.on_resize)
        self.canvas.bind("<MouseWheel>", self.on_wheel)

    # WHAT IT DOES: Shows a (new) saved layout.
    # DEPENDS ON: Data owned by the recovery window.
    # AFFECTS: Redraws the map.
    def show(self, snapshot, rows, live, options):
        self.snapshot, self.rows, self.live, self.options = snapshot, rows, live, options
        self.detail_key = None
        self.fill_detail(None)
        self.draw()

    # WHAT IT DOES: Redraws after the window is resized, at most every 80 ms.
    def on_resize(self, event):
        if event.width > 50 and event.height > 50 and (event.width, event.height) != self.size:
            self.size = (event.width, event.height)
            if self.redraw_job:
                self.after_cancel(self.redraw_job)
            self.redraw_job = self.after(80, self.draw)

    def on_wheel(self, event):
        if self.canvas.bbox("all") and self.canvas.bbox("all")[3] > self.size[1]:
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    # WHAT IT DOES: Names a tab's state: live, missing, selected or unknown.
    # DEPENDS ON: Recovery rows (identified tabs only), live IDs and options.
    # AFFECTS: Colour and clickability of that tab.
    def tab_state(self, key):
        row = self.rows.get(key)
        if not row:
            return "unknown"
        if row["session_id"] in self.live:
            return "live"
        option = self.options.get(key)
        return "selected" if option is not None and option.get() else "missing"

    # WHAT IT DOES: Toggles one missing tab between selected and not selected.
    # DEPENDS ON: The tab being identified and not already open.
    # AFFECTS: One BooleanVar.
    def toggle(self, key):
        if self.tab_state(key) in ("live", "unknown"):
            return
        self.options[key].set(not self.options[key].get())
        self.changed()

    # WHAT IT DOES: Tab click: shows that tab's details; a missing/selected tab also toggles as before.
    # DEPENDS ON: toggle (selection rules unchanged), tab_details.
    # AFFECTS: Drawer, inspected-tab outline, and (missing tabs only) one BooleanVar.
    def select_tab(self, key):
        self.detail_key = key
        if self.tab_state(key) in ("missing", "selected"):
            self.toggle(key)
        else:
            self.draw()
        self.fill_detail(key)

    # WHAT IT DOES: Everything the drawer shows for one tab (same command text as Copy command).
    # DEPENDS ON: keeper_tabdetails.tab_details and where the tab sits on the map.
    # AFFECTS: Returns a dict only.
    def describe(self, key):
        info = tab_details(self.snapshot, key, self.rows, self.live)
        window_index, tab_index = (int(part) for part in key.split(":"))
        state = self.tab_state(key)
        home = self.homes.get(window_index)
        place = "Minimised window" if home is None else f"Monitor {home + 1} · window {window_index + 1}"
        moment = captured(self.snapshot)
        seen = "Running now" if state == "live" else (f"{moment:%b %d}, {clock(moment)}" if moment else "Unknown")
        row = self.rows.get(key)
        return {**info, "state": state, "kind": row["kind"] if row else None, "where": f"{place} · tab {tab_index + 1}",
                "seen": seen, "row": row, "can_restart": state in ("missing", "selected") and info["can_copy"]}

    # WHAT IT DOES: Fills the drawer for a tab, or shows the hint when key is None.
    def fill_detail(self, key):
        if key is None:
            self.drawer.show_hint(HINT)
            return
        self.drawer.show_detail(self.describe(key))

    # WHAT IT DOES: Copies exactly the command text shown, via the Tk clipboard.
    def copy_detail(self):
        self.drawer.copy_detail()

    @property
    def shown_command(self):
        return self.drawer.shown_command

    # WHAT IT DOES: Drawer closed: forget the inspected tab.
    def close_detail(self):
        self.detail_key = None
        self.drawer.show_hint(HINT)
        self.draw()

    # WHAT IT DOES: Title-strip click: selects every missing tab in that window, or unselects them if all were.
    # DEPENDS ON: Tab states of that window.
    # AFFECTS: BooleanVars of that window only.
    def toggle_window(self, window_index):
        keys = [key for key in self.window_keys(window_index) if self.tab_state(key) in ("missing", "selected")]
        if not keys:
            return
        target = not all(self.options[key].get() for key in keys)
        for key in keys:
            self.options[key].set(target)
        self.changed()

    # WHAT IT DOES: Lists the tab keys of one saved window in saved tab order.
    def window_keys(self, window_index):
        return [f"{window_index}:{tab}" for tab in range(len(self.snapshot["windows"][window_index].get("tabs", [])))]

    # WHAT IT DOES: Redraws and tells the recovery window the selection changed.
    def changed(self):
        self.draw()
        if self.detail_key is not None and self.drawer.key == self.detail_key:
            self.drawer.draw_pill(self.tab_state(self.detail_key))
        if self.on_change:
            self.on_change()

    # WHAT IT DOES: Text shown on one tab and in its tooltip.
    # DEPENDS ON: Saved tab title.
    # AFFECTS: Display only.
    def tab_names(self, key):
        window_index, tab_index = (int(part) for part in key.split(":"))
        tab = self.snapshot["windows"][window_index]["tabs"][tab_index]
        full = tab.get("strippedTitle") or tab.get("rawTitle") or "Unnamed tab"
        state = self.tab_state(key)
        label = ("? " if state == "unknown" else "") + full   # the tab itself shortens it to fit
        if state == "unknown" and tab.get("outcome") == "MATCHED" and not (tab.get("cwd") or "").strip():
            unknown_note = "project folder not recorded, cannot be restored"
        else:
            unknown_note = "not identified, cannot be restored"
        note = {"live": "already open", "unknown": unknown_note,
                "missing": "closed - click to bring back", "selected": "selected to bring back"}[state]
        return label, f"{full}  ({note})"

    # WHAT IT DOES: Per-tab drawing facts for one window.
    def infos(self, window_index):
        result = []
        for key in self.window_keys(window_index):
            row = self.rows.get(key)
            label = self.tab_names(key)[0]
            result.append({"label": label[2:] if label.startswith("? ") else label, "state": self.tab_state(key),
                           "kind": row["kind"] if row else None, "inspected": key == self.detail_key})
        return result

    # WHAT IT DOES: The footer chip parts for one window: open, missing, selected, not identified.
    def footer(self, window_index):
        states = [self.tab_state(key) for key in self.window_keys(window_index)]
        live, missing = states.count("live"), states.count("missing") + states.count("selected")
        selected, unknown = states.count("selected"), states.count("unknown")
        parts = [(f"{live} open", DIM)] if live else []
        parts += [(f"{missing} missing", AMBER)] if missing else []
        parts += [(f"{selected} selected", GO)] if selected else []
        parts += [(f"{unknown} not identified", DIM)] if unknown else []
        return parts

    # WHAT IT DOES: The last prompt line for a window: "PS <folder>> claude", from its first identified tab.
    def prompt(self, window_index, ghost, width):
        if ghost:
            return ""          # the footer chip already says how many are missing
        row = next((self.rows[key] for key in self.window_keys(window_index) if key in self.rows), None)
        if not row:
            return "PS>"
        folder, program = row.get("cwd") or "", row.get("kind") or ""
        text = f"PS {folder}> {program}"
        mono = self.theme.font("mono", 18)
        parts = folder.replace("/", "\\").split("\\")
        while mono.measure(text) > width - self.theme.px(70) and len(parts) > 1:
            parts = parts[1:]
            text = f"PS …\\{chr(92).join(parts)}> {program}"
        return text


    # WHAT IT DOES: Draws the monitors as they really sit: every front window at its exact saved spot (back
    # first, so overlaps look as they did), and a taskbar under each screen with a button for each window
    # that was hidden behind others or minimised. Hidden windows are not drawn on the screen.
    # DEPENDS ON: Current canvas size and data; keeper_zorder for front/back and hidden.
    # AFFECTS: Canvas items only.
    def draw(self):
        self.redraw_job = None
        canvas, px = self.canvas, self.theme.px
        canvas.delete("all")
        self.tab_boxes, self.bar_boxes, self.overflow, self.menu_rows = {}, {}, {}, {}
        windows = self.snapshot.get("windows", [])
        placed, minimised = split_windows(windows)
        width, height = self.size
        screens, boxes, self.homes, _ = exact_layout(self.snapshot.get("monitors"), windows, placed, width,
                                                     max(height - px(28), px(240)), gap=px(36), side=px(16),
                                                     top=px(16), bar=px(64), chin=px(40))
        self.hidden = hidden_windows(windows, placed)
        bars = taskbar_buttons(self.snapshot.get("monitors"), windows, self.hidden, minimised)
        names = self.monitor_names(screens)
        bottom = 0
        order = sorted(screens, key=lambda screen: (screen["bezel"]["x"], screen["bezel"]["y"]))
        for position, screen in enumerate(order):
            bottom = max(bottom, self.painter.draw_bezel(screen, names[screen["number"]],
                                                         TINTS[position % len(TINTS)]))
        for index in reversed(stack_order(windows)):
            if index in boxes and index not in self.hidden:
                self.draw_window(index, boxes[index])
        for screen in screens:
            self.draw_taskbar(screen["bar"], bars.get(screen["number"], []))
        if self.overflow.get(self.menu_window):
            self.draw_menu()
        else:
            self.menu_window = None
        canvas.configure(scrollregion=(0, 0, width, max(bottom + px(8), height)))
        if bottom + px(8) > height:
            self.scroll.pack(side="right", fill="y", before=canvas)
        else:
            self.scroll.pack_forget()
            canvas.yview_moveto(0)

    # WHAT IT DOES: "Monitor 1 · Primary" labels, numbered left to right.
    def monitor_names(self, screens):
        saved = self.snapshot.get("monitors") or []
        order = sorted(screens, key=lambda screen: (screen["bezel"]["x"], screen["bezel"]["y"]))
        names = {}
        for position, screen in enumerate(order):
            number = screen["number"]
            main = " · Primary" if number < len(saved) and saved[number].get("primary") else ""
            names[number] = f"Monitor {position + 1}{main}" if saved else "Saved screen"
        return names

    # WHAT IT DOES: Draws one saved window as a Windows Terminal window and binds its clicks.
    def draw_window(self, index, box):
        keys = self.window_keys(index)
        states = [self.tab_state(key) for key in keys]
        ghost = "live" not in states and any(state in ("missing", "selected") for state in states)
        title_tag = f"title:{index}"
        rects = self.painter.draw_window(box, self.infos(index), keys, title_tag, self.footer(index),
                                         self.prompt(index, ghost, box["w"]), ghost)
        self.canvas.tag_bind(title_tag, "<Button-1>", lambda event, i=index: self.toggle_window(i))
        for key, rect in rects.items():
            self.tab_boxes[key] = rect
            tag = f"tab:{key}"
            x, y, w, h = rect
            self.canvas.tag_bind(tag, "<Enter>", lambda event, k=key, px=x, py=y + h:
                                 self.show_tip(self.tab_names(k)[1], px, py))
            self.canvas.tag_bind(tag, "<Leave>", lambda event: self.canvas.delete("tip"))
            self.canvas.tag_bind(tag, "<Button-1>", lambda event, k=key: self.select_tab(k))
        self.overflow[index] = [key for key in keys if key not in rects]
        if self.overflow[index]:      # "+N ⌄" opens the list of tabs that did not fit the row
            more = f"more-{title_tag}"
            self.canvas.tag_bind(more, "<Button-1>", lambda event, i=index: self.press_more(i))

    # WHAT IT DOES: The full name on a hidden/minimised window's taskbar button: its tab titles joined.
    def window_title(self, index):
        names = [self.tab_names(key)[1].rsplit("  (", 1)[0] for key in self.window_keys(index)]
        return " · ".join(names) or "Empty window"

    # WHAT IT DOES: Taskbar buttons for one monitor; edge colour follows the window's tabs.
    def draw_taskbar(self, bar, indexes):
        buttons = []
        for index in indexes:
            states = [self.tab_state(key) for key in self.window_keys(index)]
            state = "selected" if "selected" in states else ("missing" if "missing" in states else "")
            buttons.append((self.window_title(index), state, f"bar:{index}"))
        rects = self.painter.draw_taskbar(bar, buttons)
        for index in indexes:
            tag = f"bar:{index}"
            x, y, w, h = rects[tag]
            where = "minimised" if self.homes.get(index) is None else "hidden behind other windows"
            tip = f"{self.window_title(index)}\n({where}; {', '.join(t for t, _ in self.footer(index))})"
            self.bar_boxes[index] = rects[tag]
            self.canvas.tag_bind(tag, "<Enter>", lambda event, t=tip, px=x, py=y: self.show_tip(t, px, py, above=True))
            self.canvas.tag_bind(tag, "<Leave>", lambda event: self.canvas.delete("tip"))
            self.canvas.tag_bind(tag, "<Button-1>", lambda event, i=index: self.click_button(i))

    # WHAT IT DOES: Taskbar button click: like the window's title strip (selects its missing tabs, or
    # unselects them if all were), then opens the details of its first missing tab (else its first tab).
    # DEPENDS ON: toggle_window, so the selection rules are the same ones.
    # AFFECTS: BooleanVars of that window and the drawer.
    def click_button(self, index):
        keys = self.window_keys(index)
        if not keys:
            return
        self.toggle_window(index)
        restorable = [key for key in keys if self.tab_state(key) in ("missing", "selected")]
        self.detail_key = (restorable or keys)[0]
        self.fill_detail(self.detail_key)
        self.draw()

    # WHAT IT DOES: A large popover with the full text near the pointer, kept inside the map.
    # AFFECTS: One temporary canvas item group.
    def show_tip(self, text, x, y, above=False):
        self.canvas.delete("tip")
        px = self.theme.px
        wrap = max(min(self.size[0] - px(60), px(1500)), px(300))
        item = self.canvas.create_text(0, 0, anchor="nw", tags=("tip",), fill=INK, width=wrap,
                                       font=self.theme.font("body", TIP_SIZE), text=text)
        left, top, right, bottom = self.canvas.bbox(item)
        tx = min(max(x, px(20)), self.size[0] - (right - left) - px(20))
        ty = y - (bottom - top) - px(18) if above else y + px(14)
        self.canvas.move(item, tx, max(ty, px(10)))
        left, top, right, bottom = self.canvas.bbox(item)
        back = self.canvas.create_rectangle(left - px(14), top - px(10), right + px(14), bottom + px(10),
                                            fill=BEZEL, outline=INK, width=px(2), tags=("tip",))
        self.canvas.tag_lower(back, item)

    # WHAT IT DOES: "+N ⌄" click: opens that window's overflow list, or closes it if it is already open.
    def press_more(self, index):
        self.menu_hit = True
        if self.menu_window == index:
            self.close_menu()
        else:
            self.open_menu(index)

    # WHAT IT DOES: Opens the dropdown listing every tab of this window that did not fit its row.
    # AFFECTS: Canvas items tagged "menu" only.
    def open_menu(self, index):
        self.canvas.delete("tip")
        self.menu_window = index
        self.draw_menu()

    def draw_menu(self):
        self.canvas.delete("menu")
        index = self.menu_window
        infos = dict(zip(self.window_keys(index), self.infos(index)))
        rows = [(key, dict(infos[key], label=self.tab_names(key)[1].rsplit("  (", 1)[0]))
                for key in self.overflow[index]]
        anchor = self.canvas.bbox(f"more-title:{index}") or (0, 0, 0, 0)
        self.menu_rows = draw_menu(self.painter, anchor, rows, *self.size)
        for key in self.menu_rows:
            self.canvas.tag_bind(f"menu-row:{key}", "<Button-1>", lambda event, k=key: self.click_row(k))

    # WHAT IT DOES: A row click does exactly what clicking that tab does (select_tab); the list stays open
    # so agents can be picked one by one.
    def click_row(self, key):
        self.menu_hit = True
        self.select_tab(key)

    def close_menu(self):
        self.menu_window, self.menu_rows = None, {}
        self.canvas.delete("menu")

    # WHAT IT DOES: A click anywhere outside the open list closes it (clicks on the list or "+N" do not).
    def outside_click(self, event):
        if self.menu_hit:
            self.menu_hit = False
            return
        if self.menu_window is None:
            return
        box = self.canvas.bbox("menu")
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        if not box or not (box[0] <= x <= box[2] and box[1] <= y <= box[3]):
            self.close_menu()
