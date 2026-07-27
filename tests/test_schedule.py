"""The slot maths — the part that must not drift."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bca_forex.schedule import WIB, next_slot, seconds_until_next  # noqa: E402


def at(h: int, m: int, s: int = 0) -> datetime:
    return datetime(2026, 7, 27, h, m, s, tzinfo=WIB)


class TestNextSlot(unittest.TestCase):
    def test_mid_slot_rounds_up_to_the_next_quarter(self):
        self.assertEqual(next_slot(at(8, 0, 30)), at(8, 15))
        self.assertEqual(next_slot(at(8, 20)), at(8, 30))

    def test_after_the_last_slot_rolls_to_the_next_hour(self):
        self.assertEqual(next_slot(at(8, 45, 1)), at(9, 0))
        self.assertEqual(next_slot(at(8, 59)), at(9, 0))

    def test_exactly_on_a_slot_moves_to_the_following_one(self):
        # strictly-after, so finishing a scrape at :15:00 cannot re-fire it
        self.assertEqual(next_slot(at(8, 15)), at(8, 30))
        self.assertEqual(next_slot(at(8, 45)), at(9, 0))

    def test_across_midnight(self):
        self.assertEqual(next_slot(at(23, 59)), datetime(2026, 7, 28, 0, 0, tzinfo=WIB))

    def test_the_schedule_pattern(self):
        # 8:00, 8:15, 8:30, 8:45 … every quarter hour, on the hour, never :01
        t = at(7, 59)
        got = []
        for _ in range(5):
            t = next_slot(t)
            got.append(f"{t:%H:%M}")
        self.assertEqual(got, ["08:00", "08:15", "08:30", "08:45", "09:00"])

    def test_every_slot_of_the_hour_is_a_quarter(self):
        t = at(8, 59, 59)
        got = []
        for _ in range(4):
            t = next_slot(t)
            got.append(t.minute)
        self.assertEqual(got, [0, 15, 30, 45])

    def test_wait_is_never_negative(self):
        self.assertGreaterEqual(seconds_until_next(at(8, 14, 59)), 0)
        self.assertAlmostEqual(seconds_until_next(at(8, 14, 59)), 1.0, places=3)


if __name__ == "__main__":
    unittest.main()
