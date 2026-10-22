# Last modified: 2026-10-21 | Change: Loading window matches the dark recovery screen | Full history: git log
"""A small dark window shown the instant recovery starts, while saved layouts load."""

import tkinter as tk

from keeper_background import run_background
from keeper_display import pointer_monitor, read_monitors
from keeper_theme import EDGE, GO, INK, ROOM, STOP, Theme

BG, FG, ERR = ROOM, INK, STOP
MESSAGE = "Loading your saved layouts..."
WIDTH, HEIGHT = 640, 190


# WHAT IT DOES: Pick the top-left corner that centres a window on the monitor under the mouse.
# DEPENDS ON: keeper_display monitor reading; falls back to Tk's own screen size.
# AFFECTS: Where the loading window appears.
def centred_origin(root, width=WIDTH, height=HEIGHT):
    try:
        monitor = pointer_monitor(read_monitors())
        return monitor["x"] + (monitor["w"] - width) // 2, monitor["y"] + (monitor["h"] - height) // 2
    except (RuntimeError, OSError, KeyError, TypeError):
        return (root.winfo_screenwidth() - width) // 2, (root.winfo_screenheight() - height) // 2


# WHAT IT DOES: Shows a moving progress bar with a message, or an error message if loading fails.
# DEPENDS ON: A Tk root that is already running (the root may stay withdrawn).
# AFFECTS: Only its own Toplevel; closing it by hand quits the app.
class LoadingWindow:
    def __init__(self, root):
        self.root = root
        self.top = tk.Toplevel(root, bg=BG)
        self.top.title("Session Keeper")
        x, y = centred_origin(root)
        self.top.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")
        self.top.resizable(False, False)
        theme = Theme(root, 2400)   # fixed size: this window is small, so design pixels are used 1:1
        tk.Label(self.top, text="SESSION KEEPER", bg=BG, fg=FG, font=theme.font("display", 30)).pack(pady=(26, 0))
        self.label = tk.Label(self.top, text=MESSAGE, bg=BG, fg=FG, font=theme.font("body", 20),
                              wraplength=WIDTH - 40, justify="center")
        self.label.pack(expand=True, fill="both", padx=20, pady=(4, 8))
        self.small_font = theme.font("body", 17)
        # Own canvas bar: ttk's default theme ignores colours and a theme change would alter the picker.
        self.bar = tk.Canvas(self.top, width=WIDTH - 80, height=8, bg=EDGE, highlightthickness=0)
        self.bar.pack(pady=(0, 28))
        self.block = self.bar.create_rectangle(0, 0, 120, 8, fill=GO, width=0)
        self.position, self.job = 0, None
        self.animate()
        self.top.protocol("WM_DELETE_WINDOW", root.destroy)
        self.top.update()  # paint now, before any heavy work starts

    # WHAT IT DOES: Slides a block back and forth along the bar so the window visibly moves.
    # DEPENDS ON: Tk timer on the main thread.
    # AFFECTS: Only the canvas.
    def animate(self):
        self.position = (self.position + 8) % (2 * (WIDTH - 80 - 120))
        span = WIDTH - 80 - 120
        x = self.position if self.position <= span else 2 * span - self.position
        self.bar.coords(self.block, x, 0, x + 120, 8)
        self.job = self.top.after(30, self.animate)

    # WHAT IT DOES: Replaces the message with the error text and stops the animation.
    # DEPENDS ON: Main thread only.
    # AFFECTS: The window stays open so the error can be read.
    def show_error(self, text):
        self.top.after_cancel(self.job)
        self.bar.pack_forget()
        self.label.configure(text=f"Session Keeper could not load:\n{text}", fg=ERR, font=self.small_font)

    def close(self):
        self.top.after_cancel(self.job)
        self.top.destroy()


# WHAT IT DOES: Runs loader off the Tk thread, then closes the window and calls on_ready(result) on the Tk thread.
# DEPENDS ON: keeper_background.run_background (worker thread + root.after polling).
# AFFECTS: A loader or on_ready failure is shown in the window instead of vanishing.
def load_then_show(root, window, loader, on_ready):
    def succeeded(result):
        try:
            on_ready(result)
        except Exception as error:
            window.show_error(f"{type(error).__name__}: {error}")
            return
        window.close()

    def failed(error):
        window.show_error(f"{type(error).__name__}: {error}")

    run_background(root, loader, succeeded, failed)
