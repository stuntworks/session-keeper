# Last modified: 2026-10-22 | Change: Tk tests free Tk objects on the main thread and leave no workers running | Full history: git log
"""Tk test hygiene: every Tk object a test makes must be freed on the main thread.

Why: Python's garbage collector runs on whichever thread happens to allocate. A destroyed test
root (with its BooleanVars) sits in reference cycles until the collector runs. If a background
worker thread triggers that collection, Tk is torn down from the wrong thread:
"RuntimeError: main thread is not in main loop", then
"Tcl_AsyncDelete: async handler deleted by the wrong thread", which aborts the whole test run.
"""

import gc
import threading
from unittest.mock import patch

from keeper_background import WORKER_THREAD_NAME


# WHAT IT DOES: Stand-in for keeper_background.run_background that runs the job right here on the
# test's main thread and calls the matching callback.
# DEPENDS ON: Same arguments as run_background (root is unused).
# AFFECTS: Tests using it start no worker thread, so no worker can ever free their Tk objects.
def run_inline(root, action, on_success, on_error):
    try:
        result = action()
    except Exception as error:
        on_error(error)
        return
    on_success(result)


# WHAT IT DOES: Makes the details drawer read its transcript facts inline, from a fake, for one test.
# DEPENDS ON: keeper_drawer.run_background and keeper_drawer.agent_facts (patched until the test ends).
# AFFECTS: Clicking a tab in a test starts no worker thread and reads no real transcript files.
def keep_drawer_on_main_thread(test):
    import keeper_drawer
    for name, value in (("run_background", run_inline),
                        ("agent_facts", lambda row: {"available": False, "reason": "Test: facts not read"})):
        patcher = patch.object(keeper_drawer, name, value)
        patcher.start()
        test.addCleanup(patcher.stop)


# WHAT IT DOES: Lists background workers from keeper_background that are still running.
# DEPENDS ON: keeper_background.WORKER_THREAD_NAME.
# AFFECTS: no_workers_left uses it to fail a test that leaves a worker behind.
def running_workers():
    return [thread for thread in threading.enumerate() if thread.name == WORKER_THREAD_NAME and thread.is_alive()]


# WHAT IT DOES: Test cleanup check: fails the test if a keeper_background worker is still running.
# DEPENDS ON: running_workers.
# AFFECTS: A Tk test that starts a real worker fails here instead of crashing a later test at random.
def no_workers_left(test):
    test.assertEqual(running_workers(), [], "a background worker outlived this Tk test; use run_inline")


# WHAT IT DOES: Module teardown: collects leftover Tk roots and variables now, on the main thread.
# DEPENDS ON: Called after the module's tests have finished (their roots are destroyed).
# AFFECTS: The next test module's threads never find this module's Tk garbage to collect.
def free_tk_garbage():
    gc.collect()
