# Last modified: 2026-10-03 | Change: Probe read-only Codex console title mapping | Full history: git log
"""Diagnostic: ask Windows for one process's console title without touching its input."""

import ctypes
import sys


# WHAT IT DOES: Reads the console title attached to a specified process.
# DEPENDS ON: Windows kernel32 console APIs.
# AFFECTS: Only this short-lived probe's own console attachment changes.
def read_console_title(process_id):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.FreeConsole()
    if not kernel.AttachConsole(process_id):
        return f"AttachConsole failed: {ctypes.get_last_error()}"
    buffer = ctypes.create_unicode_buffer(1024)
    length = kernel.GetConsoleTitleW(buffer, len(buffer))
    kernel.FreeConsole()
    return buffer.value if length else f"GetConsoleTitle failed: {ctypes.get_last_error()}"


if __name__ == "__main__":
    print(read_console_title(int(sys.argv[1])))
