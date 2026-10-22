# Last modified: 2026-10-22 | Change: Dropdown list of the tabs that do not fit a window's row on the map | Full history: git log
"""The "+N ⌄" dropdown on a map window, like Windows Terminal's own tab menu: one large row per tab that did
not fit the tab row. Display only: keeper_picker owns state and decides what a row click does."""

from keeper_theme import AMBER, BEZEL, BLACK, CLAUDE, CLAUDE_MARK, DIM, EDGE, GO, INK, STEEL, rounded
from keeper_terminal_view import fit_text

ROW_H, NAME_SIZE, NOTE_SIZE = 64, 24, 19       # design pixels: large, readable rows
NOTES = {"live": "already open", "missing": "missing", "selected": "selected", "unknown": "not identified"}


# WHAT IT DOES: Draws the list under (or above) the "+N" anchor, kept inside the map.
# DEPENDS ON: A keeper_terminal_view.Painter; rows = [(key, info)] with info["label"] the FULL name.
# AFFECTS: Canvas items tagged "menu" and "menu-row:<key>"; returns {key: (x, y, w, h)}.
def draw_menu(painter, anchor, rows, area_w, area_h):
    canvas, px, theme = painter.canvas, painter.px, painter.theme
    name_font, note_font = theme.font("body", NAME_SIZE), theme.font("body", NOTE_SIZE)
    symbol = theme.font("symbol", NAME_SIZE)
    left_pad = px(20 + 16 + 34)
    note_w = max(note_font.measure(text) for text in NOTES.values())
    widest = max(name_font.measure(info["label"]) for _, info in rows)
    width = min(left_pad + widest + px(28) + note_w + px(20), area_w - px(40))
    height = px(ROW_H) * len(rows) + px(16)
    x = min(max(anchor[0], px(20)), area_w - width - px(20))
    y = anchor[3] + px(6)
    if y + height > area_h - px(10):
        y = max(anchor[1] - px(6) - height, px(10))
    canvas.create_polygon(rounded(x, y, x + width, y + height, px(10)), smooth=True, fill=BEZEL, outline=INK,
                          width=px(2), tags=("menu",))
    rects = {}
    for number, (key, info) in enumerate(rows):
        top = y + px(8) + number * px(ROW_H)
        tag = f"menu-row:{key}"
        state = info["state"]
        selected = state == "selected"
        canvas.create_rectangle(x + px(8), top + px(3), x + width - px(8), top + px(ROW_H - 3),
                                fill=GO if selected else BEZEL, outline=INK if info.get("inspected") else
                                (GO if selected else EDGE), width=px(3) if info.get("inspected") else 1,
                                tags=("menu", tag))     # white ring = the tab shown in the details drawer
        middle = top + px(ROW_H) / 2
        text = BLACK if selected else INK
        if state == "missing":
            canvas.create_oval(x + px(22), middle - px(7), x + px(36), middle + px(7), fill=AMBER, width=0,
                               tags=("menu", tag))
        mark_x = x + px(44)
        if info.get("kind") == "codex":
            canvas.create_oval(mark_x, middle - px(9), mark_x + px(18), middle + px(9), width=px(3),
                               outline=BLACK if selected else (STEEL if state == "live" else DIM), tags=("menu", tag))
        elif info.get("kind") == "claude":
            canvas.create_text(mark_x, middle, text=CLAUDE_MARK, anchor="w", font=symbol,
                               fill=BLACK if selected else CLAUDE, tags=("menu", tag))
        elif state == "unknown":
            canvas.create_text(mark_x, middle, text="?", anchor="w", font=symbol, fill=INK, tags=("menu", tag))
        room = width - left_pad - note_w - px(48)
        canvas.create_text(x + left_pad, middle, text=fit_text(name_font, info["label"], room), anchor="w",
                           font=name_font, fill=text, tags=("menu", tag))
        canvas.create_text(x + width - px(22), middle, text=NOTES[state], anchor="e", font=note_font,
                           fill=BLACK if selected else (AMBER if state == "missing" else DIM), tags=("menu", tag))
        rects[key] = (x, top, width, px(ROW_H))
    canvas.tag_raise("menu")
    return rects
