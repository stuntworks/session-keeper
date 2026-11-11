# Last modified: 2026-10-26 | Change: Dark and light palettes side by side, with the contrast pairs for each | Full history: git log
"""The two colour palettes (dark, light) and the text/background pairs the contrast test checks for each.
keeper_theme picks one at import from the saved setting; nothing else should read these dicts directly."""

# Dark: the original look (from WORK/2026-10-21 frontend-design/recovery_screen.html). Values unchanged.
DARK = {
    "ROOM": "#0E1217", "BEZEL": "#1A1F27", "EDGE": "#2A313C",
    "MONITOR_EDGE": "#8C96A8",         # monitor frame outline: light enough to read as hardware on the room
    "TERM": "#0C0C0C", "STRIP": "#1F1F1F",
    "INK": "#E6E9EF", "DIM": "#C2C9D5",  # Dim 7:1+ on every surface (owner: brighter lettering)
    "AMBER": "#F5A524", "GO": "#3DDC84", "STEEL": "#5B6575", "STOP": "#F36D6A",
    "GO_TEXT": "#3DDC84",              # green used as lettering (on the room or a panel)
    "CLAUDE": "#D97757",
    "OPEN_FILL": "#2B323D",            # open tabs keep a steel-dark fill; their text stays bright
    "HOVER_LINE": "#232933", "HOVER_GO": "#5BE69A", "HOVER_STOP": "#F78784",
    "BLACK": "#000000",                # lettering on a green fill
    "ON_STOP": "#000000",              # lettering on a red fill
    "TINTS": ("#152029", "#1D1B2E"),   # monitor screens: a cool teal slate and an indigo slate
    "TASKBAR": "#0F1319",
    "OUTLINE": "#C85A74",              # burgundy edge round every saved terminal window
    "MONITOR_BODY": "#1A1F27",         # monitor bezel and stand foot
    "BEZEL_LIT": "#3A4351", "CHIN": "#141920", "RECESS": "#05070A", "SHADOW": "#07090C",
    "CHIN_INK": "#E6E9EF",             # the monitor name on its chin
    "POWER": "#2F9E63",
    # Desktop wallpaper is squeezed into this range so text and outlines on it stay readable.
    "WALL_FLOOR": "#07090C", "WALL_CEIL": "#2A3340",
    "WALL_SHADE": "#000000",           # the stand-in wallpaper's darker end is blended toward this
    "WALL_WORST": "#2A3340",           # the wallpaper pixel text contrasts least with (the brightest here)
    # The HOW TO USE box: fill, border, step text, step numbers, and the fill behind its title.
    "GUIDE_FILL": "#1A1F27", "GUIDE_EDGE": "#3DDC84", "GUIDE_INK": "#E6E9EF", "GUIDE_MARK": "#3DDC84",
    "GUIDE_TAG": "#1A1F27",
}

# Light: the owner's reference (2026-10-26): near-white header and panels, black type, a solid green guide band,
# dark monitor hardware round a pale blue-grey screen, cream terminal windows with an amber edge.
# Accents are tuned so every pair below passes the same thresholds as dark.
LIGHT = {
    "ROOM": "#F4F4F1", "BEZEL": "#FCFCFA", "EDGE": "#C5C9D0",
    "MONITOR_EDGE": "#2C3240",
    "TERM": "#FDF6E8", "STRIP": "#F7EDD8",   # cream "paper" windows, a slightly deeper cream tab strip
    "INK": "#1B1D22", "DIM": "#383D47",
    "AMBER": "#794400", "GO": "#22C063", "STEEL": "#8A93A2", "STOP": "#A3170F",
    "GO_TEXT": "#0F6B35",
    "CLAUDE": "#A3461C",
    "OPEN_FILL": "#E6E9EE",
    "HOVER_LINE": "#ECEEF2", "HOVER_GO": "#3FD27A", "HOVER_STOP": "#85120B",
    "BLACK": "#000000",
    "ON_STOP": "#FFFFFF",
    "TINTS": ("#D5DEEA", "#DEDAEA"),   # pale blue-grey and pale lilac-grey screens
    "TASKBAR": "#C9D2DF",
    "OUTLINE": "#A86200",              # amber-orange edge round every saved terminal window
    "MONITOR_BODY": "#1F2430",
    "BEZEL_LIT": "#3A4252", "CHIN": "#1A1E28", "RECESS": "#11141A", "SHADOW": "#D6D9DF",
    "CHIN_INK": "#D3D8E1",
    "POWER": "#2F9E63",
    "WALL_FLOOR": "#D3D9E2", "WALL_CEIL": "#F0F3F7",
    "WALL_SHADE": "#D3D9E2",
    "WALL_WORST": "#D3D9E2",           # dark text: the darkest wallpaper pixel is the hard one
    "GUIDE_FILL": "#22C063", "GUIDE_EDGE": "#168F48", "GUIDE_INK": "#0B1A10", "GUIDE_MARK": "#0B1A10",
    "GUIDE_TAG": "#15191E",
}

