# Last modified: 2026-10-22 | Change: exact_layout reserves a stand under each monitor and centres the desk | Full history: git log
"""Pure geometry for the recovery map. Display positions only; restores always use the real saved positions."""

from keeper_home import is_minimised

GAP = 8
MIN_BOX_W = 190                # scale_layout only


# WHAT IT DOES: Says whether a saved window can be drawn at its real spot.
# DEPENDS ON: Integer x, y, w, h of a reasonable size, not parked off-screen.
# AFFECTS: Others go to the "minimised" strip instead of off the map.
def drawable(window):
    if not all(type(window.get(key)) is int for key in ("x", "y", "w", "h")):
        return False
    return not is_minimised(window) and window["w"] >= 200 and window["h"] >= 150


# WHAT IT DOES: Splits saved windows into ones drawn at their spot and ones listed in the minimised strip.
# DEPENDS ON: The snapshot's window list order (which gives "Window N").
# AFFECTS: Returns two lists of window indexes.
def split_windows(windows):
    placed = [index for index, window in enumerate(windows) if drawable(window)]
    strip = [index for index in range(len(windows)) if index not in placed]
    return placed, strip


# WHAT IT DOES: Scales monitors and windows into an area, side by side as they really sit.
# DEPENDS ON: Saved monitors (falls back to the windows' own bounds) and drawable windows.
# AFFECTS: Returns (monitor_rects, box_rects, scale) in canvas pixels; box heights grow to fit content.
def scale_layout(monitors, windows, placed, area_w, area_h, content_height, margin=12):
    bounds = list(monitors or []) or [windows[index] for index in placed]
    if not bounds:
        return [], {}, 1.0
    left, top = min(item["x"] for item in bounds), min(item["y"] for item in bounds)
    width = max(item["x"] + item["w"] for item in bounds) - left
    height = max(item["y"] + item["h"] for item in bounds) - top
    scale = min((area_w - 2 * margin) / max(width, 1), (area_h - 2 * margin) / max(height, 1))

    def to_canvas(item):
        return {"x": margin + (item["x"] - left) * scale, "y": margin + (item["y"] - top) * scale,
                "w": item["w"] * scale, "h": item["h"] * scale}
    monitor_rects = [to_canvas(monitor) for monitor in monitors or []]
    boxes = {}
    for index in placed:
        box = to_canvas(windows[index])
        box["w"] = max(box["w"], MIN_BOX_W)
        box["h"] = max(box["h"], content_height(index, box["w"]))
        boxes[index] = box
    return monitor_rects, nudge_apart(boxes), scale


# WHAT IT DOES: Reports whether two boxes share any horizontal span.
# DEPENDS ON: x and w.
# AFFECTS: Boxes sharing a column keep their top-to-bottom order.
def shares_column(first, second):
    return first["x"] < second["x"] + second["w"] and second["x"] < first["x"] + first["w"]


# WHAT IT DOES: Moves overlapping boxes down (never sideways) until none overlap.
# DEPENDS ON: Boxes keyed by window index.
# AFFECTS: x never changes, so left-right order and monitor stay; boxes in one column keep their order.
def nudge_apart(boxes, gap=GAP):
    order = sorted(boxes, key=lambda key: (boxes[key]["y"], boxes[key]["x"], key))
    placed = {}
    for key in order:
        box = dict(boxes[key])
        mates = [placed[other] for other in placed if shares_column(placed[other], box)]
        for mate in mates:
            box["y"] = max(box["y"], mate["y"])
        moved = True
        while moved:
            moved = False
            for mate in mates:
                if box["y"] < mate["y"] + mate["h"] + gap and mate["y"] < box["y"] + box["h"] + gap:
                    box["y"] = mate["y"] + mate["h"] + gap
                    moved = True
        placed[key] = box
    return placed


