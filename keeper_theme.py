# Last modified: 2026-10-21 | Change: Dark theme tokens, scaled fonts, contrast check and flat buttons for the Tk screens | Full history: git log
"""Colour, type and button styling shared by every Session Keeper window. Display only."""

import ctypes
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# Colour tokens (from WORK/2026-10-21 frontend-design/recovery_screen.html, Dim lightened for 4.5:1).
ROOM, BEZEL, EDGE = "#0E1217", "#1A1F27", "#2A313C"
TERM, STRIP = "#0C0C0C", "#1F1F1F"
INK, DIM = "#E6E9EF", "#C2C9D5"   # Dim brightened again (owner: brighter lettering), 7:1+ on every surface
AMBER, GO, STEEL, STOP = "#F5A524", "#3DDC84", "#5B6575", "#F36D6A"  # Stop lightened from #F0524F for 7:1
CLAUDE = "#D97757"
OPEN_FILL = "#2B323D"          # open tabs keep a steel-dark fill; their text stays bright
HOVER_LINE, HOVER_GO, HOVER_STOP = "#232933", "#5BE69A", "#F78784"
BLACK = "#000000"
TINTS = ("#152029", "#1D1B2E")     # monitor screens: 1 a cool teal slate, 2 an indigo slate, so they tell apart
TASKBAR = "#0F1319"                # each monitor's taskbar strip
OUTLINE = "#C85A74"                # burgundy edge round every saved terminal window

DISPLAY = "Bahnschrift SemiBold Condensed"
BODY = "Segoe UI"
MONO = "Cascadia Mono"
ICONS = "Segoe Fluent Icons"
SYMBOL = "Segoe UI Symbol"

# Icon code points (Segoe Fluent Icons / MDL2 share these).
IC_TERMINAL, IC_CLOSE, IC_ADD, IC_DOWN = "", "", "", ""
IC_MIN, IC_MAX, IC_CHECK, IC_COPY = "", "", "", ""
IC_RESTART, IC_PREVIEW, IC_WARN, IC_WINDOW = "", "", "", ""
CLAUDE_MARK = "✱"

DESIGN_WIDTH = 2400            # the mockup stage width every pixel size below is measured on


# WHAT IT DOES: Relative luminance of a #RRGGBB colour (WCAG 2).
# DEPENDS ON: Nothing.
# AFFECTS: Nothing.
def luminance(colour):
    def channel(value):
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
    red, green, blue = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


# WHAT IT DOES: WCAG contrast ratio between two colours (1 to 21).
# DEPENDS ON: luminance.
# AFFECTS: Nothing; used by the theme test to keep every label readable.
def contrast(first, second):
    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


# Every text colour drawn on every background it sits on. The theme test checks each pair.
TEXT_PAIRS = {
    "ink on room": (INK, ROOM), "ink on bezel": (INK, BEZEL), "ink on terminal": (INK, TERM),
    "ink on tab strip": (INK, STRIP), "ink on open tab": (INK, OPEN_FILL), "ink on edge": (INK, EDGE),
    "dim on room": (DIM, ROOM), "dim on bezel": (DIM, BEZEL), "dim on tab strip": (DIM, STRIP),
    "dim on terminal": (DIM, TERM), "amber on room": (AMBER, ROOM), "amber on bezel": (AMBER, BEZEL),
    "amber on terminal": (AMBER, TERM), "amber on tab strip": (AMBER, STRIP), "go on bezel": (GO, BEZEL),
    "black on go": (BLACK, GO), "black on stop": (BLACK, STOP), "room on ink": (ROOM, INK),
    "black on hover go": (BLACK, HOVER_GO), "black on hover stop": (BLACK, HOVER_STOP),
    "ink on hover": (INK, HOVER_LINE), "stop on bezel": (STOP, BEZEL), "stop on room": (STOP, ROOM), "claude mark on strip": (CLAUDE, STRIP),
    "ink on taskbar": (INK, TASKBAR), "dim on taskbar": (DIM, TASKBAR), "amber on taskbar": (AMBER, TASKBAR),
    **{f"ink on {tint}": (INK, tint) for tint in TINTS}, **{f"dim on {tint}": (DIM, tint) for tint in TINTS},
    **{f"amber on {tint}": (AMBER, tint) for tint in TINTS},
}
OUTLINE_PAIRS = {"outline on room": (OUTLINE, ROOM), "outline on terminal": (OUTLINE, TERM),
                 "outline on tab strip": (OUTLINE, STRIP), **{f"outline on {t}": (OUTLINE, t) for t in TINTS}}
MIN_OUTLINE = 3.0                  # WCAG non-text contrast
MIN_BODY, MIN_SECONDARY = 7.0, 4.5


# WHAT IT DOES: Blends a colour toward another (0 = first, 1 = second). Tk has no opacity, so fades use this.
# DEPENDS ON: Nothing.
# AFFECTS: Nothing.
def mix(colour, toward, amount):
    parts = [int(colour[i:i + 2], 16) * (1 - amount) + int(toward[i:i + 2], 16) * amount for i in (1, 3, 5)]
    return "#" + "".join(f"{round(part):02X}" for part in parts)


