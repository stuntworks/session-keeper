# Last modified: 2026-10-21 | Change: Prove the fast live-session scan finds exactly what the slow scan found | Full history: git log
"""Fake process tables only. Nothing here starts, stops or changes a real process."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import psutil

from keeper_codex import active_codex_sessions
from keeper_roles import ParentSnapshot, process_role

ROLLOUT_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"


# WHAT IT DOES: A pretend process table that behaves like psutil on Windows, counting the slow calls.
# DEPENDS ON: Nothing real; pids, parents and start times are invented.
# AFFECTS: Lets each test compare the old parent walk with the snapshot walk on identical data.
class Table:
    def __init__(self, rows):
        self.rows = copy.deepcopy(rows)
        self.fresh_ppid_reads = 0
        self.start_time_reads = {}

    def map(self):
        return {pid: row["ppid"] for pid, row in self.rows.items() if row.get("alive", True)}

    def proc(self, pid):
        row = self.rows.get(pid)
        if row is None or not row.get("alive", True):
            raise psutil.NoSuchProcess(pid)
        return FakeProcess(self, pid)

    def iterate(self, attrs):
        for pid in sorted(self.rows):
            try:
                process = self.proc(pid)
                process.info = process.as_dict(attrs)
                yield process
            except psutil.NoSuchProcess:
                continue


# WHAT IT DOES: One pretend process: same method names, ppid caching and errors as psutil.Process.
# DEPENDS ON: Its Table row; "dies_on" makes it vanish when the named method is called.
# AFFECTS: Test only.
class FakeProcess:
    def __init__(self, table, pid):
        self.table, self.pid, self._ppid, self.info = table, pid, None, {}

    def row(self, method):
        row = self.table.rows.get(self.pid)
        if row is None or not row.get("alive", True):
            raise psutil.NoSuchProcess(self.pid)
        if method == row.get("dies_on"):
            row["alive"] = False
            raise psutil.NoSuchProcess(self.pid)
        if method in row.get("denied", ()):
            raise psutil.AccessDenied(self.pid)
        return row

    # Like psutil on Windows: a cached parent id is returned as is, otherwise a full fresh table read.
    def ppid(self):
        if not self._ppid:
            self.row("ppid")
            self.table.fresh_ppid_reads += 1
            self._ppid = self.table.map()[self.pid]
        return self._ppid

    def create_time(self):
        row = self.row("create_time")
        self.table.start_time_reads[self.pid] = self.table.start_time_reads.get(self.pid, 0) + 1
        return row["ctime"]

    def name(self): return self.row("name")["name"]
    def cmdline(self): return self.row("cmdline").get("cmdline", [self.row("cmdline")["name"]])
    def exe(self): return self.row("exe").get("exe", "C:\\tools\\" + self.row("exe")["name"])
    def cwd(self): return self.row("cwd").get("cwd", "C:\\work")
    def open_files(self): return [SimpleNamespace(path=p) for p in self.row("open_files").get("files", [])]

    def as_dict(self, attrs):
        result = {}
        for attr in attrs:
            try:
                result[attr] = self.pid if attr == "pid" else getattr(self, attr)()
            except psutil.AccessDenied:
                result[attr] = None
        return result

    # Same steps as psutil.Process.parent(): read ppid, re-read own start time, reject a newer parent.
    def parent(self):
        self.row("parent")
        if self.pid == 0:
            return None
        ppid = self.ppid()
        own_start = self.table.proc(self.pid).create_time()
        try:
            parent = self.table.proc(ppid)
            if parent.create_time() <= own_start:
                return parent
        except psutil.NoSuchProcess:
            pass
        return None

    def parents(self):
        found, current = [], self.parent()
        while current is not None:
            found.append(current)
            current = current.parent()
        return found


# WHAT IT DOES: Builds claude (100) under a ten-deep chain of shells that ends at an interactive claude.
# DEPENDS ON: Invented pids and start times only.
# AFFECTS: The delegated child must be classed "worker" by both walks.
def long_chain(top_exe="C:\\tools\\claude.exe"):
    rows = {0: {"name": "System Idle Process", "ppid": 0, "ctime": 0.0},
            4: {"name": "System", "ppid": 0, "ctime": 1.0},
            200: {"name": "explorer.exe", "ppid": 4, "ctime": 10.0},
            199: {"name": "claude.exe", "ppid": 200, "ctime": 20.0, "cmdline": ["claude.exe"], "exe": top_exe}}
    parent = 199
    for step, pid in enumerate(range(110, 100, -1)):
        rows[pid] = {"name": "node.exe" if step % 2 else "cmd.exe", "ppid": parent, "ctime": 30.0 + step}
        parent = pid
    rows[100] = {"name": "claude.exe", "ppid": parent, "ctime": 50.0, "cmdline": ["claude.exe"]}
    return rows


# WHAT IT DOES: Runs the old walk and the snapshot walk on two separate copies of the same table.
# DEPENDS ON: process_role with and without a ParentSnapshot.
# AFFECTS: Returns both answers and the snapshot table so tests can check call counts.
def both_walks(rows, clock=1000.0):
    old_table, new_table = Table(rows), Table(rows)
    old_process, new_process = old_table.proc(100), new_table.proc(100)
    old_process.info = {"cmdline": ["claude.exe"]}
    new_process.info = {"cmdline": ["claude.exe"]}
    old = process_role(old_process, "claude")
    new = process_role(new_process, "claude", ParentSnapshot(read_map=new_table.map, clock=lambda: clock))
    return old, new, new_table


# WHAT IT DOES: Proves the fast scan gives the same live-session answer as the slow one.
# DEPENDS ON: Fake tables above, plus two read-only looks at this test's own process.
# AFFECTS: A miss here would let Session Keeper reopen a conversation that is already running.
class LiveScanTests(unittest.TestCase):
    def test_long_chain_worker_found_with_one_parent_snapshot(self):
        old, new, table = both_walks(long_chain())
        self.assertEqual((old, new), ("worker", "worker"))
        # Only System (pid 4, parent id 0) still needs a fresh read: psutil treats a cached 0 as empty too.
        self.assertEqual(table.fresh_ppid_reads, 1)
        old_table = Table(long_chain())
        old_table.proc(100).parents()
        self.assertGreater(old_table.fresh_ppid_reads, 10)

    def test_service_at_top_of_long_chain_stays_interactive(self):
        old, new, unused = both_walks(long_chain("C:\\Program Files\\WindowsApps\\Claude_1\\app\\claude.exe"))
        self.assertEqual((old, new), ("interactive", "interactive"))

    def test_reused_parent_pid_stops_walk_the_same_way(self):
        rows = long_chain()
        rows[110]["ctime"] = 99.0  # newer than its child: that pid now belongs to someone else
        old, new, unused = both_walks(rows)
        self.assertEqual((old, new), ("interactive", "interactive"))

    def test_ancestor_gone_before_scan_stops_walk_the_same_way(self):
        rows = long_chain()
        rows[105]["alive"] = False
        old, new, unused = both_walks(rows)
        self.assertEqual((old, new), ("interactive", "interactive"))

    def test_ancestor_that_dies_mid_walk_is_unknown_in_both(self):
        rows = long_chain()
        rows[105]["dies_on"] = "parent"
        old, new, unused = both_walks(rows)
        self.assertEqual((old, new), ("unknown", "unknown"))

    def test_ancestor_access_denied_is_unknown_in_both(self):
        rows = long_chain()
        rows[103]["denied"] = ["create_time"]
        old, new, unused = both_walks(rows)
        self.assertEqual((old, new), ("unknown", "unknown"))

    def test_process_started_after_snapshot_uses_old_walk(self):
        old, new, table = both_walks(long_chain(), clock=50.5)
        self.assertEqual((old, new), ("worker", "worker"))
        self.assertGreater(table.fresh_ppid_reads, 0)

    def test_unreadable_snapshot_falls_back_to_old_walk(self):
        def broken():
            raise OSError("snapshot failed")
        table = Table(long_chain())
        process = table.proc(100)
        process.info = {"cmdline": ["claude.exe"]}
        self.assertEqual(process_role(process, "claude", ParentSnapshot(read_map=broken)), "worker")
        self.assertGreater(table.fresh_ppid_reads, 0)

    def test_real_psutil_still_uses_a_prefilled_parent_id(self):
        # Guards the one psutil detail the fast walk relies on; if this fails, psutil changed.
        process = psutil.Process(os.getpid())
        process._ppid = 424242
        self.assertEqual(process.ppid(), 424242)

    def test_real_process_role_matches_with_and_without_snapshot(self):
        slow, fast = psutil.Process(os.getpid()), psutil.Process(os.getpid())
        slow.info = {"cmdline": slow.cmdline()}
        fast.info = dict(slow.info)
        self.assertEqual(process_role(slow, "claude"), process_role(fast, "claude", ParentSnapshot()))

    def codex_table(self, folder):
        rollout = Path(folder) / f"rollout-2026-10-21T01-00-00-{ROLLOUT_ID}.jsonl"
        rollout.write_text(json.dumps({"type": "session_meta", "payload": {"id": ROLLOUT_ID, "source": "cli"}}), encoding="utf-8")
        return Table({
            10: {"name": "svchost.exe", "ppid": 4, "ctime": 1.0, "denied": ["cmdline", "cwd"]},
            11: {"name": "codex.exe", "ppid": 4, "ctime": 2.0, "cmdline": ["codex.exe"], "cwd": folder, "files": [str(rollout)]},
            12: {"name": "codex.exe", "ppid": 4, "ctime": 3.0, "cmdline": ["codex.exe", "exec", "x"], "cwd": folder},
            13: {"name": "codex.exe", "ppid": 4, "ctime": 4.0, "cmdline": ["codex.exe", "app-server"]},
            14: {"name": "codex.exe", "ppid": 4, "ctime": 5.0, "dies_on": "cmdline"},
            15: {"name": "codex.exe", "ppid": 4, "ctime": 6.0, "cmdline": ["codex.exe"], "denied": ["cwd"], "files": [str(rollout)]},
            16: {"name": "node.exe", "ppid": 4, "ctime": 7.0}})

    def test_codex_scan_reads_details_only_for_codex(self):
        with tempfile.TemporaryDirectory() as folder:
            table = self.codex_table(folder)
            with patch("keeper_codex.psutil.process_iter", side_effect=table.iterate):
                sessions = active_codex_sessions(read_titles=False)
            self.assertEqual([(s["pid"], s["sessionId"], s["role"], s["cwd"]) for s in sessions],
                             [(11, ROLLOUT_ID, "interactive", folder), (12, None, "worker", folder),
                              (15, ROLLOUT_ID, "interactive", str(Path.home()))])
            self.assertEqual(table.start_time_reads, {})


if __name__ == "__main__":
    unittest.main()
