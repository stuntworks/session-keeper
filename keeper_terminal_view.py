# Last modified: 2026-10-22 | Change: Monitors drawn as real monitors: bezel, recessed desktop, chin, stand | Full history: git log
"""Canvas drawing for the recovery map: monitor bezels with tinted screens and taskbars, Windows Terminal
windows and their tabs. Display only: the map (keeper_picker) owns state and clicks; this module only paints."""

from keeper_theme import (AMBER, BEZEL, BEZEL_LIT, BLACK, CHIN, CLAUDE, CLAUDE_MARK, DIM, EDGE, GO, IC_ADD,
                          IC_CHECK, IC_CLOSE, IC_DOWN, IC_MAX, IC_MIN, IC_TERMINAL, INK, MONITOR_EDGE, OPEN_FILL, OUTLINE, POWER,
                          RECESS, SHADOW, STEEL, STRIP, TASKBAR, TERM, WALL_CEIL, mix, rounded)

STRIP_H, TAB_H, TAB_GAP = 50, 38, 6         # design pixels, as in the mockup
MIN_NAME_CHARS = 6                            # a squeezed tab still shows 6 letters of its name
TOOLS_W, CONTROLS_W = 56, 108                 # "+ ⌄" and min/max/close on the tab strip
TAB_STYLE = {  # state: (fill, outline, text, dash)
    "live": (OPEN_FILL, STEEL, INK, None),
    "missing": (None, AMBER, INK, (5, 3)),
    "selected": (GO, GO, BLACK, None),
    "unknown": (None, STEEL, INK, (2, 3)),
}


