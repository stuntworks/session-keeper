# Last modified: 2026-10-03 | Change: Test Codex CLI identity without real process control | Full history: git log
"""Codex matching tests keep ambiguous rollouts ineligible for recovery."""

import json
import tempfile
import unittest
from pathlib import Path

from keeper_codex import verified_rollout_id


# WHAT IT DOES: Tests Codex rollout and tab identity decisions.
# DEPENDS ON: Temporary files and keeper_codex.
# AFFECTS: Prevents assigning a subagent's ID to a visible CLI tab.
class CodexTests(unittest.TestCase):
    # WHAT IT DOES: Writes one test rollout with the supplied source marker.
    # DEPENDS ON: Temporary directory supplied by the test.
    # AFFECTS: Supplies identity evidence to the matcher.
    def make_rollout(self, root, suffix, source):
        folder = root / "2026/10/02"
        folder.mkdir(parents=True, exist_ok=True)
        session_id = suffix * 8 + "-" + suffix * 4 + "-" + suffix * 4 + "-" + suffix * 4 + "-" + suffix * 12
        path = folder / f"rollout-2026-10-02T19-56-00-{session_id}.jsonl"
        path.write_text(json.dumps({"type": "session_meta", "payload":
            {"id": session_id, "source": source}}) + "\n", encoding="utf-8")
        return path, session_id

    # WHAT IT DOES: Accepts the CLI rollout and rejects a simultaneous subagent rollout.
    # DEPENDS ON: First-line Codex metadata.
    # AFFECTS: Avoids restoring the wrong conversation.
    def test_cli_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            direct, direct_id = self.make_rollout(root, "a", "cli")
            subagent, subagent_id = self.make_rollout(root, "b", {"subagent": {"other": "guardian"}})
            self.assertEqual(verified_rollout_id(direct, direct_id), direct_id)
            self.assertIsNone(verified_rollout_id(subagent, subagent_id))



if __name__ == "__main__":
    unittest.main()