# WHAT IT DOES: Points of a rounded rectangle for canvas.create_polygon(..., smooth=True).
# DEPENDS ON: Nothing.
# AFFECTS: Nothing.
def rounded(x1, y1, x2, y2, r):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    return [x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y1 + r, x2, y2 - r,
            x2, y2 - r, x2, y2, x2 - r, y2, x2 - r, y2, x1 + r, y2, x1 + r, y2, x1, y2, x1, y2 - r,
            x1, y2 - r, x1, y1 + r, x1, y1 + r, x1, y1]


# WHAT IT DOES: Says whether Windows has animations turned off (Settings > Accessibility > Animation effects).
# DEPENDS ON: SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION); any failure counts as "animations on".
# AFFECTS: Drawer slide and scrubber glide are skipped when this is True.
def reduced_motion():
    try:
        value = ctypes.c_int(1)
        ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(value), 0)
        return not value.value
    except (AttributeError, OSError):
        return False


# WHAT IT DOES: Holds the screen scale and the named fonts, sized like the mockup on this window's width.
# DEPENDS ON: A Tk root; width of the recovery window in screen pixels.
# AFFECTS: Fonts only. Sizes are negative (pixels) so Tk does not scale them a second time.
class Theme:
    def __init__(self, root, width=None):
        width = width or root.winfo_screenwidth()
        self.scale = max(0.55, min(width / DESIGN_WIDTH, 1.8))
        families = set(tkfont.families(root))
        mono = MONO if MONO in families else "Consolas"
        self.icons = ICONS if ICONS in families else "Segoe MDL2 Assets"
        self.display_family = DISPLAY if DISPLAY in families else BODY
        self.mono_family = mono
        self.cache = {}
        self.root = root

    # WHAT IT DOES: A design-pixel length on this screen.
    def px(self, value):
        return max(1, round(value * self.scale))

    # WHAT IT DOES: A cached font: kind is display, body, mono, icons or symbol; size in design pixels.
    def font(self, kind, size, bold=False):
        key = (kind, size, bold)
        if key not in self.cache:
            family = {"display": self.display_family, "body": BODY, "mono": self.mono_family,
                      "icons": self.icons, "symbol": SYMBOL}[kind]
            self.cache[key] = tkfont.Font(root=self.root, family=family, size=-self.px(size),
                                          weight="bold" if bold else "normal")
        return self.cache[key]

    # WHAT IT DOES: Dark ttk styling for the few ttk widgets left (tree list, scrollbar).
    # DEPENDS ON: The "clam" theme, which honours colours (the Windows theme does not).
    # AFFECTS: Only styles named Keeper.*; other ttk widgets keep their look.
    def style_ttk(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Keeper.Treeview", background=ROOM, fieldbackground=ROOM, foreground=INK,
                        bordercolor=EDGE, rowheight=self.px(44), font=self.font("body", 20))
        style.map("Keeper.Treeview", background=[("selected", OPEN_FILL)], foreground=[("selected", INK)])
        style.configure("Keeper.Treeview.Heading", background=BEZEL, foreground=DIM, bordercolor=EDGE,
                        relief="flat", font=self.font("display", 20))
        style.map("Keeper.Treeview.Heading", background=[("active", EDGE)])
        style.configure("Keeper.Vertical.TScrollbar", background=EDGE, troughcolor=ROOM, bordercolor=ROOM,
                        arrowcolor=DIM, lightcolor=EDGE, darkcolor=EDGE)
        return style


# Button kinds: (fill, text, border, hover fill).
KINDS = {"line": (BEZEL, INK, INK, HOVER_LINE), "go": (GO, BLACK, GO, HOVER_GO),
         "red": (STOP, BLACK, STOP, HOVER_STOP), "ghost": (BEZEL, DIM, BEZEL, EDGE),
         "seg": (ROOM, DIM, ROOM, BEZEL), "seg_on": (INK, ROOM, INK, INK)}
DISABLED = (BEZEL, DIM, EDGE)   # readable, but flat and without the bright border


