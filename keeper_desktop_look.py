# Last modified: 2026-10-22 | Change: Monitor names and wallpapers for the map, read locally, never saved | Full history: git log
"""What this PC's monitors look like, so the map can draw them: each monitor's model name (from its EDID) and
its desktop wallpaper (or solid colour), dimmed so windows and text on top stay readable.
Read only, local only, never saved into snapshots. Any failure gives an empty answer: the map then draws its
own tinted desktops and "Monitor N" names."""

import ctypes
import winreg
from ctypes import wintypes

from keeper_theme import WALL_CEIL, WALL_FLOOR

MAX_WIDTH = 1600              # wallpapers are kept at most this wide; the map is never bigger per monitor
GREY_SHARE = 0.45             # how much colour is taken out before dimming
LIFT = 0.65                   # curve that lifts the dark and middle tones so the picture still shows
CLSID_WALLPAPER = "{C2CF3110-460E-4FC1-B9D0-8A1C0C9CC4BD}"
IID_WALLPAPER = "{B92B56A9-8B55-4E14-9A89-0199BBB6F93B}"
GENERIC = ("generic", "pnp", "default monitor")


class GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]


# WHAT IT DOES: Reads every connected monitor's rectangle, model name and dimmed wallpaper.
# DEPENDS ON: IDesktopWallpaper (Windows 8+), else EnumDisplayMonitors + the one shared wallpaper.
# AFFECTS: Nothing; returns [{"rect": (x, y, w, h), "name", "image" (PIL or None), "colour"}], [] on failure.
def read_desktop_look():
    for reader in (wallpaper_monitors, display_monitors):
        try:
            monitors = reader()
        except Exception:
            continue
        if monitors:
            return [finish(monitor) for monitor in monitors]
    return []


# WHAT IT DOES: Adds the model name and the dimmed picture to one monitor found by a reader.
# DEPENDS ON: "path" (monitor device path), "file" (wallpaper file or ""), "colour" (solid colour or None).
# AFFECTS: Nothing; a missing name or unreadable picture is simply left out.
def finish(monitor):
    name = None
    try:
        name = monitor_name(monitor.get("path") or "")
    except Exception:
        pass
    image = None
    if monitor.get("file"):
        try:
            image = load_image(monitor["file"], monitor["rect"])
        except Exception:
            image = None
    colour = dim_colour(monitor["colour"]) if monitor.get("colour") else None
    return {"rect": monitor["rect"], "name": name, "image": image, "colour": colour}


# WHAT IT DOES: The live monitor whose area holds the saved monitor's centre.
# DEPENDS ON: Saved monitor x/y/w/h in the same screen pixels as the live rectangles.
# AFFECTS: Nothing; None when the desk has changed and nothing sits there now.
def match(live, saved):
    cx, cy = saved["x"] + saved["w"] / 2, saved["y"] + saved["h"] / 2
    for monitor in live or []:
        x, y, w, h = monitor["rect"]
        if x <= cx < x + w and y <= cy < y + h:
            return monitor
    return None


# WHAT IT DOES: Takes most of the colour out and squeezes every channel between WALL_FLOOR and WALL_CEIL.
# DEPENDS ON: A PIL RGB image.
# AFFECTS: Nothing; no pixel can be brighter than WALL_CEIL, so the contrast tests on it hold for the picture.
def dim_image(image):
    from PIL import Image, ImageOps
    image = image.convert("RGB")
    image = Image.blend(image, ImageOps.grayscale(image).convert("RGB"), GREY_SHARE)
    table = []
    for low, high in zip(channels(WALL_FLOOR), channels(WALL_CEIL)):
        table += [round(low + (value / 255) ** LIFT * (high - low)) for value in range(256)]
    return image.point(table)