# WHAT IT DOES: Draws the desk exactly: every monitor and every window at its real x/y/w/h under ONE scale
# (no widening, no pushing apart, no scrolling), with each monitor's taskbar strip under its screen and room
# for its stand below the bezel. The whole desk is centred in the area.
# DEPENDS ON: Saved monitors (falls back to one screen around the windows) and drawable window indexes.
# AFFECTS: Returns (screens, boxes, home, scale): screens = [{"bezel", "screen", "bar", "stand", "number"}] in
# saved monitor order; boxes by window index, cut at their screen's edge; home = window index -> screen number.
def exact_layout(monitors, windows, placed, area_w, area_h, gap=36, side=16, top=16, bar=60, chin=40, stand=0):
    bounds = list(monitors or [])
    if not bounds:
        shown = [windows[index] for index in placed]
        if not shown:
            return [], {}, {}, 1.0
        left, upper = min(w["x"] for w in shown), min(w["y"] for w in shown)
        bounds = [{"x": left, "y": upper, "w": max(w["x"] + w["w"] for w in shown) - left,
                   "h": max(w["y"] + w["h"] for w in shown) - upper}]
    left, upper = min(m["x"] for m in bounds), min(m["y"] for m in bounds)
    desk_w = max(m["x"] + m["w"] for m in bounds) - left
    desk_h = max(m["y"] + m["h"] for m in bounds) - upper
    # Gaps between monitors: count the monitors wholly left of / above each one.
    column = [sum(1 for o in bounds if o["x"] + o["w"] <= m["x"]) for m in bounds]
    row = [sum(1 for o in bounds if o["y"] + o["h"] <= m["y"]) for m in bounds]
    columns, rows = max(column) + 1, max(row) + 1
    extra_w = columns * 2 * side + (columns - 1) * gap
    extra_h = rows * (top + bar + chin + stand) + (rows - 1) * gap
    scale = max(min((area_w - extra_w) / max(desk_w, 1), (area_h - extra_h) / max(desk_h, 1)), 0.01)
    offset = max((area_w - desk_w * scale - extra_w) / 2, 0)
    lift = max((area_h - desk_h * scale - extra_h) / 2, 0)
    screens = []
    for number, monitor in enumerate(bounds):
        sx = offset + side + column[number] * (2 * side + gap) + (monitor["x"] - left) * scale
        sy = lift + top + row[number] * (top + bar + chin + stand + gap) + (monitor["y"] - upper) * scale
        sw, sh = monitor["w"] * scale, monitor["h"] * scale
        bezel = {"x": sx - side, "y": sy - top, "w": sw + 2 * side, "h": sh + top + bar + chin}
        screens.append({"number": number, "screen": {"x": sx, "y": sy, "w": sw, "h": sh},
                        "bar": {"x": sx, "y": sy + sh, "w": sw, "h": bar}, "bezel": bezel,
                        "stand": {"x": bezel["x"], "y": bezel["y"] + bezel["h"], "w": bezel["w"], "h": stand}})
    boxes, home = {}, {}
    for index in placed:
        window = windows[index]
        number = monitor_for(bounds, window)
        real, screen = bounds[number], screens[number]["screen"]
        x1 = max(screen["x"] + (window["x"] - real["x"]) * scale, screen["x"])
        y1 = max(screen["y"] + (window["y"] - real["y"]) * scale, screen["y"])
        x2 = min(screen["x"] + (window["x"] + window["w"] - real["x"]) * scale, screen["x"] + screen["w"])
        y2 = min(screen["y"] + (window["y"] + window["h"] - real["y"]) * scale, screen["y"] + screen["h"])
        boxes[index] = {"x": x1, "y": y1, "w": max(x2 - x1, 1), "h": max(y2 - y1, 1)}
        home[index] = number
    return screens, boxes, home, scale


# WHAT IT DOES: The monitor holding a window's centre, or the nearest one.
# DEPENDS ON: Monitor rectangles in saved (real) coordinates.
# AFFECTS: Which screen a window is drawn on.
def monitor_for(bounds, window):
    cx, cy = window["x"] + window["w"] / 2, window["y"] + window["h"] / 2

    def distance(monitor):
        dx = max(monitor["x"] - cx, 0, cx - monitor["x"] - monitor["w"])
        dy = max(monitor["y"] - cy, 0, cy - monitor["y"] - monitor["h"])
        return dx * dx + dy * dy
    return min(range(len(bounds)), key=lambda number: distance(bounds[number]))
