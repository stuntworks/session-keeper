# Last modified: 2026-10-03 | Change: Guard every background probe against flashing a console window | Full history: git log
"""A hidden recorder must never hand Windows a reason to draw a console."""

import subprocess
import unittest
from unittest.mock import patch

import keeper_core


# WHAT IT DOES: Runs each background probe and inspects the flags it gave subprocess.
# DEPENDS ON: Patched subprocess.run, so no real PowerShell starts.
# AFFECTS: Catches the black console box the user saw popping up on 2026-10-03.
class NoConsoleTests(unittest.TestCase):
    # WHAT IT DOES: Confirms the main inventory probe still hides its console.
    # DEPENDS ON: keeper_core.run_powershell.
    # AFFECTS: Regression guard for the probe that was already correct.
    def test_core_probe_hides_its_console(self):
        with patch.object(keeper_core.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
            keeper_core.run_powershell(["-Command", "exit"])
        flags = run.call_args.kwargs.get("creationflags", 0)
        self.assertTrue(flags & subprocess.CREATE_NO_WINDOW,
                        "keeper_core.run_powershell can flash a console window")


if __name__ == "__main__":
    unittest.main()
