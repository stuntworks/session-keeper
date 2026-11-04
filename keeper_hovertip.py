# Last modified: 2026-11-03 | Change: New. One safe hover popup shared by every canvas target | Full history: git log
"""The hover popup of the monitor map, built so it can never fight the pointer.

The old popup froze the screen: it was drawn where Tk picked it as the item under the pointer,
so Tk fired Leave on the tab, the popup was deleted, Tk fired Enter, the popup was drawn again,
and so on without end. Here the popup items are disabled (Tk never picks them), a popup is only
drawn again when the target changes, and Leave only hides it when the pointer really left.
"""

from keeper_theme import BEZEL, INK

TAG = "tip"


# WHAT IT DOES: Shows and hides one large popover next to a canvas target, kept inside the map.
# DEPENDS ON: A tk.Canvas, a Theme (px, font), a function giving the map size, and the text size.
# AFFECTS: Canvas items tagged "tip" only. Never launches, moves or closes anything.
class HoverTip:
    def __init__(self, canvas, theme, get_size, text_size):
        self.canvas, self.theme, self.get_size, self.text_size = canvas, theme, get_size, text_size
        self.key = None                          # which target the popup on screen belongs to

    # WHAT IT DOES: Makes `tag` raise this popup on Enter and lose it on Leave.
    # DEPENDS ON: key identifies the target; text is what to say; x, y is where it hangs from.
    # AFFECTS: The canvas bindings of `tag` (replaces earlier Enter/Leave bindings for it).
    def attach(self, tag, key, text, x, y, above=False):
        self.canvas.tag_bind(tag, "<Enter>", lambda event: self.show(key, text, x, y, above))
        self.canvas.tag_bind(tag, "<Leave>", lambda event: self.leave(key, tag))

    # WHAT IT DOES: Draws the popup for `key`, unless that very popup is already on screen.
    # AFFECTS: One pair of disabled canvas items; the previous popup (another target) is removed.
    def show(self, key, text, x, y, above=False):
        if key == self.key and self.canvas.find_withtag(TAG):
            return
        canvas, px = self.canvas, self.theme.px
        canvas.delete(TAG)
        width, height = self.get_size()
        wrap = max(min(width - px(60), px(1500)), px(300))
        # state="disabled": Tk skips these when it works out which item is under the pointer.
        item = canvas.create_text(0, 0, anchor="nw", tags=(TAG,), fill=INK, width=wrap, state="disabled",
                                  font=self.theme.font("body", self.text_size), text=text)
        left, top, right, bottom = canvas.bbox(item)
        tx = min(max(x, px(20)), width - (right - left) - px(20))
        ty = y - (bottom - top) - px(18) if above else y + px(14)
        canvas.move(item, tx, max(ty, px(10)))
        left, top, right, bottom = canvas.bbox(item)
        back = canvas.create_rectangle(left - px(14), top - px(10), right + px(14), bottom + px(10),
                                       fill=BEZEL, outline=INK, width=px(2), tags=(TAG,), state="disabled")
        canvas.tag_lower(back, item)
        self.key = key

    # WHAT IT DOES: Pointer left one item of the target. Once Tk has settled on the next item, hides the
    # popup only if the pointer is no longer on any item of that target (a tab is several items).
    def leave(self, key, tag):
        if key == self.key:
            self.canvas.after_idle(self.check, key, tag)

    def check(self, key, tag):
        try:
            if key == self.key and tag not in self.canvas.gettags("current"):
                self.hide()
        except Exception:
            self.key = None                      # the canvas is gone; nothing to hide

    # WHAT IT DOES: Removes the popup, whatever target it belonged to.
    def hide(self):
        self.key = None
        self.canvas.delete(TAG)
