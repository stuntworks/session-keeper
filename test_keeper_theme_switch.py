# Last modified: 2026-10-26 | Change: Dark mode / Light mode setting, header switch and the reopen | Full history: git log
"""Light mode: the saved setting, the header switch and the reopen of the recovery screen. Temporary files,
a throwaway mutex name and fake launchers only: nothing is launched, restored or closed for real."""

import os
import subprocess
import sys
import tempfile
import tkinter as tk
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import keeper_recovery_view
import keeper_single
from keeper_chrome import Header
from keeper_recovery_view import RecoveryScreen
from keeper_settings import auto_restore_enabled, record_auto_restore, record_theme, theme_mode
from keeper_theme import MODE, Theme
from tk_test_support import free_tk_garbage

OTHER = "light" if MODE != "light" else "dark"


# WHAT IT DOES: The saved screen-colour choice: dark unless the latest valid choice says light.
# DEPENDS ON: A temporary settings event file.
# AFFECTS: Nothing outside the temporary folder.
class ThemeSettingTests(unittest.TestCase):
    def test_default_is_dark(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.jsonl"
            self.assertEqual(theme_mode(path), "dark")
            path.write_text('{"auto_restore": true}\nnot json\n{"theme": "neon"}\n[1]\n', encoding="utf-8")
            self.assertEqual(theme_mode(path), "dark")

    def test_latest_choice_wins_and_keeps_other_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.jsonl"
            record_auto_restore(True, path)
            record_theme("light", path)
            self.assertEqual(theme_mode(path), "light")
            self.assertTrue(auto_restore_enabled(path), "a theme choice must not touch auto restore")
            record_theme("dark", path)
            self.assertEqual(theme_mode(path), "dark")
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 3)

    def test_unknown_theme_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.jsonl"
            with self.assertRaises(ValueError):
                record_theme("neon", path)
            self.assertFalse(path.exists())


# WHAT IT DOES: The reopen gives up the screen lock and starts the same recover command, never anything else.
# DEPENDS ON: keeper_single with a fake Popen and a throwaway mutex name.
# AFFECTS: Nothing is launched.
class RelaunchTests(unittest.TestCase):
    def setUp(self):
        self.name = f"Local\\SessionKeeper-test-{uuid.uuid4().hex}"
        self.calls = []

    def fake_popen(self, command, **options):
        self.calls.append((command, options))

    def test_reopens_the_same_recover_command_without_a_console(self):
        handle = keeper_single.try_own(self.name)
        with patch.object(keeper_single, "_held", [handle]):
            keeper_single.relaunch_recover_screen(self.fake_popen, ["keeper_app.py", "recover"])
            self.assertEqual(keeper_single._held, [])
        free = keeper_single.try_own(self.name)
        self.assertTrue(free, "the screen lock must be free for the new copy")
        keeper_single.release(free)
        command, options = self.calls[0]
        self.assertEqual(command, [sys.executable, os.path.abspath("keeper_app.py"), "recover"])
        self.assertTrue(options["creationflags"] & subprocess.CREATE_NO_WINDOW)

    def test_never_reopens_the_recorder_or_anything_else(self):
        for argv in (["keeper_app.py", "watch"], ["keeper_app.py", "snapshot"], ["keeper_app.py"]):
            with self.subTest(argv=argv), patch.object(keeper_single, "_held", []):
                with self.assertRaises(RuntimeError):
                    keeper_single.relaunch_recover_screen(self.fake_popen, argv)
        self.assertEqual(self.calls, [])

    def test_failed_start_keeps_this_window_as_the_screen(self):
        def broken(command, **options):
            raise OSError("cannot start")
        handle = keeper_single.try_own(self.name)
        with patch.object(keeper_single, "_held", [handle]), \
                patch.object(keeper_single, "screen_lock_name", lambda: self.name):
            with self.assertRaises(OSError):
                keeper_single.relaunch_recover_screen(broken, ["keeper_app.py", "recover"])
            self.assertEqual(len(keeper_single._held), 1)
            keeper_single.release(keeper_single._held.pop())


# WHAT IT DOES: The screen's switch saves the choice, then reopens (recover) or only saves (login screen).
# DEPENDS ON: RecoveryScreen.switch_theme on a stand-in window; record_theme patched to a list.
# AFFECTS: Nothing on disk; no window is closed.
class SwitchThemeTests(unittest.TestCase):
    def window(self, **state):
        values = {"busy": False, "countdown_job": None, "native_wait": None, "watch_mode": False}
        values.update(state)
        text = SimpleNamespace(value=None)
        text.configure = lambda **options: setattr(text, "value", options.get("text"))
        root = SimpleNamespace(destroyed=False)
        root.destroy = lambda: setattr(root, "destroyed", True)
        return SimpleNamespace(countdown_text=text, root=root, **values)

    def switch(self, window, mode):
        saved, relaunched = [], []
        with patch.object(keeper_recovery_view, "record_theme", saved.append):
            RecoveryScreen.switch_theme(window, mode, relaunch=lambda: relaunched.append(True))
        return saved, relaunched

    def test_switch_writes_the_setting_and_reopens(self):
        window = self.window()
        saved, relaunched = self.switch(window, OTHER)
        self.assertEqual(saved, [OTHER])
        self.assertEqual(relaunched, [True])
        self.assertTrue(window.root.destroyed)

    def test_current_mode_does_nothing(self):
        window = self.window()
        self.assertEqual(self.switch(window, MODE), ([], []))
        self.assertFalse(window.root.destroyed)

    def test_login_screen_only_saves(self):
        window = self.window(watch_mode=True)
        saved, relaunched = self.switch(window, OTHER)
        self.assertEqual((saved, relaunched), ([OTHER], []))
        self.assertFalse(window.root.destroyed)
        self.assertIn("next time", window.countdown_text.value)

    def test_never_during_a_restore_or_countdown(self):
        for state in ({"busy": True}, {"countdown_job": "after#1"}, {"native_wait": object()}):
            with self.subTest(state=state):
                window = self.window(**state)
                self.assertEqual(self.switch(window, OTHER), ([], []))
                self.assertFalse(window.root.destroyed)


# WHAT IT DOES: The header shows a Dark mode / Light mode switch whose buttons call back with the mode.
# DEPENDS ON: A hidden Tk root.
# AFFECTS: Widgets only.
class HeaderSwitchTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()

    def test_buttons_call_back_with_their_mode(self):
        chosen = []
        header = Header(self.root, Theme(self.root, 2400), lambda name: None, tk.BooleanVar(self.root),
                        lambda: None, on_theme=chosen.append)
        self.assertEqual(header.dark_btn.cget("text"), "Dark mode")
        self.assertEqual(header.light_btn.cget("text"), "Light mode")
        header.light_btn.invoke()
        header.dark_btn.invoke()
        self.assertEqual(chosen, ["light", "dark"])
        active = header.light_btn if MODE == "light" else header.dark_btn
        self.assertEqual(active.kind, "seg_on")

    def test_no_switch_without_a_callback(self):
        header = Header(self.root, Theme(self.root, 2400), lambda name: None, tk.BooleanVar(self.root), lambda: None)
        self.assertIsNone(header.theme_switch)


# WHAT IT DOES: Frees this module's Tk objects on the main thread once its tests finish.
def tearDownModule():
    free_tk_garbage()


if __name__ == "__main__":
    unittest.main()
