# Last modified: 2026-10-22 | Change: Unidentified-restore tests take a throwaway lock | Full history: git log
"""Live restore test 2026-10-22: one unidentified Claude (no pid record) paused every manual restore.

A clicked restore may pass an unidentified process only when its own launch command proves it cannot be
any selected conversation, or when the user clicks "Restore anyway". Login auto-restore stays fully strict."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import keeper_restore
from keeper_restore import LiveCheck, UnidentifiedSessions, live_session_ids, restore_selected

SAVED = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"
KNOWN = "33333333-3333-3333-3333-333333333333"
ROWS = [{"session_id": SAVED, "cwd": "C:\\Work\\Outreach", "title": "Outreach"}]


# WHAT IT DOES: One unidentified interactive Claude process, as live_inventory reports it.
# DEPENDS ON: Nothing.
# AFFECTS: Test data only.
def unknown(command, folder="C:\\Work\\Other", title="Some tab"):
    return {"kind": "claude", "pid": 4242, "sessionId": None, "role": "interactive", "uncertain": True,
            "title": title, "command": command, "processCwd": folder}


KNOWN_ROW = {"kind": "claude", "pid": 1, "sessionId": KNOWN, "role": "interactive", "uncertain": False}


# WHAT IT DOES: Runs live_session_ids against a fixed fake inventory with no waiting.
# DEPENDS ON: keeper_inventory.live_inventory being the only process source.
# AFFECTS: Nothing outside the test.
def ids(rows, strict):
    with patch("keeper_inventory.live_inventory", return_value=rows), \
         patch.object(keeper_restore, "STRICT_WAIT_SECONDS", 0), \
         patch.object(keeper_restore, "STRICT_POLL_SECONDS", 0):
        return live_session_ids(strict=strict)


class ProofTests(unittest.TestCase):
    # WHAT IT DOES: A plain `claude` started a NEW conversation, so it cannot be the saved one.
    def test_plain_new_claude_does_not_block_a_clicked_restore(self):
        self.assertEqual(ids([KNOWN_ROW, unknown(["claude.exe"])], LiveCheck(ROWS)), {KNOWN})

    def test_model_flag_alone_is_still_new(self):
        self.assertEqual(ids([unknown(["claude.exe", "--model", "x"])], LiveCheck(ROWS)), set())

    # WHAT IT DOES: --continue only reopens a chat from its own folder; a different folder is proof.
    def test_continue_in_another_folder_does_not_block(self):
        self.assertEqual(ids([unknown(["claude.exe", "--model", "m", "--continue"])], LiveCheck(ROWS)), set())

    def test_continue_in_the_saved_folder_blocks(self):
        with self.assertRaises(UnidentifiedSessions):
            ids([unknown(["claude.exe", "-c"], folder="c:\\work\\outreach\\")], LiveCheck(ROWS))

    def test_continue_with_unknown_folder_blocks(self):
        with self.assertRaises(UnidentifiedSessions):
            ids([unknown(["claude.exe", "--continue"], folder=None)], LiveCheck(ROWS))

    def test_resume_of_a_different_id_does_not_block(self):
        self.assertEqual(ids([unknown(["claude.exe", "-r", OTHER])], LiveCheck(ROWS)), set())
        self.assertEqual(ids([unknown(["claude.exe", f"--resume={OTHER}"])], LiveCheck(ROWS)), set())

    def test_resume_of_the_saved_id_blocks(self):
        with self.assertRaises(UnidentifiedSessions):
            ids([unknown(["claude.exe", "--resume", SAVED.upper()])], LiveCheck(ROWS))

    # WHAT IT DOES: `claude -r` alone opens a picker, so it could be any conversation.
    def test_resume_picker_blocks(self):
        for command in (["claude.exe", "-r"], ["claude.exe", "-r", "--model", "x"], ["claude.exe", "--resume", "search words"]):
            with self.subTest(command=command), self.assertRaises(UnidentifiedSessions):
                ids([unknown(command)], LiveCheck(ROWS))

    def test_unknown_command_or_codex_blocks(self):
        for row in (unknown(None), unknown([]), {**unknown(["codex.exe"]), "kind": "codex"}):
            with self.subTest(row=row), self.assertRaises(UnidentifiedSessions):
                ids([row], LiveCheck(ROWS))

    # WHAT IT DOES: Login auto-restore (strict=True) never passes an unidentified session.
    def test_login_auto_restore_still_blocks_on_plain_claude(self):
        with self.assertRaises(UnidentifiedSessions):
            ids([unknown(["claude.exe"])], True)

    # WHAT IT DOES: The explicit "Restore anyway" click passes unidentified sessions, never identified ones.
    def test_override_passes_but_keeps_known_ids(self):
        self.assertEqual(ids([KNOWN_ROW, unknown(["claude.exe", "-r"])], LiveCheck(ROWS, override=True)), {KNOWN})

    # WHAT IT DOES: The pause names which tab and folder could not be identified.
    def test_error_names_the_tab_and_folder(self):
        with self.assertRaises(UnidentifiedSessions) as caught:
            ids([unknown(["claude.exe", "-r"], folder="C:\\Work\\Crypto", title="Crypto tab")], LiveCheck(ROWS))
        text = str(caught.exception)
        self.assertIn("Crypto tab", text)
        self.assertIn("C:\\Work\\Crypto", text)
        self.assertEqual(len(caught.exception.sessions), 1)


# WHAT IT DOES: Saved layout with one restorable tab.
def snapshot():
    return {"captured_at": "T1", "windows": [{"x": 10, "y": 20, "w": 900, "h": 700, "tabs": [
        {"outcome": "MATCHED", "sessionId": SAVED, "strippedTitle": "Outreach", "cwd": "C:\\Work\\Outreach"}]}]}


class RestorePathTests(unittest.TestCase):
    # WHAT IT DOES: Runs a dry-run restore against a fake inventory holding one unidentified process.
    def run_dry(self, inventory, **options):
        with patch("keeper_inventory.live_inventory", return_value=inventory), \
             patch.object(keeper_restore, "STRICT_WAIT_SECONDS", 0), \
             patch("keeper_restore.read_windows", return_value=[]), \
             patch("keeper_restore.read_monitors", return_value=[{"x": 0, "y": 0, "w": 4000, "h": 2000}]), \
             patch("keeper_restore.load_created_windows", return_value=[]), \
             patch("keeper_restore.read_boot_id", return_value="B"), \
             patch("keeper_restore.read_live_groups", return_value=[]):
            return restore_selected(snapshot(), {"0:0"}, True, lock_name="unittest-restore", **options)

    def test_clicked_restore_opens_past_a_new_claude(self):
        restored, warnings = self.run_dry([unknown(["claude.exe"])], manual=True)
        self.assertEqual(restored, ["Outreach"])

    # WHAT IT DOES: Login auto-restore stops; the screen then shows the fix-it text and the button.
    def test_login_restore_still_stops(self):
        with self.assertRaises(UnidentifiedSessions):
            self.run_dry([unknown(["claude.exe"])])

    def test_clicked_restore_with_unprovable_process_raises_for_the_choice(self):
        with self.assertRaises(UnidentifiedSessions):
            self.run_dry([unknown(["claude.exe", "-r"])], manual=True)

    def test_restore_anyway_opens(self):
        restored, warnings = self.run_dry([unknown(["claude.exe", "-r"])], manual=True, override=True)
        self.assertEqual(restored, ["Outreach"])

    def test_assess_restore_raises_the_choice(self):
        with patch("keeper_inventory.live_inventory", return_value=[unknown(["claude.exe", "-r"])]), \
             patch.object(keeper_restore, "STRICT_WAIT_SECONDS", 0), \
             patch("keeper_restore.read_windows", return_value=[]):
            with self.assertRaises(UnidentifiedSessions):
                keeper_restore.assess_restore(snapshot(), {"0:0"}, lock_name="unittest-restore")


class AppChoiceTests(unittest.TestCase):
    # WHAT IT DOES: The paused panel offers "Restore anyway"; clicking it reruns with override, never on its own.
    def test_paused_panel_offers_restore_anyway(self):
        import keeper_app
        drawer = MagicMock()
        screen = SimpleNamespace(busy=True, countdown_text=MagicMock(), map=SimpleNamespace(drawer=drawer),
                                 selection_changed=MagicMock(), begin_operation=MagicMock(),
                                 command_entries=MagicMock(return_value=[]))
        error = UnidentifiedSessions([unknown(["claude.exe", "-r"], title="Crypto tab")])
        keeper_app.RecoveryWindow.operation_failed(screen, error, selected={"0:0"})
        self.assertFalse(screen.busy)
        screen.begin_operation.assert_not_called()
        kwargs = drawer.show_message.call_args.kwargs
        text, callback = kwargs["action"]
        self.assertEqual(text, "Restore anyway")
        callback()
        screen.begin_operation.assert_called_once_with(dry_run=False, ask=True, keys={"0:0"}, override=True)

    # WHAT IT DOES: An in-use window on the saved spot no longer asks; only empty terminals get the close question.
    def test_in_use_spot_does_not_ask(self):
        import keeper_app
        screen = SimpleNamespace(run_restore=MagicMock(), root=None, countdown_text=MagicMock())
        busy = [{"window_index": 0, "title": "a", "occupants": [{"hwnd": 10, "state": "live", "tabs": ["chat"]}]}]
        with patch.object(keeper_app, "OccupiedDialog") as dialog:
            keeper_app.RecoveryWindow.ask_about(screen, {"0:0"}, busy)
        dialog.assert_not_called()
        screen.run_restore.assert_called_once()

    def test_other_errors_offer_no_button(self):
        import keeper_app
        drawer = MagicMock()
        screen = SimpleNamespace(busy=True, countdown_text=MagicMock(), map=SimpleNamespace(drawer=drawer),
                                 selection_changed=MagicMock(), begin_operation=MagicMock(),
                                 command_entries=MagicMock(return_value=[]))
        keeper_app.RecoveryWindow.operation_failed(screen, RuntimeError("disk"))
        self.assertIsNone(drawer.show_message.call_args.kwargs.get("action"))


if __name__ == "__main__":
    unittest.main()
