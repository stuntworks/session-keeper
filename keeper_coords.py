# Last modified: 2026-11-10 | Change: The died-in-crash window (already physical pixels) is never converted | Full history: git log
"""One pixel space for saved windows, monitors and MoveWindow: physical (per-monitor) pixels.

Snapshots saved before 2026-10-27 have no "coords" marker. Their window rectangles were read
system-DPI-aware, which on a mixed-DPI desk puts monitors at different places than the physical
monitor list beside them (a right-hand 4K monitor sits at x=5760 instead of x=3840). This module
maps such a rectangle back: find the monitor it sat on in system-aware space, then move it onto the
same monitor in physical space, scaled by that monitor's DPI over the system DPI. That needs the live
monitors to be the same ones the snapshot saved; if they are not, nothing is guessed."""

from keeper_home import is_minimised

PHYSICAL = "physical"
KEYS = ("x", "y", "w", "h")


# WHAT IT DOES: The fix-it line when an older snapshot cannot be converted.
def unmatched_warning():
    return ("This layout was saved by an older Session Keeper and the monitors are not set up the same way now, "
            "so windows from a scaled monitor may open in a different spot. To fix: drag them where you want "
            "after they open.")


# WHAT IT DOES: Returns the snapshot with every window rectangle in physical pixels, plus any warnings.
# DEPENDS ON: Live monitors from keeper_display.read_monitors (physical work area, dpi, system, system_dpi).
# AFFECTS: Never edits the given snapshot. A marked or unconvertible snapshot is returned as the same object.
# DANGER ZONE HIGH: Converting twice moves windows wrongly / breaks: restore spots / depends on: the "coords" marker.
def physical_snapshot(snapshot, live_monitors):
    if snapshot.get("coords") == PHYSICAL:
        return snapshot, []
    windows = snapshot.get("windows") or []
    layout = matched_layout(snapshot.get("monitors"), live_monitors)
    if layout is None:
        return snapshot, ([unmatched_warning()] if windows and snapshot.get("monitors") else [])
    converted = dict(snapshot, coords=PHYSICAL, windows=[convert_window(window, layout) for window in windows])
    return converted, []


# WHAT IT DOES: The live monitors, when they are exactly the saved ones and carry DPI information; else None.
# DEPENDS ON: Same count, positions and sizes (physical work areas) as saved.
# AFFECTS: None means the snapshot is used as saved.
def matched_layout(saved, live):
    if not saved or not live or len(saved) != len(live):
        return None
    if sorted(rect_of(m) for m in saved) != sorted(rect_of(m) for m in live):
        return None
    for monitor in live:
        system = monitor.get("system")
        if (not isinstance(system, dict) or not all(type(system.get(k)) is int for k in KEYS)
                or not monitor.get("dpi") or not monitor.get("system_dpi")):
            return None
    return live


# WHAT IT DOES: A plain x, y, w, h tuple for comparing monitors.
def rect_of(item):
    return tuple(item.get(key) for key in KEYS)


# WHAT IT DOES: One window copied with its rectangle in physical pixels and its saved one kept as converted_from.
# DEPENDS ON: A matched layout. Minimised, incomplete or off-every-monitor rectangles are copied unchanged,
# and so is keeper_crash's "crash_group" window, whose spot was made in physical pixels to begin with.
# AFFECTS: The copy only.
def convert_window(window, layout):
    copy = dict(window)
    rect = {key: window.get(key) for key in KEYS}
    if not all(type(value) is int for value in rect.values()) or is_minimised(rect) or window.get("crash_group"):
        return copy
    monitor = system_monitor(rect, layout)
    if monitor is None:
        return copy
    scale = monitor["dpi"] / monitor["system_dpi"]
    system = monitor["system"]
    copy.update({"x": monitor["x"] + round((rect["x"] - system["x"]) * scale),
                 "y": monitor["y"] + round((rect["y"] - system["y"]) * scale),
                 "w": round(rect["w"] * scale), "h": round(rect["h"] * scale),
                 "converted_from": rect})
    return copy


# WHAT IT DOES: The monitor whose system-aware area holds the rectangle's centre, else the one it overlaps most.
# DEPENDS ON: Each monitor's "system" work area.
# AFFECTS: None means the rectangle touched no monitor and is left as saved.
def system_monitor(rect, layout):
    cx, cy = rect["x"] + rect["w"] / 2, rect["y"] + rect["h"] / 2
    for monitor in layout:
        area = monitor["system"]
        if area["x"] <= cx < area["x"] + area["w"] and area["y"] <= cy < area["y"] + area["h"]:
            return monitor
    best, best_area = None, 0
    for monitor in layout:
        area = monitor["system"]
        width = min(rect["x"] + rect["w"], area["x"] + area["w"]) - max(rect["x"], area["x"])
        height = min(rect["y"] + rect["h"], area["y"] + area["h"]) - max(rect["y"], area["y"])
        if width > 0 and height > 0 and width * height > best_area:
            best, best_area = monitor, width * height
    return best


# WHAT IT DOES: The snapshot the map should draw: converted when possible, else exactly as saved.
# DEPENDS ON: A zero-argument monitor reader (keeper_display.read_monitors by default).
# AFFECTS: Display only; an unreadable monitor list never stops the map from drawing.
def map_snapshot(snapshot, reader=None):
    if snapshot.get("coords") == PHYSICAL:
        return snapshot
    try:
        if reader is None:
            from keeper_display import read_monitors as reader
        return physical_snapshot(snapshot, reader())[0]
    except (OSError, RuntimeError, AttributeError, ValueError):
        return snapshot
