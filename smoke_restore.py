# Last modified: 2026-10-03 | Change: Test real grouped windows using self-closing disposable shells | Full history: git log
"""No agent conversation is opened; the two test shells exit by themselves."""

import subprocess
from unittest.mock import patch

from keeper_core import read_windows, rectangles_overlap
from keeper_display import read_monitors
from keeper_restore import restore_selected


# WHAT IT DOES: Finds a visible test position that does not cover an existing terminal.
# DEPENDS ON: Current monitor work areas and terminal rectangles.
# AFFECTS: The smoke test refuses to run if it cannot find a free location.
def choose_test_rectangle():
    windows = read_windows()
    for monitor in reversed(read_monitors()):
        for offset_y in (50, 650, 1250):
            for offset_x in (50, 850, 1650, 2450):
                rectangle = {"x": monitor["x"] + offset_x, "y": monitor["y"] + offset_y, "w": 760, "h": 480}
                if (offset_x + 760 <= monitor["w"] and offset_y + 480 <= monitor["h"]
                        and not any(rectangles_overlap(rectangle, window) for window in windows)):
                    return rectangle
    raise RuntimeError("No free terminal test position is available")


# WHAT IT DOES: Substitutes a self-closing shell for the agent CLI during this one test.
# DEPENDS ON: Windows Terminal and PowerShell.
# AFFECTS: Two disposable tabs remain visible for at most twenty seconds.
def launch_probe(window_name, row):
    subprocess.Popen(["wt.exe", "-w", window_name, "new-tab", "--title", row["title"],
        "powershell.exe", "-NoProfile", "-Command", "Start-Sleep -Seconds 20"],
        creationflags=subprocess.CREATE_NO_WINDOW)


# WHAT IT DOES: Verifies the real restore engine groups and positions two disposable tabs.
# DEPENDS ON: Real terminal APIs; only agent launch and session-ready checks are substituted.
# AFFECTS: Proves window behavior without reading or resuming any saved conversation.
def main():
    rectangle = choose_test_rectangle()
    previous = {window["hwnd"] for window in read_windows()}
    titles = ["Session Keeper smoke A", "Session Keeper smoke B"]
    tabs = [{"outcome": "MATCHED", "kind": "claude", "strippedTitle": title,
        "sessionId": str(index + 1) * 8 + "-1111-1111-1111-111111111111", "cwd": "C:\\Users\\example"}
        for index, title in enumerate(titles)]
    snapshot = {"windows": [{**rectangle, "tabs": tabs}]}
    with patch("keeper_restore.live_session_ids", return_value=set()), \
         patch("keeper_restore.launch_tab", side_effect=launch_probe), \
         patch("keeper_restore.wait_for_session", return_value=True):
        restored, warnings = restore_selected(snapshot, {"0:0", "0:1"})
    created = [window for window in read_windows() if window["hwnd"] not in previous]
    assert restored == titles, (restored, warnings)
    assert not warnings, warnings
    assert len(created) == 1, created
    assert created[0]["tabs"] == titles, created[0]
    assert all(abs(created[0][key] - value) <= 2 for key, value in rectangle.items()), created[0]
    print({"result": "PASS", "one_window": True, "tab_order": created[0]["tabs"],
           "rectangle": rectangle, "agent_history_resumed": False})


if __name__ == "__main__":
    main()
