# Last modified: 2026-11-10 | Change: Windows from a missing monitor cascade; maximised window monitor area; monitor flicker retry | Full history: git log
"""Physical desktop geometry for terminal recovery."""

import ctypes
from ctypes import wintypes


# WHAT IT DOES: Holds a monitor's physical bounds and available work area.
# DEPENDS ON: Windows MONITORINFOEXW layout.
# AFFECTS: Incorrect fields would corrupt monitor coordinates.
class MonitorInfo(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                ("work", wintypes.RECT), ("flags", wintypes.DWORD), ("device", wintypes.WCHAR * 32)]


# WHAT IT DOES: Reads attached monitors in physical pixels with their taskbar-free work areas.
# Each monitor also carries "dpi", "system_dpi" and "system" (the same work area in system-DPI-aware
# pixels) when Windows can report them; keeper_coords needs these to convert older snapshots.
# DEPENDS ON: Windows display APIs and per-thread DPI awareness.
# AFFECTS: Recovery can avoid placing a window on a disconnected monitor.
def read_monitors():
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    previous = user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    monitors, handles = [], []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                      ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    user.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MonitorInfo)]

    # WHAT IT DOES: Copies one monitor record from the enumeration callback.
    # DEPENDS ON: GetMonitorInfoW and the local output list.
    # AFFECTS: Adds only successfully read physical work areas.
    def collect(handle, device_context, rectangle, user_data):
        info = MonitorInfo()
        info.size = ctypes.sizeof(info)
        if user.GetMonitorInfoW(handle, ctypes.byref(info)):
            monitors.append({"name": info.device, "x": info.work.left, "y": info.work.top,
                "w": info.work.right - info.work.left, "h": info.work.bottom - info.work.top,
                "primary": bool(info.flags & 1)})
            handles.append(handle)
        return True

    callback = callback_type(collect)
    try:
        if not user.EnumDisplayMonitors(None, None, callback, 0) or not monitors:
            raise RuntimeError("Cannot read attached monitors")
        add_dpi_info(user, monitors, handles)
    finally:
        if previous:
            user.SetThreadDpiAwarenessContext(previous)
    return sorted(monitors, key=lambda monitor: monitor["name"])


# WHAT IT DOES: Adds each monitor's DPI, the system DPI and its system-DPI-aware work area.
# DEPENDS ON: shcore.GetDpiForMonitor and GetDpiForSystem (Windows 10 1607+). Leaves the thread per-monitor-v2.
# AFFECTS: On older Windows the fields are simply missing, and older snapshots are then used as saved.
def add_dpi_info(user, monitors, handles):
    try:
        shcore = ctypes.WinDLL("shcore")
        dpis = []
        for handle in handles:
            dpi_x, dpi_y = ctypes.c_uint(), ctypes.c_uint()
            if shcore.GetDpiForMonitor(handle, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) != 0:
                return
            dpis.append(dpi_x.value)
        user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-2))
        try:
            system_dpi = user.GetDpiForSystem()
            areas = []
            for handle in handles:
                info = MonitorInfo()
                info.size = ctypes.sizeof(info)
                if not user.GetMonitorInfoW(handle, ctypes.byref(info)):
                    return
                areas.append({"x": info.work.left, "y": info.work.top,
                              "w": info.work.right - info.work.left, "h": info.work.bottom - info.work.top})
        finally:
            user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        return
    if not system_dpi:
        return
    for monitor, dpi, area in zip(monitors, dpis, areas):
        monitor.update({"dpi": dpi, "system_dpi": system_dpi, "system": area})


# WHAT IT DOES: True when at least 200 x 150 pixels of the rectangle are on one current monitor.
# DEPENDS ON: Physical rectangle and monitor work areas. AFFECTS: Pure.
def on_some_monitor(rectangle, monitors):
    for monitor in monitors:
        width = min(rectangle["x"] + rectangle["w"], monitor["x"] + monitor["w"]) - max(rectangle["x"], monitor["x"])
        height = min(rectangle["y"] + rectangle["h"], monitor["y"] + monitor["h"]) - max(rectangle["y"], monitor["y"])
        if width >= 200 and height >= 150:
            return True
    return False


CASCADE_STEP = 40      # pixels each fallback window steps in from the one before
CASCADE_WRAP = 8       # after this many, start again at the corner


# WHAT IT DOES: How far (pixels, both ways) fallback window number `slot` is stepped in from a corner, so windows
# moved off a missing monitor (or out of the minimised strip) do not land exactly on top of each other.
# Slot 0 is 0; the steps wrap after CASCADE_WRAP windows and never leave less than 200 x 150 of the area.
# DEPENDS ON: Nothing. AFFECTS: Pure.
def cascade_offset(slot, room_w, room_h):
    offset = (slot % CASCADE_WRAP) * CASCADE_STEP
    return max(0, min(offset, room_w - 200, room_h - 150))


