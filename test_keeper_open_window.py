# Last modified: 2026-10-22 | Change: Open-window tests take a throwaway restore lock | Full history: git log
"""Fakes only: no terminal is opened, focused, or closed."""

import unittest
from unittest.mock import patch

from keeper_core import plan_restore
from keeper_restore import restore_selected

IDS = [f"{n}{n}{n}{n}{n}{n}{n}{n}-{n}{n}{n}{n}-{n}{n}{n}{n}-{n}{n}{n}{n}-{n}{n}{n}{n}{n}{n}{n}{n}{n}{n}{n}{n}"
       for n in range(1, 7)]
TITLES = ["red", "blue", "yellow", "purple", "green"]
RECT = {"x": 100, "y": 50, "w": 1600, "h": 900}


# WHAT IT DOES: Builds one saved five-tab window; the fifth tab is the one that was closed.
# DEPENDS ON: Nothing.
# AFFECTS: Test data only.
def saved():
    return {"captured_at": "T1", "windows": [{"hwnd": 500, **RECT, "tabs": [
        {"outcome": "MATCHED", "sessionId": IDS[i], "strippedTitle": t, "cwd": "C:\\Users\\example"}
        for i, t in enumerate(TITLES)]}]}


# WHAT IT DOES: Describes one live terminal window as the read-only probe reports it.
# DEPENDS ON: Nothing.
# AFFECTS: Test data only.
def probe_window(hwnd, titles, rect=RECT):
    return {"hwnd": hwnd, **rect, "title": titles[0] if titles else "", "tabs": list(titles)}


# WHAT IT DOES: Describes the same live window with tab identities matched to sessions.
# DEPENDS ON: Nothing.
# AFFECTS: Test data only.
def live_group(hwnd, ids, rect=RECT, extra=()):
    tabs = [{"strippedTitle": TITLES[IDS.index(i)], "outcome": "MATCHED", "sessionId": i} for i in ids]
    tabs += [{"strippedTitle": t, "outcome": "NONE"} for t in extra]
    return {"hwnd": hwnd, **rect, "tabs": tabs}


FOUR = IDS[:4]


# WHAT IT DOES: The live failure: a tab closed while its window stayed open with four siblings.
# DEPENDS ON: plan_restore with fake live windows.
# AFFECTS: Without the fix Preview said "Skipped occupied position: green".
class PlanAddToOpenWindowTests(unittest.TestCase):
    def test_missing_tab_goes_back_into_its_open_window(self):
        plans, warnings = plan_restore(saved(), {"0:4"}, set(FOUR), [probe_window(500, TITLES[:4])],
                                       live_groups=[live_group(500, FOUR)])
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["mode"], "add")
        self.assertEqual(plans[0]["target_hwnd"], 500)
        self.assertEqual([r["title"] for r in plans[0]["tabs"]], ["green"])
        self.assertFalse(any("occupied" in w for w in warnings))

    def test_home_window_found_by_session_ids_even_after_it_moved(self):
        moved = {"x": 3000, "y": 400, "w": 1600, "h": 900}
        plans, _ = plan_restore(saved(), {"0:4"}, set(FOUR), [probe_window(600, TITLES[:4], moved)],
                                live_groups=[live_group(600, FOUR, moved)])
        self.assertEqual(plans[0]["target_hwnd"], 600)

    def test_user_added_shell_tab_does_not_block_home_window(self):
        plans, _ = plan_restore(saved(), {"0:4"}, set(FOUR), [probe_window(500, TITLES[:4] + ["PowerShell"])],
                                live_groups=[live_group(500, FOUR, extra=("PowerShell",))])
        self.assertEqual(plans[0]["mode"], "add")

    # Owner 2026-10-22: a foreign window no longer blocks; ours opens on top of it, never into it.
    def test_foreign_window_on_the_spot_opens_on_top(self):
        plans, warnings = plan_restore(saved(), {"0:4"}, set(), [probe_window(900, ["Unrelated"])],
                                       live_groups=[live_group(900, [], extra=("Unrelated",))])
        self.assertTrue(plans[0]["on_top"])
        self.assertNotEqual(plans[0].get("mode"), "add")
        self.assertIn("Unrelated", plans[0]["covering"])

    def test_siblings_not_verifiable_by_id_opens_on_top_never_into_it(self):
        unidentified = {"hwnd": 500, **RECT, "tabs": [{"strippedTitle": t, "outcome": "AMBIGUOUS"} for t in TITLES[:4]]}
        plans, warnings = plan_restore(saved(), {"0:4"}, set(FOUR), [probe_window(500, TITLES[:4])],
                                       live_groups=[unidentified])
        self.assertTrue(plans[0]["on_top"])
        self.assertNotIn("target_hwnd", plans[0])

    def test_siblings_split_across_windows_is_not_guessed(self):
        other = {"x": 3000, "y": 400, "w": 1600, "h": 900}
        plans, warnings = plan_restore(saved(), {"0:4"}, set(FOUR),
                                       [probe_window(500, TITLES[:2]), probe_window(600, TITLES[2:4], other)],
                                       live_groups=[live_group(500, FOUR[:2]), live_group(600, FOUR[2:], other)])
        self.assertEqual(plans, [])
        self.assertTrue(any("more than one window" in w for w in warnings))

    def test_window_gone_opens_new_window_at_saved_spot(self):
        plans, warnings = plan_restore(saved(), {"0:3", "0:4"}, set(), [], live_groups=[])
        self.assertEqual(len(plans), 1)
        self.assertNotIn("mode", plans[0])
        self.assertEqual(plans[0]["rect"], RECT)

    def test_never_duplicates_a_live_session(self):
        plans, warnings = plan_restore(saved(), {"0:3", "0:4"}, set(FOUR), [probe_window(500, TITLES[:4])],
                                       live_groups=[live_group(500, FOUR)])
        self.assertEqual([r["title"] for r in plans[0]["tabs"]], ["green"])
        self.assertTrue(any("already open" in w and "purple" in w for w in warnings))

    def test_live_windows_are_not_read_when_no_sibling_is_live(self):
        def explode():
            raise AssertionError("live window identities were read without need")
        plans, _ = plan_restore(saved(), {"0:4"}, set(), [], live_groups=explode)
        self.assertEqual(len(plans), 1)