# WHAT IT DOES: Paints map pieces on one canvas with one theme.
# DEPENDS ON: A tk.Canvas and keeper_theme.Theme.
# AFFECTS: Canvas items only.
class Painter:
    def __init__(self, canvas, theme):
        self.canvas, self.theme = canvas, theme
        self.px = theme.px

    def fonts(self):
        return (self.theme.font("body", 19), self.theme.font("icons", 15),
                self.theme.font("icons", 11), self.theme.font("symbol", 19))

    # WHAT IT DOES: Width from a tab's left edge to where its name starts: [dot] [glyph] agent [check].
    # Compact tabs (space is tight) drop the terminal glyph; the state dot and agent mark stay.
    # Shared by tab_width and draw_tab so the size the layout plans is the size that gets drawn.
    def lead(self, info, compact=False):
        name_font, icon_font, x_font, symbol = self.fonts()
        px = self.px
        width = px(9) + (px(16) if info["state"] == "missing" else 0)
        if not compact:
            width += icon_font.measure(IC_TERMINAL) + px(6)
        if info.get("kind") == "codex":
            width += px(20)
        elif info.get("kind") == "claude":
            width += symbol.measure(CLAUDE_MARK) + px(5)
        elif info["state"] == "unknown":
            width += symbol.measure("?") + px(5)
        if info["state"] == "selected":
            width += icon_font.measure(IC_CHECK) + px(6)
        return width

    # WHAT IT DOES: Space after the name: the close cross, or just padding on a compact tab.
    def tail(self, compact=False):
        return self.px(12) if compact else self.px(18) + self.fonts()[2].measure(IC_CLOSE)

    # WHAT IT DOES: Width a tab needs to show its full name.
    def tab_width(self, info, compact=False):
        return self.lead(info, compact) + self.fonts()[0].measure(info["label"]) + self.tail(compact)

    # WHAT IT DOES: The narrowest a tab is ever squeezed to: its marks plus MIN_NAME_CHARS letters and "…".
    def floor_width(self, info, compact=False):
        name = info["label"] if len(info["label"]) <= MIN_NAME_CHARS else info["label"][:MIN_NAME_CHARS] + "…"
        return min(self.tab_width(info, compact),
                   self.lead(info, compact) + self.fonts()[0].measure(name) + self.tail(compact))

    # WHAT IT DOES: The "+3 · 1 selected" overflow label for the tabs not shown; selected ones show in green.
    def more_parts(self, hidden):
        if not hidden:
            return []
        parts = [(f"+{len(hidden)}", INK)]
        selected = sum(1 for info in hidden if info["state"] == "selected")
        return parts + ([(f" · {selected} selected", GO)] if selected else [])

    def more_width(self, hidden):
        return sum(self.fonts()[0].measure(text) for text, _ in self.more_parts(hidden)) + self.px(24)

    # WHAT IT DOES: Fits ONE row of tabs (normal or compact) into `room` pixels: natural widths when they fit,
    # otherwise the longest names shrink first (equal share, short names keep their full width); if even
    # squeezed tabs do not fit, only the first ones show and the rest go to the "+N" list.
    # AFFECTS: Returns ([widths of the tabs shown, in order], number not shown).
    def fit_row(self, infos, room, compact):
        if not infos:
            return [], 0
        gap = self.px(TAB_GAP)
        natural = [self.tab_width(info, compact) for info in infos]
        if sum(natural) + gap * (len(infos) - 1) <= room:
            return natural, 0
        floors = [self.floor_width(info, compact) for info in infos]
        count = len(infos)

        def budget(shown):
            hidden = infos[shown:]
            return room - gap * (shown - 1) - (self.more_width(hidden) + gap if hidden else 0)

        while count > 1 and sum(floors[:count]) > budget(count):
            count -= 1
        widths = list(floors[:count])
        spare = budget(count) - sum(widths)
        growing = [n for n in range(count) if natural[n] > widths[n]]
        while spare > 0.5 and growing:
            share = spare / len(growing)
            for n in list(growing):
                step = min(share, natural[n] - widths[n])
                widths[n] += step
                spare -= step
                if natural[n] - widths[n] < 0.5:
                    growing.remove(n)
        return widths, len(infos) - count

    # WHAT IT DOES: Normal tabs when they all fit at full width, otherwise compact tabs.
    # AFFECTS: Returns (widths, number not shown, compact).
    def row_layout(self, infos, room):
        widths, more = self.fit_row(infos, room, compact=False)
        if widths == [self.tab_width(info) for info in infos]:
            return widths, more, False
        return self.fit_row(infos, room, compact=True) + (True,)

    def row_widths(self, infos, room):
        return self.row_layout(infos, room)[:2]

    def tab_room(self, width):
        return width - self.px(8 + TOOLS_W + CONTROLS_W)

    # WHAT IT DOES: Draws one tab and returns its rectangle; every item carries `tag` for clicks and hover.
    def draw_tab(self, x, y, info, tag, width, compact=False):
        canvas, px = self.canvas, self.px
        name_font, icon_font, x_font, symbol = self.fonts()
        height = px(TAB_H)
        fill, line, text, dash = TAB_STYLE[info["state"]]
        if info.get("inspected"):
            canvas.create_polygon(rounded(x - px(4), y - px(4), x + width + px(4), y + height + px(4), px(10)),
                                  smooth=True, fill="", outline=INK, width=px(3), tags=(tag,))
        canvas.create_polygon(rounded(x, y, x + width, y + height, px(7)), smooth=True, fill=fill or STRIP,
                              outline=line, width=px(2), dash=dash, tags=(tag,))
        middle = y + height / 2
        cx = x + px(9)
        if info["state"] == "missing":
            canvas.create_oval(cx, middle - px(5), cx + px(10), middle + px(5), fill=AMBER, width=0, tags=(tag,))
            cx += px(16)
        if not compact:
            canvas.create_text(cx, middle, text=IC_TERMINAL, anchor="w", font=icon_font,
                               fill=BLACK if info["state"] == "selected" else DIM, tags=(tag,))
            cx += icon_font.measure(IC_TERMINAL) + px(6)
        self.agent_mark(cx, middle, info, tag, symbol)
        lead = self.lead(info, compact)
        if info["state"] == "selected":
            check_x = x + lead - icon_font.measure(IC_CHECK) - px(6)
            canvas.create_text(check_x, middle, text=IC_CHECK, anchor="w", font=icon_font, fill=BLACK, tags=(tag,))
        room = width - lead - self.tail(compact) + px(4)
        canvas.create_text(x + lead, middle, text=fit_text(name_font, info["label"], room), anchor="w",
                           font=name_font, fill=text, tags=(tag,))
        if not compact:
            canvas.create_text(x + width - px(9), middle, text=IC_CLOSE, anchor="e", font=x_font,
                               fill=BLACK if info["state"] == "selected" else DIM, tags=(tag,))
        return x, y, width, height

    # WHAT IT DOES: Claude's orange asterisk, a ring for Codex, nothing for an unidentified tab.
    def agent_mark(self, cx, middle, info, tag, symbol):
        canvas, px = self.canvas, self.px
        selected = info["state"] == "selected"
        if info.get("kind") == "codex":
            colour = BLACK if selected else (STEEL if info["state"] == "live" else DIM)
            canvas.create_oval(cx, middle - px(7), cx + px(14), middle + px(7), outline=colour, width=px(2.5),
                               tags=(tag,))
            return cx + px(14) + px(6)
        if info.get("kind") == "claude":
            item = canvas.create_text(cx, middle, text=CLAUDE_MARK, anchor="w", font=symbol,
                                      fill=BLACK if selected else CLAUDE, tags=(tag,))
            return canvas.bbox(item)[2] + px(5)
        if info["state"] == "unknown":
            item = canvas.create_text(cx, middle, text="?", anchor="w", font=symbol, fill=INK, tags=(tag,))
            return canvas.bbox(item)[2] + px(5)
        return cx

    # WHAT IT DOES: Draws one monitor like the real thing: a shadow on the desk, a rounded bezel with a lit top
    # edge, a recessed screen holding the desktop (the owner's dimmed wallpaper, a solid colour, or a tinted
    # wallpaper-like gradient), the taskbar strip, a darker chin with the name and a power light, and a stand
    # (neck and foot) centred under it.
    # DEPENDS ON: One screen entry from keeper_map_layout.exact_layout; desktop = None, "#rrggbb" or PhotoImage.
    # AFFECTS: Canvas items only (the frame is tagged "bezel", the foot "stand"); returns the lowest y drawn.
    def draw_bezel(self, place, name, tint, desktop=None):
        canvas, px = self.canvas, self.px
        bezel, screen, bar = place["bezel"], place["screen"], place["bar"]
        x1, y1 = bezel["x"], bezel["y"]
        x2, y2 = x1 + bezel["w"], y1 + bezel["h"]
        radius = px(16)
        canvas.create_polygon(rounded(x1 + px(4), y1 + px(10), x2 + px(4), y2 + px(10), radius), smooth=True,
                              fill=SHADOW, outline="")
        bottom = self.draw_stand(place.get("stand") or {"x": x1, "y": y2, "w": bezel["w"], "h": px(40)})
        canvas.create_polygon(rounded(x1, y1, x2, y2, radius), smooth=True, fill=BEZEL, outline=MONITOR_EDGE,
                              width=px(3), tags=("bezel",))
        chin_top = bar["y"] + bar["h"] + px(6)
        canvas.create_polygon(rounded(x1 + px(2), chin_top, x2 - px(2), y2 - px(2), radius - px(2)), smooth=True,
                              fill=CHIN, outline="")
        canvas.create_line(x1 + radius, y1 + px(2), x2 - radius, y1 + px(2), fill=BEZEL_LIT, width=px(2))
        self.draw_desktop(screen, tint, desktop)
        canvas.create_rectangle(bar["x"], bar["y"], bar["x"] + bar["w"], bar["y"] + bar["h"], fill=TASKBAR,
                                width=0)
        edge = px(3)
        sx1, sy1 = screen["x"] - edge / 2, screen["y"] - edge / 2
        sx2, sy2 = screen["x"] + screen["w"] + edge / 2, bar["y"] + bar["h"] + edge / 2
        canvas.create_rectangle(sx1, sy1, sx2, sy2, outline=RECESS, width=edge)
        canvas.create_line(sx1 - edge / 2, sy2 + edge, sx2 + edge / 2, sy2 + edge, fill=EDGE, width=max(px(1), 1))
        middle, chin = (x1 + x2) / 2, (chin_top + y2) / 2
        font = self.theme.font("display", 21)
        canvas.create_text(middle, chin, text=fit_text(font, name, bezel["w"] - px(90)), font=font, fill=INK)
        dot = px(4)
        canvas.create_oval(x2 - px(30) - dot, chin - dot, x2 - px(30) + dot, chin + dot, fill=POWER, width=0)
        return bottom

    # WHAT IT DOES: The screen's desktop: a picture (the owner's wallpaper or keeper_desktop_look's tinted one),
    # a solid colour, or, without Pillow, a gentle tinted gradient, so an empty screen reads as a desktop.
    # DEPENDS ON: Every colour stays at or under WALL_CEIL, the brightness the contrast tests check.
    def draw_desktop(self, screen, tint, desktop):
        canvas = self.canvas
        x, y, w, h = screen["x"], screen["y"], screen["w"], screen["h"]
        if isinstance(desktop, str):
            canvas.create_rectangle(x, y, x + w, y + h, fill=desktop, width=0)
            return
        if desktop is not None:
            canvas.create_image(x, y, image=desktop, anchor="nw")
            return
        bands = 28
        top, low = mix(tint, WALL_CEIL, 0.30), mix(tint, BLACK, 0.30)
        for step in range(bands):
            canvas.create_rectangle(x, y + h * step / bands, x + w, y + h * (step + 1) / bands + 1,
                                    fill=mix(top, low, step / (bands - 1)), width=0)

    # WHAT IT DOES: The monitor stand: a tapered neck out of the chin and a wide flat foot, both centred.
    # DEPENDS ON: The stand area exact_layout reserved under the bezel (width = bezel width).
    # AFFECTS: Canvas items only; returns the lowest y drawn.
    def draw_stand(self, stand):
        canvas, px = self.canvas, self.px
        middle, top, height = stand["x"] + stand["w"] / 2, stand["y"], max(stand["h"], px(24))
        neck_top = max(px(26), min(stand["w"] * 0.10, px(80))) / 2
        neck_low = neck_top * 1.35
        foot = max(px(110), min(stand["w"] * 0.40, px(460))) / 2
        foot_top, bottom = top + height * 0.66, top + height
        canvas.create_oval(middle - foot - px(10), bottom - px(6), middle + foot + px(10), bottom + px(8),
                           fill=SHADOW, width=0)
        canvas.create_polygon(middle - neck_top, top - px(2), middle + neck_top, top - px(2), middle + neck_low,
                              foot_top + px(4), middle - neck_low, foot_top + px(4), fill=CHIN, outline=MONITOR_EDGE,
                              width=px(2))
        canvas.create_polygon(rounded(middle - foot, foot_top, middle + foot, bottom, px(8)), smooth=True,
                              fill=BEZEL, outline=MONITOR_EDGE, width=px(2), tags=("stand",))
        canvas.create_line(middle - foot + px(10), foot_top + px(2), middle + foot - px(10), foot_top + px(2),
                           fill=BEZEL_LIT, width=max(px(1), 1))
        return bottom + px(8)

    # WHAT IT DOES: Draws one Windows Terminal window at its exact box: one row of tabs, a black body with its
    # prompt line and footer chip when there is room, and a burgundy outline (dashed for a window whose
    # saved spot no longer has a live window).
    # DEPENDS ON: infos (one per tab, saved order), keys, a footer [(text, colour)], and the prompt text.
    # AFFECTS: Returns {key: rect} of the tabs drawn. Clicking the strip's empty space uses title_tag.
    def draw_window(self, box, infos, keys, title_tag, footer, prompt, ghost=False):
        canvas, px = self.canvas, self.px
        x, y, w, h = box["x"], box["y"], box["w"], box["h"]
        strip = min(px(STRIP_H), h)
        canvas.create_rectangle(x, y, x + w, y + h, fill=TERM, width=0, tags=(title_tag,))
        canvas.create_rectangle(x, y, x + w, y + strip, fill=STRIP, width=0, tags=(title_tag,))
        rects = {}
        widths, more, compact = self.row_layout(infos, self.tab_room(w))
        top = y + (strip - px(TAB_H)) / 2
        cx = x + px(8)
        for width, info, key in zip(widths, infos, keys):
            rects[key] = self.draw_tab(cx, top, info, f"tab:{key}", width, compact)
            cx += width + px(TAB_GAP)
        middle = top + px(TAB_H) / 2
        # "+N" and the dropdown arrow open the list of tabs that did not fit (not the whole-window toggle).
        menu_tags = (f"more-{title_tag}",) if more else (title_tag,)
        if more:
            hidden = infos[len(widths):]
            canvas.create_rectangle(cx, top, cx + self.more_width(hidden) + px(56), top + px(TAB_H), fill=STRIP,
                                    width=0, tags=menu_tags)     # one solid click target for "+N ⌄"
            tx = cx + px(4)
            for text, colour in self.more_parts(hidden):
                canvas.create_text(tx, middle, text=text, anchor="w", font=self.fonts()[0], fill=colour,
                                   tags=menu_tags)
                tx += self.fonts()[0].measure(text)
            cx += self.more_width(hidden)
        icons = self.theme.font("icons", 13)
        canvas.create_text(cx + px(14), middle, text=IC_ADD, font=icons, fill=DIM, tags=(title_tag,))
        canvas.create_text(cx + px(40), middle, text=IC_DOWN, font=icons, fill=INK if more else DIM, tags=menu_tags)
        controls = self.theme.font("icons", 12)
        for number, glyph in enumerate((IC_CLOSE, IC_MAX, IC_MIN)):
            canvas.create_text(x + w - px(22 + 34 * number), y + strip / 2, text=glyph, font=controls,
                               fill=DIM, tags=(title_tag,))
        mono = self.theme.font("mono", 17)
        body_top = y + strip + px(14)
        if prompt and h > strip + px(48):
            text = canvas.create_text(x + px(18), body_top, anchor="nw", text=fit_text(mono, prompt, w - px(56)),
                                      font=mono, fill=INK)
            end = canvas.bbox(text)[2]
            canvas.create_rectangle(end + px(3), body_top + px(4), end + px(13), body_top + px(22), fill=DIM,
                                    width=0)
        if h > strip + px(96):
            self.draw_footer(x + px(14), y + h - px(12), footer)
        line = max(px(3), 2)
        canvas.create_rectangle(x + line / 2, y + line / 2, x + w - line / 2, y + h - line / 2, outline=OUTLINE,
                                width=line, dash=(8, 5) if ghost else None, tags=(title_tag,))
        return rects

    # WHAT IT DOES: The "2 open · 1 missing" chip; parts are (text, colour).
    def draw_footer(self, x, bottom, parts):
        canvas, px = self.canvas, self.px
        font = self.theme.font("display", 19)
        pieces = []
        for number, (text, colour) in enumerate(parts):
            if number:
                pieces.append((" · ", DIM))
            pieces.append((text, colour))
        if not pieces:
            return x
        width = sum(font.measure(text) for text, _ in pieces) + px(28)
        top = bottom - px(34)
        canvas.create_polygon(rounded(x, top, x + width, bottom, px(17)), smooth=True, fill=BEZEL, outline=EDGE)
        cx = x + px(14)
        for text, colour in pieces:
            canvas.create_text(cx, top + px(17), text=text, anchor="w", font=font, fill=colour)
            cx += font.measure(text)
        return x + width

    # WHAT IT DOES: One monitor's taskbar: a Windows mark, then one button per hidden or minimised window,
    # each labelled with that window's full tab names on up to two lines.
    # DEPENDS ON: buttons = [(text, state, tag)]; state "missing", "selected" or "" colours the button's edge.
    # AFFECTS: Returns {tag: rect} for the buttons.
    def draw_taskbar(self, bar, buttons):
        canvas, px = self.canvas, self.px
        x, y, w, h = bar["x"], bar["y"], bar["w"], bar["h"]
        size, left = px(9), x + px(16)
        for dx in (0, size + px(2)):
            for dy in (0, size + px(2)):
                canvas.create_rectangle(left + dx, y + h / 2 - size - px(1) + dy, left + dx + size,
                                        y + h / 2 - px(1) + dy, fill=DIM, width=0)
        start, rects = left + 2 * size + px(22), {}
        if not buttons:
            return rects
        font = self.theme.font("body", 17)
        width = min(px(460), (x + w - px(8) - start) / len(buttons) - px(8))
        for number, (text, state, tag) in enumerate(buttons):
            bx = start + number * (width + px(8))
            line = {"missing": AMBER, "selected": GO}.get(state, STEEL)
            canvas.create_polygon(rounded(bx, y + px(4), bx + width, y + h - px(4), px(6)), smooth=True,
                                  fill=OPEN_FILL, outline=line, width=px(2), tags=(tag,))
            canvas.create_text(bx + px(10), y + h / 2, anchor="w", text="\n".join(two_lines(font, text,
                               width - px(20))), font=font, fill=INK, justify="left", tags=(tag,))
            rects[tag] = (bx, y, width, h)
        return rects


# WHAT IT DOES: Wraps text onto at most two lines of `width` pixels, ending with "…" if it is longer.
# DEPENDS ON: A tkinter font.
# AFFECTS: Display only; hovering the button shows the full text.
def two_lines(font, text, width):
    words, lines = text.split(), []
    while words and len(lines) < 2:
        line = words.pop(0)
        while words and font.measure(f"{line} {words[0]}") <= width:
            line = f"{line} {words.pop(0)}"
        lines.append(line)
    if words:
        lines[-1] = lines[-1] + " " + " ".join(words)
        lines[-1] = fit_text(font, lines[-1], width)
    return [fit_text(font, line, width) for line in lines] or [""]


# WHAT IT DOES: Shortens text with "…" until it fits a pixel width.
# DEPENDS ON: A tkinter font.
# AFFECTS: Display only.
def fit_text(font, text, width):
    if font.measure(text) <= width:
        return text
    while text and font.measure(text + "…") > width:
        text = text[:-1]
    return text.rstrip() + "…" if text else "…"