# WHAT IT DOES: Keeps the saved position when visible, otherwise moves it onto the primary monitor.
# slot: this window's place among the fallback windows (keeper_core.fallback_slot); slot 0 = the old spot exactly.
# DEPENDS ON: Saved physical rectangle and current monitor work areas.
# AFFECTS: A disconnected monitor cannot make a restored terminal unreachable.
def keep_visible(rectangle, monitors, slot=0):
    if on_some_monitor(rectangle, monitors):
        return rectangle.copy()
    if not monitors:
        raise RuntimeError("No monitor is available for recovery")
    monitor = next((item for item in monitors if item.get("primary")), monitors[0])
    offset = cascade_offset(slot, monitor["w"], monitor["h"])
    return {"x": monitor["x"] + offset, "y": monitor["y"] + offset,
            "w": min(rectangle["w"], monitor["w"] - offset), "h": min(rectangle["h"], monitor["h"] - offset)}


# WHAT IT DOES: The work area of the monitor holding the rectangle's centre (a maximised window's monitor),
# or None when no current monitor holds it.
# DEPENDS ON: Physical rectangle and monitor work areas. AFFECTS: Pure.
def monitor_area(rectangle, monitors):
    cx, cy = rectangle["x"] + rectangle["w"] / 2, rectangle["y"] + rectangle["h"] / 2
    for monitor in monitors or []:
        if monitor["x"] <= cx < monitor["x"] + monitor["w"] and monitor["y"] <= cy < monitor["y"] + monitor["h"]:
            return {key: monitor[key] for key in ("x", "y", "w", "h")}
    return None


# WHAT IT DOES: True when the monitors still match the ones a restore planned with. A differing (or failed)
# read is retried first, since displays flicker while waking; once a change is confirmed (state
# "monitors_changed"), later groups read once without waiting.
# DEPENDS ON: read (read_monitors) and wait (time.sleep), passed in so restore tests can fake them.
# AFFECTS: False skips only the current group.
def monitors_settled(monitors, state, read, wait, retries, retry_seconds):
    tries = 1 if state.get("monitors_changed") else retries + 1
    for attempt in range(tries):
        try:
            if read() == monitors:
                return True
        except RuntimeError:
            pass
        if attempt + 1 < tries:
            wait(retry_seconds)
    state["monitors_changed"] = True
    return False


# WHAT IT DOES: Makes this process DPI-aware before Tk starts, per-monitor when Windows supports it.
# DEPENDS ON: user32/shcore; each newer call is tried first, older ones are fallbacks.
# AFFECTS: Tk renders crisp and its pixels equal screen pixels, so monitor rectangles and clicks line up.
def make_dpi_aware():
    try:
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        if user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except (AttributeError, OSError):
        pass
    try:
        if ctypes.WinDLL("shcore").SetProcessDpiAwareness(2) == 0:
            return "per-monitor"
    except (AttributeError, OSError):
        pass
    try:
        if ctypes.WinDLL("user32").SetProcessDPIAware():
            return "system"
    except (AttributeError, OSError):
        pass
    return None


# WHAT IT DOES: Picks the monitor under the mouse pointer, falling back to the primary one.
# DEPENDS ON: GetCursorPos and read_monitors' physical work areas.
# AFFECTS: Where the recovery window opens.
def pointer_monitor(monitors):
    point = wintypes.POINT()
    try:
        ctypes.WinDLL("user32").GetCursorPos(ctypes.byref(point))
    except OSError:
        point.x = point.y = 0
    for monitor in monitors:
        if monitor["x"] <= point.x < monitor["x"] + monitor["w"] and monitor["y"] <= point.y < monitor["y"] + monitor["h"]:
            return monitor
    return next((m for m in monitors if m.get("primary")), monitors[0] if monitors else None)


# WHAT IT DOES: Tk geometry string for a window close to full screen on one monitor's work area.
# DEPENDS ON: A monitor rectangle in the same pixels Tk uses (true when DPI-aware).
# AFFECTS: Recovery window size and position only.
def big_geometry(monitor, margin_ratio=0.03):
    if not monitor:
        return None
    mx, my = int(monitor["w"] * margin_ratio), int(monitor["h"] * margin_ratio)
    return f"{monitor['w'] - 2 * mx}x{monitor['h'] - 2 * my - 40}+{monitor['x'] + mx}+{monitor['y'] + my}"
