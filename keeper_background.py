# Last modified: 2026-10-03 | Change: Keep recovery controls responsive during process scans | Full history: git log
"""Workers return results through a queue; only the UI thread touches Tk widgets."""

import queue
import threading


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

    threading.Thread(target=work, daemon=True).start()
    root.after(100, poll)