# WHAT IT DOES: A stand-in wallpaper when the real one is unknown: the monitor's tint as a soft top-to-bottom
# gradient with a gentle light low on the right, so an empty screen looks like a desktop, not a hole.
# DEPENDS ON: Pillow; every pixel lies between the tint's darker shade and WALL_CEIL (contrast-safe).
# AFFECTS: Nothing; returns a PIL image of `size`.
def tinted_desktop(tint, size):
    from PIL import Image
    width, height = size
    top, low = blend(tint, WALL_CEIL, 0.30), blend(tint, "#000000", 0.30)
    shade = Image.linear_gradient("L").resize(size, Image.BILINEAR)
    picture = Image.composite(Image.new("RGB", size, low), Image.new("RGB", size, top), shade)
    glow_w, glow_h = max(1, round(width * 0.75)), max(1, round(height * 0.85))
    glow = Image.radial_gradient("L").resize((glow_w, glow_h), Image.BILINEAR)
    glow = glow.point([round(0.50 * (1 - value / 255) ** 1.6 * 255) for value in range(256)])
    mask = Image.new("L", size, 0)
    mask.paste(glow, (round(width * 0.66 - glow_w / 2), round(height * 0.60 - glow_h / 2)))
    return Image.composite(Image.new("RGB", size, WALL_CEIL), picture, mask)


def blend(colour, toward, share):
    return "#" + "".join(f"{round(a * (1 - share) + b * share):02X}" for a, b in zip(channels(colour), channels(toward)))


# WHAT IT DOES: The same dimming for a solid desktop colour.
def dim_colour(colour):
    parts = channels(colour)
    grey = 0.299 * parts[0] + 0.587 * parts[1] + 0.114 * parts[2]
    out = []
    for value, low, high in zip(parts, channels(WALL_FLOOR), channels(WALL_CEIL)):
        value = value * (1 - GREY_SHARE) + grey * GREY_SHARE
        out.append(round(low + (value / 255) ** LIFT * (high - low)))
    return "#" + "".join(f"{part:02X}" for part in out)


def channels(colour):
    return [int(colour[index:index + 2], 16) for index in (1, 3, 5)]


# WHAT IT DOES: Opens a wallpaper file, crops it to cover the monitor's shape and dims it.
# DEPENDS ON: Pillow (if missing, the map keeps its tinted desktop).
# AFFECTS: Nothing; returns a PIL image at most MAX_WIDTH wide.
def load_image(path, rect):
    from PIL import Image, ImageOps
    width = max(1, min(MAX_WIDTH, rect[2]))
    height = max(1, round(width * rect[3] / max(rect[2], 1)))
    with Image.open(path) as picture:
        picture.draft("RGB", (width, height))
        picture = ImageOps.exif_transpose(picture).convert("RGB")
        return dim_image(ImageOps.fit(picture, (width, height), Image.LANCZOS))


# WHAT IT DOES: A monitor's model name, e.g. "DELL U2720Q", from the EDID Windows keeps in the registry.
# DEPENDS ON: A monitor device path like \\?\DISPLAY#DEL41A8#5&...&UID4352#{guid}.
# AFFECTS: Nothing; None when unknown or only a generic driver name.
def monitor_name(path):
    key_path = registry_key(path)
    if not key_path:
        return None
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
        return edid_name(winreg.QueryValueEx(key, "EDID")[0])


def registry_key(path):
    parts = path.split("#")
    if len(parts) < 3 or not parts[1] or not parts[2]:
        return None
    return rf"SYSTEM\CurrentControlSet\Enum\DISPLAY\{parts[1]}\{parts[2]}\Device Parameters"


# WHAT IT DOES: The monitor name descriptor (tag 0xFC) inside a 128-byte EDID block.
def edid_name(data):
    for start in (54, 72, 90, 108):
        block = bytes(data[start:start + 18])
        if len(block) == 18 and block[:3] == b"\0\0\0" and block[3] == 0xFC:
            name = block[5:].split(b"\n")[0].decode("ascii", "ignore").strip()
            if name and not any(word in name.lower() for word in GENERIC):
                return name
    return None


