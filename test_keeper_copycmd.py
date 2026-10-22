# Last modified: 2026-10-20 | Change: Tests for the Copy command text builder | Full history: git log
"""Fake snapshot rows only: no real session IDs, nothing is launched."""

import base64
import os
import shutil
import subprocess
import tempfile
import unittest

from keeper_copycmd import build_resume_text
from keeper_restore import resume_program

ID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ID_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
BS = chr(92)
_ROOT = tempfile.mkdtemp(prefix="keeper_copy_")
VD = os.path.join(_ROOT, "ProjectB")
OT = os.path.join(_ROOT, "Other")
os.makedirs(VD)
os.makedirs(OT)
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
SPECIAL = ["a$(calc)b", "my$stuff", "back`tick", "it's", "a&b", "semi;colon", "sp ace", "café 日本"]


def special_dirs():
    out = {}
    for name in SPECIAL:
        path = os.path.join(_ROOT, name)
        os.makedirs(path, exist_ok=True)
        out[name] = path
    return out


def tearDownModule():
    shutil.rmtree(_ROOT, ignore_errors=True)




def row(session_id=ID_A, kind="claude", title="ProjectB", cwd=VD, key="0:0"):
    return {"key": key, "session_id": session_id, "kind": kind, "title": title, "cwd": cwd}


# WHAT IT DOES: Guards the exact text the Copy command button puts on the clipboard.
# DEPENDS ON: keeper_copycmd.build_resume_text and keeper_restore.resume_program.
# AFFECTS: Test run only.
class CopyCommandTests(unittest.TestCase):
    def test_single_claude_tab_is_one_powershell_line(self):
        self.assertEqual(build_resume_text([row()]),
                         "if (Set-Location -LiteralPath '" + VD + "' -PassThru) { claude -r " + ID_A + " }")

    def test_codex_uses_the_restorer_command(self):
        text = build_resume_text([row(ID_B, "codex", "Other", OT)])
        self.assertEqual(text, "if (Set-Location -LiteralPath '" + OT + "' -PassThru) { " +
                         " ".join(resume_program(row(ID_B, "codex"))) + " }")
        self.assertIn("{ codex resume " + ID_B + " }", text)

    def test_multiple_tabs_have_comment_and_blank_line(self):
        text = build_resume_text([row(), row(ID_B, "codex", "Other", OT, "0:1")])
        first = "if (Set-Location -LiteralPath '" + VD + "' -PassThru) { claude -r " + ID_A + " }"
        second = "if (Set-Location -LiteralPath '" + OT + "' -PassThru) { codex resume " + ID_B + " }"
        self.assertEqual(text, "# ProjectB" + chr(10) + first + chr(10) + chr(10) + "# Other" + chr(10) + second)

    def test_single_quote_is_doubled(self):
        path = special_dirs()["it's"]
        self.assertIn("-LiteralPath '" + path.replace("'", "''") + "'", build_resume_text([row(cwd=path)]))

    def test_bad_session_id_refused(self):
        with self.assertRaises(ValueError):
            build_resume_text([row(session_id="not-an-id")])
        with self.assertRaises(ValueError):
            build_resume_text([row(session_id="")])

    def test_cwd_with_newline_refused(self):
        for bad in (VD + chr(10), VD + chr(13)):
            with self.assertRaises(ValueError):
                build_resume_text([row(cwd=bad)])

    def test_missing_folder_refused_with_clear_message(self):
        gone = os.path.join(_ROOT, "does_not_exist")
        with self.assertRaises(ValueError) as caught:
            build_resume_text([row(cwd=gone)])
        self.assertEqual(str(caught.exception), "Saved project folder no longer exists: " + gone)

    def test_unknown_kind_and_empty_refused(self):
        with self.assertRaises(RuntimeError):
            build_resume_text([row(kind="other")])
        with self.assertRaises(ValueError):
            build_resume_text([])

    def test_title_newline_cannot_inject_a_command(self):
        text = build_resume_text([row(title="x" + chr(10) + "rm -rf /"), row(ID_B, key="0:1")])
        self.assertEqual(text.splitlines()[0], "# x rm -rf /")


# WHAT IT DOES: Runs the generated line in a real PowerShell with claude/codex replaced by a stand-in function.
# DEPENDS ON: powershell or pwsh on PATH; skipped otherwise.
# AFFECTS: Test run only (temp folders).
@unittest.skipUnless(POWERSHELL, "PowerShell not available")
class CopyCommandPowerShellTests(unittest.TestCase):
    def run_line(self, text, start=None):
        stand_in = ('function claude { Write-Output ("RAN:" + (Get-Location).Path + "|" + ($args -join " ")) }; '
                    'function codex { Write-Output ("RAN:" + (Get-Location).Path + "|codex " + ($args -join " ")) }; ')
        script = "[Console]::OutputEncoding = [Text.Encoding]::UTF8; " + stand_in + text
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        done = subprocess.run([POWERSHELL, "-NoProfile", "-EncodedCommand", encoded],
                              capture_output=True, text=True, encoding="utf-8", cwd=start or tempfile.gettempdir(), timeout=60)
        return [line for line in done.stdout.splitlines() if line.startswith("RAN:")], done

    def test_special_character_folders_run_in_the_right_folder(self):
        for name, path in special_dirs().items():
            with self.subTest(name=name):
                ran, done = self.run_line(build_resume_text([row(cwd=path)]))
                self.assertEqual(ran, ["RAN:" + path + "|-r " + ID_A], done.stderr)

    def test_codex_row_runs(self):
        ran, done = self.run_line(build_resume_text([row(ID_B, "codex", "Other", OT)]))
        self.assertEqual(ran, ["RAN:" + OT + "|codex resume " + ID_B], done.stderr)

    def test_nothing_in_the_folder_name_is_evaluated(self):
        marker = os.path.join(_ROOT, "zz_marker")
        path = os.path.join(_ROOT, "x$(New-Item -ItemType File zz_marker)")
        os.makedirs(path, exist_ok=True)
        ran, done = self.run_line(build_resume_text([row(cwd=path)]), start=_ROOT)
        self.assertEqual(ran, ["RAN:" + path + "|-r " + ID_A], done.stderr)
        self.assertFalse(os.path.exists(marker))

    def test_folder_removed_after_copy_does_not_run_resume(self):
        path = os.path.join(_ROOT, "vanishing")
        os.makedirs(path, exist_ok=True)
        text = build_resume_text([row(cwd=path)])
        os.rmdir(path)
        ran, done = self.run_line(text)
        self.assertEqual(ran, [])

    def test_multi_row_runs_both(self):
        ran, done = self.run_line(build_resume_text([row(), row(ID_B, "codex", "Other", OT, "0:1")]))
        self.assertEqual(ran, ["RAN:" + VD + "|-r " + ID_A, "RAN:" + OT + "|codex resume " + ID_B], done.stderr)


if __name__ == "__main__":
    unittest.main()
