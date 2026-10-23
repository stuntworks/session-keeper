# Last modified: 2026-10-22 | Change: Monitor names and wallpapers for the map, read locally, never saved | Full history: git log
"""The map's real monitor names and dimmed wallpapers: readable, matched to the right monitor, never fatal."""

import unittest
from unittest.mock import patch

import keeper_desktop_look as look
from keeper_theme import AMBER, DIM, INK, MIN_BODY, MIN_OUTLINE, MIN_SECONDARY, OUTLINE, WALL_CEIL, contrast


def edid(name):
    data = bytearray(128)
    block = bytes([0, 0, 0, 0xFC, 0]) + (name.encode("ascii") + b"\n").ljust(13, b" ")
    data[72:90] = block
    return bytes(data)


class DesktopLookTests(unittest.TestCase):
    def test_monitor_name_comes_from_the_edid(self):
        self.assertEqual(look.edid_name(edid("DELL U2720Q")), "DELL U2720Q")
        self.assertIsNone(look.edid_name(bytes(128)))
        self.assertIsNone(look.edid_name(b""))

    def test_registry_key_is_read_from_the_device_path(self):
        path = r"\\?\DISPLAY#DEL41A8#5&2a8c1b5&0&UID4352#{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}"
        self.assertEqual(look.registry_key(path), r"SYSTEM\CurrentControlSet\Enum\DISPLAY\DEL41A8\5&2a8c1b5&0&UID4352"
                                                  r"\Device Parameters")
        self.assertIsNone(look.registry_key("nonsense"))

    def test_the_brightest_wallpaper_pixel_still_keeps_text_readable(self):
        self.assertGreaterEqual(contrast(INK, WALL_CEIL), MIN_BODY)
        self.assertGreaterEqual(contrast(DIM, WALL_CEIL), MIN_BODY)
        self.assertGreaterEqual(contrast(AMBER, WALL_CEIL), MIN_SECONDARY)
        self.assertGreaterEqual(contrast(OUTLINE, WALL_CEIL), MIN_OUTLINE)
        for colour in ("#FFFFFF", "#FF0000", "#00FF00", "#0000FF", "#808080"):
            dimmed = look.dim_colour(colour)
            self.assertTrue(all(int(dimmed[i:i + 2], 16) <= int(WALL_CEIL[i:i + 2], 16) for i in (1, 3, 5)), colour)

    def test_a_white_wallpaper_is_dimmed_under_the_ceiling(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed: wallpapers fall back to the tinted desktop")
        dimmed = look.dim_image(Image.new("RGB", (40, 20), "#FFFFFF"))
        ceiling = tuple(int(WALL_CEIL[i:i + 2], 16) for i in (1, 3, 5))
        for channel, top in zip(dimmed.getextrema(), ceiling):
            self.assertLessEqual(channel[1], top)

    def test_stand_in_wallpaper_stays_under_the_ceiling(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("Pillow not installed: the painter draws a plain gradient instead")
        from keeper_theme import TINTS
        ceiling = tuple(int(WALL_CEIL[i:i + 2], 16) for i in (1, 3, 5))
        for tint in TINTS:
            picture = look.tinted_desktop(tint, (320, 180))
            self.assertEqual(picture.size, (320, 180))
            for channel, top in zip(picture.getextrema(), ceiling):
                self.assertLessEqual(channel[1], top, tint)

    def test_saved_monitor_is_matched_by_where_it_sits(self):
        live = [{"rect": (0, 0, 3840, 2160), "name": "DELL U2720Q"}, {"rect": (3840, 0, 3840, 2160), "name": "LG"}]
        self.assertEqual(look.match(live, {"x": 3840, "y": 0, "w": 3840, "h": 2088})["name"], "LG")
        self.assertIsNone(look.match(live, {"x": 9000, "y": 0, "w": 1000, "h": 1000}))
        self.assertIsNone(look.match([], {"x": 0, "y": 0, "w": 10, "h": 10}))

    def test_reading_never_fails_the_screen(self):
        with patch.object(look, "wallpaper_monitors", side_effect=OSError("no COM")), \
                patch.object(look, "display_monitors", side_effect=OSError("no user32")):
            self.assertEqual(look.read_desktop_look(), [])


if __name__ == "__main__":
    unittest.main()
