# Last modified: 2026-10-22 | Change: Name worker threads so tests can prove none outlive a Tk root | Full history: git log
"""Workers return results through a queue; only the UI thread touches Tk widgets."""

import queue
import threading

# Every worker started here carries this name; tests use it to check no worker outlives a Tk root.
WORKER_THREAD_NAME = "keeper-background"


# WHAT IT DOES: Runs an operation away from the UI and returns its result to the Tk thread.
# DEPENDS ON: An active Tk root and success/error callbacks.
# AFFECTS: The STOP button remains usable while process and window scans are running.
def run_background(root, action, on_success, on_error):
    results = queue.Queue()

    # WHAT IT DOES: Puts the operation result or error into the thread-safe result queue.
    # DEPENDS ON: The supplied action and result queue.
    # AFFECTS: No UI work happens on this worker thread.
    def work():
        try:
            results.put((True, action()))
        except Exception as error:
            results.put((False, error))

    # WHAT IT DOES: Delivers a completed result while keeping Tk responsive between checks.
    # DEPENDS ON: Tk timer and result queue.
    # AFFECTS: Callbacks run on the UI thread.
    def poll():
        try:
            succeeded, result = results.get_nowait()
        except queue.Empty:
            root.after(100, poll)
            return
        if succeeded:
            on_success(result)
        else:
            on_error(result)

    threading.Thread(target=work, daemon=True, name=WORKER_THREAD_NAME).start()
    root.after(100, poll)
