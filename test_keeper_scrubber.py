# Last modified: 2026-10-21 | Change: Tests for the film-strip time scrubber stop mapping | Full history: git log
"""Pure logic only: the scrubber's stops are exactly the Saved layout chooser's entries, in time order."""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from keeper_chooser import captured, chooser_entries, clock
from keeper_scrubber import nearest_stop, reboot_marks, scrubber_stops, short_label, strip_note, tick_positions

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


def snap(moment, boot):
    return {"captured_at": moment.isoformat(), "boot_id": boot, "windows": []}


# WHAT IT DOES: A newest-first history: every 2 minutes for 30 hours, reboot 2 hours ago.
def history(hours=30, reboot_minutes=120):
    items = []
    for minutes in range(0, 60 * hours, 2):
        boot = "boot-2" if minutes < reboot_minutes else "boot-1"
        items.append((Path(f"s{minutes}.json"), snap(NOW - timedelta(minutes=minutes), boot)))
    return items


class ScrubberStopTests(unittest.TestCase):
    def test_stops_are_the_chooser_entries_in_time_order(self):
        saved = history()
        entries = chooser_entries(saved, NOW)
        stops = scrubber_stops(saved, entries)
        self.assertEqual(sorted(stop["index"] for stop in stops), sorted(index for _, index in entries))
        times = [stop["time"] for stop in stops]
        self.assertEqual(times, sorted(times))
        self.assertEqual(stops[-1]["index"], 0)
        self.assertEqual(stops[-1]["label"], "Latest")
        xs = [stop["x"] for stop in stops]
        self.assertEqual(xs, sorted(xs))
        self.assertTrue(all(0 < x < 1 for x in xs))

    def test_short_labels(self):
        self.assertEqual(short_label("Latest (3:42 PM)"), "Latest")
        self.assertEqual(short_label("15 min ago (3:27 PM)"), "15 min")
        self.assertEqual(short_label("1 hour ago (2:42 PM)"), "1 hr")
        self.assertEqual(short_label("3 hours ago (12:42 PM)"), "3 hr")
        self.assertEqual(short_label("Before last reboot (Oct 19, 9:58 AM)"), "Before reboot")
        self.assertEqual(short_label("Today 9:05 AM (first today)"), "Start of today")
        self.assertEqual(short_label("Yesterday 6:10 PM (last that day)"), "Yesterday")
        self.assertEqual(short_label("Fri Oct 16 6:10 PM (last that day)"), "Fri Oct 16")
        self.assertEqual(short_label("Old prototype capture"), "Prototype")

    def test_missing_entry_has_no_stop(self):
        saved = history(hours=1, reboot_minutes=999)
        stops = scrubber_stops(saved, chooser_entries(saved, NOW))
        labels = [stop["label"] for stop in stops]
        self.assertNotIn("Before reboot", labels)
        self.assertNotIn("Yesterday", labels)
        self.assertEqual(reboot_marks(stops), [])

    def test_reboot_splice_sits_after_the_before_reboot_stop(self):
        saved = history()
        stops = scrubber_stops(saved, chooser_entries(saved, NOW))
        marks = reboot_marks(stops)
        self.assertEqual(len(marks), 1)
        before = next(number for number, stop in enumerate(stops) if stop["label"] == "Before reboot")
        self.assertLess(stops[before]["x"], marks[0])
        self.assertLess(marks[0], stops[before + 1]["x"])

    def test_single_snapshot_is_one_stop(self):
        saved = [(Path("only.json"), snap(NOW, "b"))]
        stops = scrubber_stops(saved, chooser_entries(saved, NOW))
        self.assertEqual(len(stops), 1)
        self.assertEqual(nearest_stop(stops, 0.0), 0)

    def test_one_or_two_stops_show_their_clock_time(self):
        first = snap(NOW, "b")
        saved = [(Path("only.json"), first)]
        stops = scrubber_stops(saved, chooser_entries(saved, NOW))
        self.assertEqual(stops[0]["label"], f"Latest · {clock(captured(first))}")
        self.assertEqual(strip_note(stops), "Only one saved layout so far")
        saved = [(Path("a.json"), first), (Path("b.json"), snap(NOW - timedelta(minutes=30), "b"))]
        stops = scrubber_stops(saved, chooser_entries(saved, NOW))
        self.assertEqual(len(stops), 2)
        self.assertTrue(all(" · " in stop["label"] for stop in stops))
        self.assertEqual(strip_note(stops), "")

    def test_many_stops_keep_short_labels(self):
        saved = history()
        stops = scrubber_stops(saved, chooser_entries(saved, NOW))
        self.assertTrue(all(" · " not in stop["label"] for stop in stops))

    def test_nearest_stop_and_ticks(self):
        saved = history()
        stops = scrubber_stops(saved, chooser_entries(saved, NOW))
        self.assertEqual(nearest_stop(stops, 0.0), 0)
        self.assertEqual(nearest_stop(stops, 1.0), len(stops) - 1)
        self.assertEqual(nearest_stop(stops, stops[2]["x"] + 0.001), 2)
        ticks = tick_positions(saved, stops)
        self.assertTrue(ticks)
        self.assertTrue(all(stops[0]["x"] <= tick <= stops[-1]["x"] for tick in ticks))


if __name__ == "__main__":
    unittest.main()