# WHAT IT DOES: A flat dark button: optional icon + label, hover colour, keyboard focus ring, Space/Enter.
# DEPENDS ON: Theme fonts.
# AFFECTS: Calls command only while its state is "normal". cget("text"/"state") and ["state"] work like tk.Button.
class FlatButton(tk.Frame):
    def __init__(self, parent, theme, text, command=None, kind="line", icon=None, size=22, height=68,
                 width=None, state="normal"):
        self.theme, self.command, self.kind = theme, command, kind
        self._text, self._state, self._hover = text, state, False
        super().__init__(parent, background=EDGE, highlightthickness=theme.px(3), takefocus=1,
                         highlightcolor=INK, highlightbackground=parent.cget("background"), cursor="hand2")
        self.min_width = theme.px(width) if width else 0
        self.inner = tk.Frame(self, height=theme.px(height), width=self.min_width or 1)
        border = theme.px(2)
        self.inner.pack(fill="both", expand=True, padx=border, pady=border)
        self.inner.pack_propagate(False)
        row = tk.Frame(self.inner)
        row.place(relx=0.5, rely=0.5, anchor="center")
        self.row = row
        self.icon_label = tk.Label(row, text=icon or "", font=theme.font("icons", size * 0.8), padx=0)
        if icon:
            self.icon_label.pack(side="left", padx=(0, theme.px(10)))
        self.text_label = tk.Label(row, text=text, font=theme.font("display", size), padx=0)
        self.text_label.pack(side="left")
        self.parts = (self, self.inner, row, self.icon_label, self.text_label)
        for part in self.parts:
            part.bind("<Button-1>", self.invoke, add="+")
            part.bind("<Enter>", lambda event: self.set_hover(True), add="+")
            part.bind("<Leave>", lambda event: self.set_hover(False), add="+")
        self.bind("<space>", self.invoke)
        self.bind("<Return>", self.invoke)
        self.paint()
        self.fit_width()

    # WHAT IT DOES: Makes the button at least as wide as its icon and label plus padding.
    def fit_width(self, event=None):
        self.row.update_idletasks()
        need = max(self.row.winfo_reqwidth() + self.theme.px(52), self.min_width)
        if int(self.inner.cget("width")) != need:
            self.inner.configure(width=need)

    def set_hover(self, on):
        self._hover = on
        self.paint()

    # WHAT IT DOES: Applies the colours for the current kind, state and hover.
    def paint(self):
        if self._state == "disabled":
            fill, text, border = DISABLED
            cursor = "arrow"
        else:
            fill, text, border, hover = KINDS[self.kind]
            fill = hover if self._hover else fill
            cursor = "hand2"
        super().configure(background=border, cursor=cursor)
        for part in self.parts[1:]:
            part.configure(background=fill)
        self.icon_label.configure(foreground=text)
        self.text_label.configure(foreground=text)

    # WHAT IT DOES: Runs the command when enabled.
    def invoke(self, event=None):
        if self._state == "normal" and self.command:
            self.command()
        return "break"

    def cget(self, key):
        if key == "text":
            return self._text
        if key == "state":
            return self._state
        return super().cget(key)

    __getitem__ = cget

    # WHAT IT DOES: configure(text=, state=, kind=, icon=, command=) plus any Frame option.
    def configure(self, cnf=None, **options):
        if "text" in options:
            self._text = options.pop("text")
            self.text_label.configure(text=self._text)
            self.fit_width()
        if "icon" in options:
            self.icon_label.configure(text=options.pop("icon"))
        if "state" in options:
            self._state = str(options.pop("state"))
        if "kind" in options:
            self.kind = options.pop("kind")
        if "command" in options:
            self.command = options.pop("command")
        if cnf or options:
            super().configure(cnf, **options)
        self.paint()

    config = configure


# WHAT IT DOES: A dark label with the theme's font; a tiny helper so every screen uses the same type.
def label(parent, theme, text="", kind="body", size=20, colour=INK, bold=False, **options):
    return tk.Label(parent, text=text, font=theme.font(kind, size, bold), foreground=colour,
                    background=options.pop("background", parent.cget("background")), **options)


# WHAT IT DOES: Draws a square tick box as a PhotoImage: dark with a light edge, or green with a black tick.
def box_image(parent, size, ticked):
    image = tk.PhotoImage(master=parent, width=size, height=size)
    edge = max(2, size // 12)
    image.put(GO if ticked else INK, to=(0, 0, size, size))
    image.put(GO if ticked else TERM, to=(edge, edge, size - edge, size - edge))
    if ticked:
        pen = max(2, size // 9)
        for step in range(size):          # short stroke down-right, then long stroke up-right
            x = int(size * 0.22 + step * 0.6 * size / size)
            if x > size * 0.42:
                break
            y = int(size * 0.5 + (x - size * 0.22))
            image.put(BLACK, to=(x, y, x + pen, y + pen))
        x0, y0 = int(size * 0.42), int(size * 0.7)
        for step in range(int(size * 0.4)):
            image.put(BLACK, to=(x0 + step, y0 - step, x0 + step + pen, y0 - step + pen))
    return image


# WHAT IT DOES: A dark checkbox: Tk's own Checkbutton (so keyboard and the variable behave as before)
# with a large drawn box in place of Tk's tiny indicator.
def checkbox(parent, theme, text, variable, command=None, size=19):
    background = parent.cget("background")
    side = theme.px(28)
    off, on = box_image(parent, side, False), box_image(parent, side, True)
    box = tk.Checkbutton(parent, text=" " + text, variable=variable, command=command, font=theme.font("body", size),
                         foreground=INK, background=background, activeforeground=INK,
                         activebackground=background, selectcolor=background, highlightthickness=0,
                         borderwidth=0, cursor="hand2", anchor="w", indicatoron=False, image=off,
                         selectimage=on, compound="left", offrelief="flat", overrelief="flat", relief="flat")
    box.images = (off, on)
    return box