PALETTES = {"dark": DARK, "light": LIGHT}


# WHAT IT DOES: Every text colour drawn on every background it sits on, for one palette.
# DEPENDS ON: A palette dict above.
# AFFECTS: Nothing; the theme test checks each pair (INK and "black"/"on" pairs at 7:1, the rest at 4.5:1).
def text_pairs(p):
    ink, dim, amber = p["INK"], p["DIM"], p["AMBER"]
    pairs = {
        "ink on room": (ink, p["ROOM"]), "ink on bezel": (ink, p["BEZEL"]), "ink on terminal": (ink, p["TERM"]),
        "ink on tab strip": (ink, p["STRIP"]), "ink on open tab": (ink, p["OPEN_FILL"]), "ink on edge": (ink, p["EDGE"]),
        "dim on room": (dim, p["ROOM"]), "dim on bezel": (dim, p["BEZEL"]), "dim on tab strip": (dim, p["STRIP"]),
        "dim on terminal": (dim, p["TERM"]), "amber on room": (amber, p["ROOM"]), "amber on bezel": (amber, p["BEZEL"]),
        "amber on terminal": (amber, p["TERM"]), "amber on tab strip": (amber, p["STRIP"]),
        "go on bezel": (p["GO_TEXT"], p["BEZEL"]), "go on room": (p["GO_TEXT"], p["ROOM"]),
        "go on terminal": (p["GO_TEXT"], p["TERM"]),
        "black on go": (p["BLACK"], p["GO"]), "black on stop": (p["ON_STOP"], p["STOP"]),
        "room on ink": (p["ROOM"], ink),
        "black on hover go": (p["BLACK"], p["HOVER_GO"]), "black on hover stop": (p["ON_STOP"], p["HOVER_STOP"]),
        "ink on hover": (ink, p["HOVER_LINE"]), "stop on bezel": (p["STOP"], p["BEZEL"]),
        "stop on room": (p["STOP"], p["ROOM"]), "claude mark on strip": (p["CLAUDE"], p["STRIP"]),
        "claude mark on bezel": (p["CLAUDE"], p["BEZEL"]), "dim on open tab": (dim, p["OPEN_FILL"]),
        "ink on taskbar": (ink, p["TASKBAR"]), "dim on taskbar": (dim, p["TASKBAR"]),
        "amber on taskbar": (amber, p["TASKBAR"]),
        "ink on hardest wallpaper pixel": (ink, p["WALL_WORST"]), "dim on hardest wallpaper pixel": (dim, p["WALL_WORST"]),
        "amber on hardest wallpaper pixel": (amber, p["WALL_WORST"]), "ink on chin": (p["CHIN_INK"], p["CHIN"]),
        "black on guide": (p["GUIDE_INK"], p["GUIDE_FILL"]), "guide number on guide": (p["GUIDE_MARK"], p["GUIDE_FILL"]),
        "go on guide title": (p["GO"], p["GUIDE_TAG"]),
    }
    for tint in p["TINTS"]:
        pairs.update({f"ink on {tint}": (ink, tint), f"dim on {tint}": (dim, tint), f"amber on {tint}": (amber, tint)})
    return pairs


# WHAT IT DOES: The terminal-window outline on every surface it is drawn on, for one palette (3:1 non-text).
def outline_pairs(p):
    line = p["OUTLINE"]
    pairs = {"outline on room": (line, p["ROOM"]), "outline on terminal": (line, p["TERM"]),
             "outline on tab strip": (line, p["STRIP"]), "outline on hardest wallpaper pixel": (line, p["WALL_WORST"])}
    pairs.update({f"outline on {tint}": (line, tint) for tint in p["TINTS"]})
    return pairs