# WHAT IT DOES: Runs the restore loop for an add-to-open plan with every system call faked.
# DEPENDS ON: perform_restore, keeper_target, and patched probes.
# AFFECTS: Proves the tab is launched into, and verified inside, the exact window.
class RestoreAddToOpenWindowTests(unittest.TestCase):
    # WHAT IT DOES: Runs restore_selected against a fake screen that changes only when a tab is launched.
    # DEPENDS ON: before = screen now; after = screen once the launch lands.
    # AFFECTS: Test only.
    def run_restore(self, before, after=None, owned=(), front=True, dry_run=False, keys=("0:4",),
                    live_ids=None, groups=None):
        screen = {"now": before}

        def launched(name, row):
            screen["now"] = after if after is not None else screen["now"]
        live = set(FOUR) if live_ids is None else live_ids
        with patch("keeper_restore.live_session_ids", return_value=live),              patch("keeper_restore.read_windows", side_effect=lambda: screen["now"]),              patch("keeper_restore.read_live_groups", return_value=groups or [live_group(500, FOUR)]),              patch("keeper_restore.read_monitors", return_value=[{"x": 0, "y": 0, "w": 3840, "h": 2088, "primary": True}]),              patch("keeper_restore.load_created_windows", return_value=list(owned)),              patch("keeper_restore.read_boot_id", return_value="B"),              patch("keeper_restore.launch_tab", side_effect=launched) as launch,              patch("keeper_restore.find_new_window") as find,              patch("keeper_restore.place_new_window") as place,              patch("keeper_target.bring_to_front", return_value=front) as focus,              patch("keeper_restore.wait_for_session", return_value=True),              patch("keeper_restore.time.sleep"):
            restored, warnings = restore_selected(saved(), set(keys), dry_run, lock_name="unittest-restore")
        find.assert_not_called()
        place.assert_not_called()
        return restored, warnings, launch, focus

    def test_preview_says_it_adds_to_its_open_window(self):
        restored, warnings, launch, focus = self.run_restore([probe_window(500, TITLES[:4])], dry_run=True)
        self.assertEqual(restored, ["Would add to its open window: green"])
        launch.assert_not_called()
        focus.assert_not_called()

    def test_window_session_keeper_made_is_targeted_by_name(self):
        owned = [{"window_name": "SessionKeeper-abc", "hwnd": 500, "group": "T0#3", "boot_id": "B"}]
        restored, warnings, launch, focus = self.run_restore(
            [probe_window(500, TITLES[:4])], [probe_window(500, TITLES)], owned=owned)
        self.assertEqual(restored, ["green"])
        self.assertEqual(launch.call_args.args[0], "SessionKeeper-abc")
        focus.assert_not_called()

    def test_other_window_is_focused_then_verified(self):
        restored, warnings, launch, focus = self.run_restore(
            [probe_window(500, TITLES[:4])], [probe_window(500, TITLES)])
        self.assertEqual(restored, ["green"])
        self.assertEqual(launch.call_args.args[0], "0")
        self.assertEqual(focus.call_args.args[0], 500)

    def test_tab_landing_elsewhere_stops_without_retry(self):
        elsewhere = {"x": 3000, "y": 0, "w": 900, "h": 700}
        before = [probe_window(500, TITLES[:3]), probe_window(700, ["x"], elsewhere)]
        wrong = [probe_window(500, TITLES[:3]), probe_window(700, ["x", "purple"], elsewhere)]
        restored, warnings, launch, focus = self.run_restore(
            before, wrong, keys=("0:3", "0:4"), live_ids=set(IDS[:3]), groups=[live_group(500, IDS[:3])])
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(restored, [])
        self.assertTrue(any("not in its own window" in w for w in warnings))
        self.assertTrue(any("Not added" in w and "green" in w for w in warnings))

    def test_focus_refused_launches_nothing(self):
        restored, warnings, launch, focus = self.run_restore([probe_window(500, TITLES[:4])], front=False)
        launch.assert_not_called()
        self.assertTrue(any("could not bring" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