# WHAT IT DOES: Per-monitor wallpapers through the Windows shell's IDesktopWallpaper object.
# DEPENDS ON: COM (ole32); runs on whatever thread calls it, with its own COM setup.
# AFFECTS: Nothing; returns [{"rect", "path", "file", "colour"}].
def wallpaper_monitors():
    ole32 = ctypes.OleDLL("ole32")
    ole32.CoInitializeEx(None, 2)
    try:
        clsid, iid = GUID(), GUID()
        ole32.CLSIDFromString(CLSID_WALLPAPER, ctypes.byref(clsid))
        ole32.IIDFromString(IID_WALLPAPER, ctypes.byref(iid))
        shell = ctypes.c_void_p()
        ole32.CoCreateInstance(ctypes.byref(clsid), None, 0x17, ctypes.byref(iid), ctypes.byref(shell))
        table = ctypes.cast(shell, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents

        def method(index, *argtypes):
            return ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(table[index])
        try:
            count = ctypes.c_uint()
            method(6, ctypes.POINTER(ctypes.c_uint))(shell, ctypes.byref(count))
            colour = ctypes.c_ulong()
            method(9, ctypes.POINTER(ctypes.c_ulong))(shell, ctypes.byref(colour))
            solid = "#{:02X}{:02X}{:02X}".format(colour.value & 255, colour.value >> 8 & 255, colour.value >> 16 & 255)
            monitors = []
            for index in range(count.value):
                text = ctypes.c_wchar_p()
                try:
                    method(5, ctypes.c_uint, ctypes.POINTER(ctypes.c_wchar_p))(shell, index, ctypes.byref(text))
                    path = taken(ole32, text)
                    rect = wintypes.RECT()
                    method(7, ctypes.c_wchar_p, ctypes.POINTER(wintypes.RECT))(shell, path, ctypes.byref(rect))
                    method(4, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_wchar_p))(shell, path, ctypes.byref(text))
                    file = taken(ole32, text)
                except OSError:
                    continue         # a monitor that is listed but not attached right now
                monitors.append({"rect": (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top),
                                 "path": path, "file": file, "colour": None if file else solid})
            return monitors
        finally:
            method(2)(shell)
    finally:
        ole32.CoUninitialize()


# WHAT IT DOES: Copies a COM-allocated string and frees it.
def taken(ole32, text):
    value = text.value or ""
    if text.value is not None:
        ole32.CoTaskMemFree(text)
    text.value = None
    return value


# WHAT IT DOES: Fallback: every monitor from EnumDisplayMonitors, all sharing the one desktop wallpaper.
# DEPENDS ON: user32 only.
# AFFECTS: Nothing; returns [{"rect", "path", "file", "colour"}].
def display_monitors():
    user32 = ctypes.WinDLL("user32")
    buffer = ctypes.create_unicode_buffer(520)
    user32.SystemParametersInfoW(0x0073, len(buffer), buffer, 0)          # SPI_GETDESKWALLPAPER
    file = buffer.value
    value = user32.GetSysColor(1)                                         # COLOR_DESKTOP
    solid = "#{:02X}{:02X}{:02X}".format(value & 255, value >> 8 & 255, value >> 16 & 255)

    class INFO(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT), ("work", wintypes.RECT),
                    ("flags", wintypes.DWORD), ("device", wintypes.WCHAR * 32)]

    class DEVICE(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("name", wintypes.WCHAR * 32), ("string", wintypes.WCHAR * 128),
                    ("flags", wintypes.DWORD), ("id", wintypes.WCHAR * 128), ("key", wintypes.WCHAR * 128)]
    found = []

    def each(handle, dc, rect, data):
        info = INFO()
        info.size = ctypes.sizeof(INFO)
        if user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            device = DEVICE()
            device.size = ctypes.sizeof(DEVICE)
            path = device.id if user32.EnumDisplayDevicesW(info.device, 0, ctypes.byref(device), 1) else ""
            box = info.monitor
            found.append({"rect": (box.left, box.top, box.right - box.left, box.bottom - box.top), "path": path,
                          "file": file, "colour": None if file else solid})
        return True
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                                  wintypes.LPARAM)(each)
    user32.EnumDisplayMonitors(None, None, callback, 0)
    return found
