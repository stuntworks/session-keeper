# Last modified: 2026-10-22 | Change: Occupancy tests take a throwaway restore lock | Full history: git log
"""Fakes only: no window is closed and no process is inspected for real."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import keeper_occupancy
from keeper_core import plan_restore
from keeper_occupancy import EMPTY, LIVE, UNKNOWN, classify_window, close_empty_spot, read_shells
from keeper_restore import restore_selected

ID1 = "11111111-1111-1111-1111-111111111111"
ID2 = "22222222-2222-2222-2222-222222222222"
SPOT = {"x": 100, "y": 50, "w": 1600, "h": 900}


# WHAT IT DOES: Builds one live window as match_inventory reports it.
# DEPENDS ON: Nothing.
# AFFECTS: Test data only.
def group(hwnd, titles, matched=None):
    tabs = [{"strippedTitle": t, "outcome": "NONE"} for t in titles]
    if matched:
        tabs.append({"strippedTitle": "chat", "outcome": "MATCHED", "sessionId": matched})
    return {"hwnd": hwnd, **SPOT, "tabs": tabs}


# WHAT IT DOES: Builds one tab process as read_shells reports it.
# DEPENDS ON: Nothing.
# AFFECTS: Test data only.
def shell(title, bare=True):
    return {"pid": 1, "name": "cmd.exe", "title": title, "bare": bare}


# WHAT IT DOES: Builds a saved one-window layout with two chats.
# DEPENDS ON: Nothing.
# AFFECTS: Test data only.
def saved():
    return {"captured_at": "T1", "windows": [{**SPOT, "tabs": [
        {"outcome": "MATCHED", "sessionId": ID1, "strippedTitle": "First", "cwd": "C:\\Users\\example"},
        {"outcome": "MATCHED", "sessionId": ID2, "strippedTitle": "Second", "cwd": "C:\\Users\\example"}]}]}


# WHAT IT DOES: LIVE / EMPTY / UNKNOWN decisions, failing closed.
# DEPENDS ON: classify_window.
# AFFECTS: Only EMPTY windows can ever be offered for closing.
class ClassifyTests(unittest.TestCase):
    def test_window_with_a_verified_chat_is_live(self):
        window = group(9, ["PowerShell"], matched=ID1)
        self.assertEqual(classify_window(window, [window], [shell("PowerShell")]), LIVE)

    def test_all_bare_shells_is_empty(self):
        window = group(9, ["PowerShell", "Command Prompt"])
        shells = [shell("PowerShell"), shell("Command Prompt")]
        self.assertEqual(classify_window(window, [window], shells), EMPTY)

    def test_shell_running_a_program_is_not_empty(self):
        window = group(9, ["PowerShell"])
        self.assertEqual(classify_window(window, [window], [shell("PowerShell", bare=False)]), UNKNOWN)

    def test_title_pairing_must_be_exact_count(self):
        first, second = group(9, ["PowerShell"]), group(10, ["PowerShell"])
        self.assertEqual(classify_window(first, [first, second], [shell("PowerShell")]), UNKNOWN)
        self.assertEqual(classify_window(first, [first, second], [shell("PowerShell"), shell("PowerShell")]), EMPTY)

    def test_unreadable_or_unmatched_is_unknown(self):
        window = group(9, ["Settings"])
        self.assertEqual(classify_window(window, [window], [shell("PowerShell")]), UNKNOWN)
        self.assertEqual(classify_window(window, [window], None), UNKNOWN)
        self.assertEqual(classify_window(group(9, []), [], []), UNKNOWN)


# WHAT IT DOES: Reads tab processes from a fake Windows Terminal process tree.
# DEPENDS ON: Patched psutil and console-title probe.
# AFFECTS: Proves a shell with a child program is not bare and an unreadable title fails closed.
class ReadShellsTests(unittest.TestCase):
    def proc(self, pid, name, parent=None, children=()):
        node = SimpleNamespace(pid=pid, info={"pid": pid, "name": name})
        node.name = lambda: name
        node.parent = lambda: parent
        node.children = lambda recursive=False: list(children)
        return node

    def tree(self):
        host = self.proc(1, "WindowsTerminal.exe")
        bare = self.proc(2, "cmd.exe", host, [self.proc(5, "conhost.exe")])
        busy = self.proc(3, "cmd.exe", host, [self.proc(6, "claude.exe")])
        console = self.proc(4, "OpenConsole.exe", host)
        host.children = lambda recursive=False: [bare, busy, console]
        return [host, bare, busy, console]

    def test_bare_and_busy_shells(self):
        with patch("psutil.process_iter", return_value=self.tree()), \
             patch("keeper_codex.console_title", side_effect=lambda pid: f"tab{pid}"):
            shells = read_shells()
        self.assertEqual([(s["pid"], s["bare"]) for s in shells], [(2, True), (3, False)])

    def test_unreadable_title_returns_none(self):
        with patch("psutil.process_iter", return_value=self.tree()), \
             patch("keeper_codex.console_title", return_value=None):
            self.assertIsNone(read_shells())


# WHAT IT DOES: Closing happens only for approved, freshly re-verified EMPTY windows.
# DEPENDS ON: close_empty_spot with every probe faked.
# AFFECTS: Proves exact-HWND closing, re-verification and the live-chat check.
class CloseSpotTests(unittest.TestCase):
    def run_close(self, hwnds, approved, groups, shells, closes=True, live=({ID1}, {ID1})):
        screen = {"now": [{"hwnd": g["hwnd"], **SPOT, "tabs": [t["strippedTitle"] for t in g["tabs"]]} for g in groups]}
        live_answers = iter(live)

        def close(hwnd):
            if closes:
                screen["now"] = [w for w in screen["now"] if w["hwnd"] != hwnd]
        with patch.object(keeper_occupancy, "close_window", side_effect=close) as closer, \
             patch.object(keeper_occupancy, "wait_closed", side_effect=lambda hwnd: closes):
            result = close_empty_spot(hwnds, approved, lambda: screen["now"], lambda windows: groups,
                                      lambda: shells, lambda: next(live_answers))
        return result, closer

    def test_not_approved_never_closes(self):
        (freed, reason), closer = self.run_close([9], set(), [group(9, ["PowerShell"])], [shell("PowerShell")])
        self.assertFalse(freed)
        closer.assert_not_called()

    def test_no_longer_empty_is_left_alone(self):
        (freed, reason), closer = self.run_close([9], {9}, [group(9, ["PowerShell"])], [shell("PowerShell", False)])
        self.assertFalse(freed)
        self.assertIn("no longer looks empty", reason)
        closer.assert_not_called()

    def test_approved_empty_window_is_closed_by_exact_handle(self):
        (freed, reason), closer = self.run_close([9], {9}, [group(9, ["PowerShell"])], [shell("PowerShell")])
        self.assertTrue(freed)
        closer.assert_called_once_with(9)

    def test_window_that_stays_open_skips_the_spot(self):
        (freed, reason), closer = self.run_close([9], {9}, [group(9, ["PowerShell"])], [shell("PowerShell")],
                                                 closes=False)
        self.assertFalse(freed)
        self.assertIn("did not close", reason)

    def test_losing_a_live_chat_stops_everything(self):
        with self.assertRaises(RuntimeError):
            self.run_close([9], {9}, [group(9, ["PowerShell"])], [shell("PowerShell")], live=({ID1}, set()))


# WHAT IT DOES: The planner honours explicit choices and otherwise reports blocked spots.
# DEPENDS ON: plan_restore.
# AFFECTS: Nothing stacks or closes without a user choice.
class PlanChoiceTests(unittest.TestCase):
    covering = [{"hwnd": 9, **SPOT, "tabs": ["PowerShell"]}]

    # Owner 2026-10-22: an occupied spot no longer blocks; the window opens on top and says so.
    def test_no_choice_opens_on_top_and_reports_blocked(self):
        blocked = []
        plans, warnings = plan_restore(saved(), {"0:0"}, set(), self.covering, blocked=blocked)
        self.assertTrue(plans[0]["on_top"])
        self.assertIn("PowerShell", plans[0]["covering"])
        self.assertNotIn("close_hwnds", plans[0])
        self.assertEqual(blocked[0]["occupants"][0]["hwnd"], 9)

    def test_open_on_top_only_when_chosen(self):
        plans, _ = plan_restore(saved(), {"0:0"}, set(), self.covering, choices={0: "on_top"})
        self.assertTrue(plans[0]["on_top"])

    def test_close_empty_names_exact_handles(self):
        plans, _ = plan_restore(saved(), {"0:0"}, set(), self.covering, choices={0: "close_empty"})
        self.assertEqual(plans[0]["close_hwnds"], [9])


# WHAT IT DOES: Full restore loop with an empty leftover terminal on the saved spot.
# DEPENDS ON: restore_selected with every system call faked.
# AFFECTS: Proves auto/no-choice never closes, Preview describes, and approved close then opens.
class RestoreWithEmptyTerminalTests(unittest.TestCase):
    def run_restore(self, dry_run=False, choices=None, approved=()):
        screen = {"now": [{"hwnd": 9, **SPOT, "tabs": ["PowerShell"]}]}
        groups = [group(9, ["PowerShell"])]

        def close(hwnd):
            screen["now"] = []
        with patch("keeper_restore.live_session_ids", return_value=set()), \
             patch("keeper_restore.read_windows", side_effect=lambda: screen["now"]), \
             patch("keeper_restore.read_live_groups", return_value=groups), \
             patch("keeper_restore.read_monitors", return_value=[{"x": 0, "y": 0, "w": 3840, "h": 2088, "primary": True}]), \
             patch("keeper_restore.load_created_windows", return_value=[]), \
             patch("keeper_restore.read_boot_id", return_value="B"), \
             patch("keeper_occupancy.read_shells", return_value=[shell("PowerShell")]), \
             patch.object(keeper_occupancy, "close_window", side_effect=close) as closer, \
             patch.object(keeper_occupancy, "wait_closed", return_value=True), \
             patch("keeper_restore.launch_tab") as launch, \
             patch("keeper_restore.find_new_window", return_value={"hwnd": 500}), \
             patch("keeper_restore.record_created_window"), \
             patch("keeper_restore.place_new_window"), \
             patch("keeper_restore.wait_for_session", return_value=True), \
             patch("keeper_restore.time.sleep"):
            result = restore_selected(saved(), {"0:0", "0:1"}, dry_run, choices, approved,
                                      lock_name="unittest-restore")
        return result, closer, launch

    def test_without_a_choice_nothing_is_closed_and_it_opens_on_top(self):
        (restored, warnings), closer, launch = self.run_restore()
        closer.assert_not_called()
        self.assertEqual(restored, ["First", "Second"])
        self.assertTrue(any("on top of" in w and "PowerShell" in w and "drag it where you want" in w for w in warnings))

    def test_preview_describes_and_changes_nothing(self):
        (restored, warnings), closer, launch = self.run_restore(dry_run=True)
        closer.assert_not_called()
        launch.assert_not_called()
        self.assertTrue(any("ask before closing" in w for w in warnings))

    def test_approved_close_then_restores_in_place(self):
        (restored, warnings), closer, launch = self.run_restore(choices={0: "close_empty"}, approved={9})
        closer.assert_called_once_with(9)
        self.assertEqual(restored, ["First", "Second"])

    def test_open_on_top_keeps_the_other_window(self):
        (restored, warnings), closer, launch = self.run_restore(choices={0: "on_top"})
        closer.assert_not_called()
        self.assertEqual(restored, ["First", "Second"])


if __name__ == "__main__":
    unittest.main()
