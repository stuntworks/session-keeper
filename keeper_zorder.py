# Last modified: 2026-10-22 | Change: Z-order capture, front/hidden detection, taskbar buttons, restore order | Full history: git log
"""Which saved terminal window was in front of which.

Capture: the windows list comes from UI Automation FindAll(Children), whose order Windows does not document
as z-order, so each window also gets an explicit "z" (0 = frontmost) read with GetTopWindow/GetWindow
(GW_HWNDNEXT), which the Win32 docs define as walking the z-order top to bottom. Read only.
Old snapshots have no "z": they fall back to list order (best effort, usually top first in practice)."""

import ctypes

GW_HWNDNEXT = 2
HIDDEN_SHARE = 0.8          # a window this much covered by windows in front of it counts as hidden
WALK_LIMIT = 100000         # stop a z-order walk that loops or never ends


# WHAT IT DOES: Walks the live z-order from the top and ranks just the given window handles (0 = front).
# DEPENDS ON: top() -> first handle; below(handle) -> next handle down, 0/None at the end.
# AFFECTS: Nothing; returns {hwnd: rank}. A looping walk returns {} rather than a wrong order.
def z_ranks(hwnds, top, below):
    wanted, ranks, seen = set(hwnds), {}, set()
    handle = top()
    while handle and len(ranks) < len(wanted):
        if handle in seen or len(seen) > WALK_LIMIT:
            return {}
        seen.add(handle)
        if handle in wanted:
            ranks[handle] = len(ranks)
        handle = below(handle)
    return ranks


# WHAT IT DOES: Reads live ranks for terminal windows with the documented Win32 z-order walk.
# DEPENDS ON: user32 GetTopWindow / GetWindow. Read only; never moves or focuses a window.
# AFFECTS: Returns {hwnd: rank}.
def read_z_ranks(hwnds):
    user32 = ctypes.windll.user32
    user32.GetTopWindow.restype = ctypes.c_void_p
    user32.GetWindow.restype = ctypes.c_void_p
    user32.GetWindow.argtypes = (ctypes.c_void_p, ctypes.c_uint)
    return z_ranks(hwnds, lambda: user32.GetTopWindow(None) or 0,
                   lambda handle: user32.GetWindow(handle, GW_HWNDNEXT) or 0)


# WHAT IT DOES: Writes each window's rank into its "z" field.
# DEPENDS ON: Windows carrying "hwnd".
# AFFECTS: Changes the given window dicts in place; windows without a rank get no "z".
def add_z(windows, ranks):
    for window in windows:
        if window.get("hwnd") in ranks:
            window["z"] = ranks[window["hwnd"]]
    return windows


# WHAT IT DOES: Window indexes from front to back.
# DEPENDS ON: "z" on every window; otherwise the saved list order (old snapshots, best effort).
# AFFECTS: Display order on the map and restore order.
def stack_order(windows):
    if windows and all(type(window.get("z")) is int for window in windows):
        return sorted(range(len(windows)), key=lambda index: (windows[index]["z"], index))
    return list(range(len(windows)))


def _area(boxes):
    """Exact area of a union of (x1, y1, x2, y2) boxes, by cutting the plane at every edge."""
    xs = sorted({value for box in boxes for value in (box[0], box[2])})
    total = 0
    for left, right in zip(xs, xs[1:]):
        spans = sorted((box[1], box[3]) for box in boxes if box[0] <= left and box[2] >= right)
        covered, end = 0, None
        for top, bottom in spans:
            if end is None or top > end:
                covered += bottom - top
                end = bottom
            elif bottom > end:
                covered += bottom - end
                end = bottom
        total += covered * (right - left)
    return total


# WHAT IT DOES: Finds drawn windows covered at least HIDDEN_SHARE by the drawn windows in front of them.
# DEPENDS ON: stack_order; drawable = indexes of windows shown at their spot (not minimised).
# AFFECTS: Hidden windows are not drawn on the map; they get a taskbar button instead.
def hidden_windows(windows, drawable):
    drawable = set(drawable)
    order = [index for index in stack_order(windows) if index in drawable]
    hidden = set()
    for depth, index in enumerate(order):
        window = windows[index]
        x1, y1 = window["x"], window["y"]
        x2, y2 = x1 + window["w"], y1 + window["h"]
        cover = []
        for other in order[:depth]:
            front = windows[other]
            box = (max(x1, front["x"]), max(y1, front["y"]),
                   min(x2, front["x"] + front["w"]), min(y2, front["y"] + front["h"]))
            if box[0] < box[2] and box[1] < box[3]:
                cover.append(box)
        if cover and _area(cover) >= HIDDEN_SHARE * window["w"] * window["h"]:
            hidden.add(index)
    return hidden


# WHAT IT DOES: Puts a taskbar button on each monitor for its hidden windows; minimised ones go to the primary.
# DEPENDS ON: Saved monitors (may be empty: then one bar, number 0) and keeper_map_layout.monitor_for.
# AFFECTS: Returns {monitor number: [window indexes]} in front-to-back order.
def taskbar_buttons(monitors, windows, hidden, minimised):
    from keeper_map_layout import monitor_for
    monitors = list(monitors or [])
    primary = next((number for number, monitor in enumerate(monitors) if monitor.get("primary")), 0)
    minimised = set(minimised)
    buttons = {}
    for index in stack_order(windows):
        if index in minimised:
            number = primary
        elif index in hidden:
            number = monitor_for(monitors, windows[index]) if monitors else 0
        else:
            continue
        buttons.setdefault(number, []).append(index)
    return buttons


# WHAT IT DOES: Orders restore plans back window first, front window last, so windows that were hidden
# behind another end up behind it again (each newly opened window comes to the front).
# DEPENDS ON: Plans from keeper_core.plan_restore (each with tabs[0]["window_index"]) and the snapshot.
# AFFECTS: Only the ORDER of the same plan objects; never which tabs or sessions open.
def back_to_front(plans, snapshot):
    order = stack_order(snapshot.get("windows", []))
    depth = {index: position for position, index in enumerate(order)}
    return sorted(plans, key=lambda plan: -depth.get(plan["tabs"][0]["window_index"], -1))
