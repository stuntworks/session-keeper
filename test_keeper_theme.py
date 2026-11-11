# Last modified: 2026-10-26 | Change: Contrast rules checked for BOTH palettes; palette picked from the saved setting | Full history: git log
"""Contrast guard for keeper_theme (WCAG ratio). Owner rule: no grey-on-grey anywhere, in dark or light mode."""

import os
import subprocess
import sys
import unittest
from pathlib import Path

import keeper_theme
from keeper_palette import DARK, LIGHT, PALETTES, outline_pairs, text_pairs
from keeper_theme import (MIN_BODY, MIN_OUTLINE, MIN_SECONDARY, OUTLINE_PAIRS, TEXT_PAIRS, contrast, mix,
                          palette_for, rounded)

HERE = Path(__file__).resolve().parent


class ContrastTests(unittest.TestCase):
    def test_every_text_pair_is_readable_in_both_palettes(self):
        for mode, palette in PALETTES.items():
            for name, (text, background) in text_pairs(palette).items():
                body = text == palette["INK"] or name.startswith(("ink", "black"))
                floor = MIN_BODY if body else MIN_SECONDARY
                with self.subTest(mode=mode, name=name):
                    self.assertGreaterEqual(contrast(text, background), floor,
                                            f"{mode} {name}: {contrast(text, background):.2f}")

    def test_monitor_tints_are_brightly_lettered_and_distinct(self):
        for mode, palette in PALETTES.items():
            tints = palette["TINTS"]
            self.assertGreaterEqual(len(tints), 2)
            self.assertEqual(len(set(tints)), len(tints))
            for tint in tints:
                with self.subTest(mode=mode, tint=tint):
                    self.assertIn(("ink on " + tint), text_pairs(palette))
                    self.assertGreaterEqual(contrast(palette["INK"], tint), MIN_BODY)
                    self.assertGreaterEqual(contrast(palette["DIM"], tint), MIN_BODY, "owner: Dim on tints at least 7:1")

    def test_window_outline_reads_on_every_surface(self):
        for mode, palette in PALETTES.items():
            pairs = outline_pairs(palette)
            self.assertTrue(pairs)
            for name, (line, background) in pairs.items():
                with self.subTest(mode=mode, name=name):
                    self.assertGreaterEqual(contrast(line, background), MIN_OUTLINE, f"{mode} {name}")

    def test_hardest_wallpaper_pixel_keeps_dim_text_at_body_contrast(self):
        for mode, palette in PALETTES.items():
            with self.subTest(mode=mode):
                self.assertGreaterEqual(contrast(palette["DIM"], palette["WALL_WORST"]), MIN_BODY)

    def test_both_palettes_define_the_same_colours(self):
        self.assertEqual(set(DARK), set(LIGHT))

    def test_active_pairs_come_from_the_active_palette(self):
        self.assertEqual(TEXT_PAIRS, text_pairs(keeper_theme.PALETTE))
        self.assertEqual(OUTLINE_PAIRS, outline_pairs(keeper_theme.PALETTE))
        self.assertEqual(keeper_theme.ROOM, keeper_theme.PALETTE["ROOM"])

    def test_contrast_reference_values(self):
        self.assertAlmostEqual(contrast("#000000", "#FFFFFF"), 21.0, places=2)
        self.assertAlmostEqual(contrast("#777777", "#777777"), 1.0, places=2)

    def test_mix_and_rounded(self):
        self.assertEqual(mix("#000000", "#FFFFFF", 0.5), "#808080")
        self.assertEqual(len(rounded(0, 0, 100, 40, 8)), 40)


# WHAT IT DOES: The saved mode picks the palette; dark unless the setting says light.
# DEPENDS ON: keeper_theme.palette_for; a separate Python process for the import-time check.
# AFFECTS: Nothing on disk (the setting is faked inside the child process).
class PaletteChoiceTests(unittest.TestCase):
    def test_light_setting_selects_light_palette(self):
        self.assertIs(palette_for("light"), LIGHT)

    def test_anything_else_selects_dark(self):
        for mode in ("dark", None, "", "neon"):
            with self.subTest(mode=mode):
                self.assertIs(palette_for(mode), DARK)

    def test_import_uses_the_saved_mode(self):
        code = ("import keeper_settings; keeper_settings.theme_mode = lambda *a, **k: {mode!r}; "
                "import keeper_theme, keeper_palette; "
                "print(keeper_theme.MODE, keeper_theme.ROOM == keeper_palette.{name}['ROOM'], "
                "keeper_theme.KINDS['go'][0] == keeper_palette.{name}['GO'])")
        for mode, name in (("light", "LIGHT"), ("dark", "DARK")):
            with self.subTest(mode=mode):
                result = subprocess.run([sys.executable, "-c", code.format(mode=mode, name=name)], cwd=HERE,
                                        capture_output=True, text=True, timeout=60,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
                self.assertEqual(result.stdout.split(), [mode, "True", "True"], result.stderr)


if __name__ == "__main__":
    unittest.main()
