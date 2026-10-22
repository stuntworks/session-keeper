# Last modified: 2026-10-19 | Change: Find a saved group's still-open window by exact session IDs | Full history: git log
"""Pure decisions about where a missing tab belongs. No window is read, focused or moved here."""

SPLIT = "split"
MINIMISED_AT = -30000


# WHAT IT DOES: Finds the one live window that holds every live sibling of a saved group.
# DEPENDS ON: Saved session IDs, the verified live ID set, and live windows whose tabs carry matched session IDs.
# AFFECTS: A missing tab is added to this window instead of being skipped or opened elsewhere.
# DANGER ZONE HIGH: Identity is exact session ID only / breaks: tabs land in a stranger's window / depends on: MATCHED tabs.
def home_window(saved_ids, live_ids, live_groups):
    live_siblings = set(saved_ids) & set(live_ids)
    if not live_siblings:
        return None
    holders = []
    for window in live_groups:
        held = {tab.get("sessionId") for tab in window.get("tabs", [])
                if tab.get("outcome") == "MATCHED" and tab.get("sessionId")}
        if held & live_siblings:
            holders.append((window, held))
    if len(holders) > 1:
        return SPLIT
    if len(holders) == 1 and live_siblings <= holders[0][1] and holders[0][0].get("hwnd"):
        return holders[0][0]
    return None


# WHAT IT DOES: Says whether Windows recorded a window as minimised (parked at -32000, -32000).
# DEPENDS ON: Saved or live x, y values.
# AFFECTS: Minimised windows are shown in their own strip and restored to a visible spot.
def is_minimised(rect):
    x, y = rect.get("x"), rect.get("y")
    return type(x) is int and type(y) is int and (x <= MINIMISED_AT or y <= MINIMISED_AT)


# WHAT IT DOES: Picks a visible, ordinary-size spot on the primary monitor for a minimised window.
# DEPENDS ON: Current monitor work areas.
# AFFECTS: A minimised window is reopened where the user can see it.
def minimised_rect(monitors):
    if not monitors:
        return None
    monitor = next((item for item in monitors if item.get("primary")), monitors[0])
    return {"x": monitor["x"] + 60, "y": monitor["y"] + 60,
            "w": max(200, min(1600, monitor["w"] - 120)), "h": max(150, min(900, monitor["h"] - 120))}
