# Last modified: 2026-10-21 | Change: Film-strip time scrubber over the Saved layout chooser entries | Full history: git log
"""The time scrubber. Its stops ARE the chooser entries (keeper_chooser.chooser_entries), oldest on the left.
Picking a stop only asks the recovery window to show that saved layout; it never launches anything."""

import re
import tkinter as tk

from keeper_chooser import captured, clock
from keeper_theme import DIM, EDGE, BEZEL, INK, ROOM, STEEL, STOP, mix, reduced_motion, rounded

LEFT, RIGHT = 0.05, 0.975          # stop positions along the strip, as fractions of its width
SPLICE_AT = 0.3                    # reboot mark sits this far from the older stop toward the newer one

SHORT = ((r"^Latest\b", "Latest"), (r"^15 min ago", "15 min"), (r"^1 hour ago", "1 hr"),
         (r"^3 hours ago", "3 hr"), (r"^Before last reboot", "Before reboot"), (r"^Today\b", "Start of today"),
         (r"^Yesterday\b", "Yesterday"), (r"^Old prototype", "Prototype"))


# WHAT IT DOES: Turns a chooser label into the few words shown under its stop.
# DEPENDS ON: keeper_chooser label wording.
# AFFECTS: Display only. Unknown labels lose their bracketed time and clock.
def short_label(full):
    for pattern, short in SHORT:
        if re.search(pattern, full):
            return short
    day = re.match(r"^(\w{3} \w{3} \d{2})", full)
    return day.group(1) if day else full.split(" (")[0]


# WHAT IT DOES: One stop per chooser entry, sorted oldest to newest and spread evenly across the strip.
# DEPENDS ON: history (newest first, as read_history returns) and chooser_entries(history).
# AFFECTS: Returns [{index, label, full, time, boot, x}]; an entry that does not exist has no stop.
def scrubber_stops(history, entries):
    stops = []
    for full, index in entries:
        snapshot = history[index][1]
        stops.append({"index": index, "full": full, "label": short_label(full), "time": captured(snapshot),
                      "boot": snapshot.get("boot_id")})
    stops.sort(key=lambda stop: (stop["time"] is not None, stop["time"] or 0, -stop["index"]))
    count = len(stops)
    for number, stop in enumerate(stops):
        stop["x"] = RIGHT if count == 1 else LEFT + number * (RIGHT - LEFT) / (count - 1)
        if count <= 2 and stop["time"] is not None:     # few stops: say exactly when each was saved
            stop["label"] = f"{stop['label']} · {clock(stop['time'])}"
    return stops


# WHAT IT DOES: A quiet line inside an otherwise empty strip, so one stop does not look broken.
# DEPENDS ON: Stops from scrubber_stops.
# AFFECTS: Display only.
def strip_note(stops):
    return "Only one saved layout so far" if len(stops) == 1 else ""


# WHAT IT DOES: Positions of red reboot splices: between neighbouring stops saved under different boots.
# DEPENDS ON: Stops from scrubber_stops.
# AFFECTS: Display only.
def reboot_marks(stops):
    marks = []
    for older, newer in zip(stops, stops[1:]):
        if older["boot"] and newer["boot"] and older["boot"] != newer["boot"]:
            marks.append(older["x"] + (newer["x"] - older["x"]) * SPLICE_AT)
    return marks


# WHAT IT DOES: One small tick per loaded snapshot, placed by time between the stops around it.
# DEPENDS ON: Stops with times; history (any order).
# AFFECTS: Display only; snapshots outside the stops' time span get no tick.
def tick_positions(history, stops):
    timed = [stop for stop in stops if stop["time"] is not None]
    ticks = []
    if len(timed) < 2:
        return ticks
    for _, snapshot in history:
        moment = captured(snapshot)
        if moment is None or not timed[0]["time"] <= moment <= timed[-1]["time"]:
            continue
        for older, newer in zip(timed, timed[1:]):
            if older["time"] <= moment <= newer["time"]:
                span = (newer["time"] - older["time"]).total_seconds() or 1
                part = (moment - older["time"]).total_seconds() / span
                ticks.append(older["x"] + (newer["x"] - older["x"]) * part)
                break
    return ticks


# WHAT IT DOES: Number of the stop closest to a position along the strip (0..1).
# DEPENDS ON: Stops with x.
# AFFECTS: Nothing.
def nearest_stop(stops, fraction):
    return min(range(len(stops)), key=lambda number: abs(stops[number]["x"] - fraction))


