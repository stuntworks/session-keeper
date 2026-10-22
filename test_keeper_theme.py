# Last modified: 2026-10-21 | Change: Every text colour must stay readable on every background it is drawn on | Full history: git log
"""Contrast guard for keeper_theme (WCAG ratio). Owner rule: no grey-on-grey anywhere."""

import unittest

from keeper_theme import (DIM, INK, MIN_BODY, MIN_OUTLINE, MIN_SECONDARY, OUTLINE_PAIRS, TEXT_PAIRS, TINTS,
                          contrast, mix, rounded)


class ContrastTests(unittest.TestCase):
    def test_every_text_pair_is_readable(self):
        for name, (text, background) in TEXT_PAIRS.items():
            floor = MIN_BODY if text == INK or name.startswith("black") else MIN_SECONDARY
            with self.subTest(name=name):
                self.assertGreaterEqual(contrast(text, background), floor, f"{name}: {contrast(text, background):.2f}")

    def test_monitor_tints_are_brightly_lettered_and_distinct(self):
        self.assertGreaterEqual(len(TINTS), 2)
        self.assertEqual(len(set(TINTS)), len(TINTS))
        for tint in TINTS:
            with self.subTest(tint=tint):
                self.assertIn(("ink on " + tint), TEXT_PAIRS)
                self.assertGreaterEqual(contrast(INK, tint), MIN_BODY)
                self.assertGreaterEqual(contrast(DIM, tint), MIN_BODY, "owner: Dim on tints at least 7:1")

    def test_window_outline_reads_on_every_surface(self):
        self.assertTrue(OUTLINE_PAIRS)
        for name, (line, background) in OUTLINE_PAIRS.items():
            with self.subTest(name=name):
                self.assertGreaterEqual(contrast(line, background), MIN_OUTLINE, f"{name}")

    def test_contrast_reference_values(self):
        self.assertAlmostEqual(contrast("#000000", "#FFFFFF"), 21.0, places=2)
        self.assertAlmostEqual(contrast("#777777", "#777777"), 1.0, places=2)

    def test_mix_and_rounded(self):
        self.assertEqual(mix("#000000", "#FFFFFF", 0.5), "#808080")
        self.assertEqual(len(rounded(0, 0, 100, 40, 8)), 40)


if __name__ == "__main__":
    unittest.main()
