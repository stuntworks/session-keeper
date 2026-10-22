# Last modified: 2026-10-19 | Change: DPI-aware startup and a near-full-screen recovery window | Full history: git log
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
# DEPENDS ON: Windows display APIs and per-thread DPI awareness.
# AFFECTS: Recovery can avoid placing a window on a disconnected monitor.
def read_monitors():
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    previous = user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    monitors = []
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
        return True

    callback = callback_type(collect)
    try:
        if not user.EnumDisplayMonitors(None, None, callback, 0) or not monitors:
            raise RuntimeError("Cannot read attached monitors")
    finally:
        if previous:
            user.SetThreadDpiAwarenessContext(previous)
    return sorted(monitors, key=lambda monitor: monitor["name"])


# WHAT IT DOES: Keeps the saved position when visible, otherwise moves it onto the primary monitor.
# DEPENDS ON: Saved physical rectangle and current monitor work areas.
# AFFECTS: A disconnected monitor cannot make a restored terminal unreachable.
def keep_visible(rectangle, monitors):
    for monitor in monitors:
        width = min(rectangle["x"] + rectangle["w"], monitor["x"] + monitor["w"]) - max(rectangle["x"], monitor["x"])
        height = min(rectangle["y"] + rectangle["h"], monitor["y"] + monitor["h"]) - max(rectangle["y"], monitor["y"])
        if width >= 200 and height >= 150:
            return rectangle.copy()
    if not monitors:
        raise RuntimeError("No monitor is available for recovery")
    monitor = next((item for item in monitors if item.get("primary")), monitors[0])
    return {"x": monitor["x"], "y": monitor["y"],
            "w": min(rectangle["w"], monitor["w"]), "h": min(rectangle["h"], monitor["h"])}


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