# WHAT IT DOES: Draws the film strip, its stops and labels, and lets the user click, drag or arrow to a stop.
# DEPENDS ON: Theme; on_pick(stop_number) callback owned by the recovery window.
# AFFECTS: Canvas items only. on_pick fires once per release on a different stop.
class FilmStrip(tk.Canvas):
    def __init__(self, parent, theme, on_pick):
        self.theme, self.on_pick = theme, on_pick
        super().__init__(parent, height=theme.px(134), background=ROOM, highlightthickness=0, takefocus=1)
        self.stops, self.ticks, self.marks = [], [], []
        self.current, self.enabled, self.dragging, self.knob_x, self.glide = 0, True, False, None, None
        self.bind("<Configure>", lambda event: self.draw())
        self.bind("<ButtonPress-1>", self.press)
        self.bind("<B1-Motion>", self.drag)
        self.bind("<ButtonRelease-1>", self.release)
        for key, step in (("<Left>", -1), ("<Right>", 1), ("<Down>", -1), ("<Up>", 1)):
            self.bind(key, lambda event, s=step: self.step(s))
        self.bind("<Home>", lambda event: self.pick(0))
        self.bind("<End>", lambda event: self.pick(len(self.stops) - 1))

    # WHAT IT DOES: Shows a new set of stops with `current` highlighted.
    def set_stops(self, stops, ticks, marks, current):
        self.stops, self.ticks, self.marks, self.current = stops, ticks, marks, current
        self.knob_x = None
        self.draw()

    # WHAT IT DOES: Moves the highlight to a stop, gliding the knob over 4 frames unless motion is reduced.
    def set_current(self, number):
        if not self.stops:
            return
        start = self.knob_x if self.knob_x is not None else self.stop_x(self.current)
        self.current = number
        target = self.stop_x(number)
        if self.glide:
            self.after_cancel(self.glide)
            self.glide = None
        if reduced_motion() or not self.winfo_ismapped():
            self.knob_x = None
            self.draw()
            return
        self.animate(start, target, 1)

    def animate(self, start, target, frame):
        self.knob_x = start + (target - start) * frame / 4
        self.draw()
        if frame < 4:
            self.glide = self.after(45, lambda: self.animate(start, target, frame + 1))
        else:
            self.glide, self.knob_x = None, None

    # WHAT IT DOES: Greys the strip out (On screen now) or brings it back.
    def set_enabled(self, enabled):
        self.enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self.draw()

    def span(self):
        pad = self.theme.px(2)
        return pad, max(self.winfo_width() - pad, pad + 10)

    def stop_x(self, number):
        left, right = self.span()
        return left + self.stops[number]["x"] * (right - left)

    def colour(self, value):
        return value if self.enabled else mix(value, ROOM, 0.6)

    # WHAT IT DOES: Redraws everything from the current state.
    def draw(self):
        self.delete("all")
        px, c = self.theme.px, self.colour
        left, right = self.span()
        top, bottom = px(14), px(14 + 76)
        self.create_polygon(rounded(left, top, right, bottom, px(8)), smooth=True, fill=c(BEZEL),
                            outline=c(EDGE), width=px(2))
        x = left + px(14)
        while x < right - px(20):
            for y in (top + px(6), bottom - px(16)):
                self.create_polygon(rounded(x, y, x + px(16), y + px(10), px(3)), smooth=True, fill=c(ROOM))
            x += px(34)
        for tick in self.ticks:
            tx = left + tick * (right - left)
            self.create_rectangle(tx, top + px(26), tx + px(2), top + px(46), fill=c(STEEL), width=0)
        for mark in self.marks:
            mx = left + mark * (right - left)
            for edge in (mx - px(4), mx + px(4)):
                self.create_rectangle(edge, top + px(2), edge + px(2), bottom - px(2), fill=c(STOP), width=0)
        font = self.theme.font("display", 22)
        note = strip_note(self.stops)
        if note:
            self.create_text((left + right) / 2, (top + bottom) / 2, text=note, font=self.theme.font("body", 20),
                             fill=c(DIM))
        for number, stop in enumerate(self.stops):
            sx = self.stop_x(number)
            self.create_rectangle(sx - px(1), top + px(20), sx + px(2), top + px(52), fill=c(DIM), width=0)
            current = number == self.current
            tag = f"stop:{number}"
            anchor = "ne" if sx > right - px(60) else ("nw" if sx < left + px(60) else "n")
            item = self.create_text(sx, bottom + px(10), text=stop["label"], anchor=anchor, font=font,
                                    fill=c(INK if current else DIM), tags=(tag,))
            if current:
                x1, _, x2, y2 = self.bbox(item)
                self.create_rectangle(x1 + px(2), y2 + px(1), x2 - px(2), y2 + px(4), fill=c(INK), width=0)
            self.tag_bind(tag, "<ButtonRelease-1>", lambda event, n=number: self.pick(n))
        if self.stops:
            kx = self.knob_x if self.knob_x is not None else self.stop_x(self.current)
            self.create_polygon(rounded(kx - px(15), top - px(12), kx + px(15), top + px(84), px(7)), smooth=True,
                                fill=c(INK), tags=("knob",))
            self.create_rectangle(kx - px(2), top + px(6), kx + px(2), top + px(66), fill=c(ROOM), width=0,
                                  tags=("knob",))

    def fraction(self, x):
        left, right = self.span()
        return min(max((x - left) / (right - left), 0.0), 1.0)

    def press(self, event):
        self.focus_set()
        if not self.enabled or not self.stops or event.y > self.theme.px(14 + 76 + 4):
            return
        self.dragging = True
        self.drag(event)

    def drag(self, event):
        if self.dragging:
            left, right = self.span()
            self.knob_x = min(max(event.x, self.stop_x(0)), self.stop_x(len(self.stops) - 1))
            self.draw()

    def release(self, event):
        if not self.dragging:
            return
        self.dragging = False
        self.knob_x = None
        self.pick(nearest_stop(self.stops, self.fraction(event.x)))

    def step(self, direction):
        if self.stops:
            self.pick(min(max(self.current + direction, 0), len(self.stops) - 1))

    # WHAT IT DOES: Picks a stop: highlights it and tells the recovery window (only if it changed).
    def pick(self, number):
        if not self.enabled or not self.stops:
            return
        changed = number != self.current
        if changed:
            self.set_current(number)
            self.on_pick(number)
        else:
            self.draw()
