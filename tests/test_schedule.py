"""The slot maths — the part that must not drift.

Three cadences: trading hours (Mon-Fri 08:00-16:00 WIB) every 15 min, weeknights
hourly, weekends every three hours. 2026-07-27 is a Monday, 2026-07-25/26 the
weekend before it, and 2026-07-24 the Friday, which the cases below rely on.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bca_forex.schedule import WIB, is_active, next_slot, seconds_until_next  # noqa: E402


def at(h: int, m: int, s: int = 0, day: int = 27) -> datetime:
    return datetime(2026, 7, day, h, m, s, tzinfo=WIB)


class TestIsActive(unittest.TestCase):
    def test_trading_hours_on_a_weekday(self):
        self.assertTrue(is_active(at(8, 0)))
        self.assertTrue(is_active(at(12, 30)))
        self.assertTrue(is_active(at(15, 59)))

    def test_the_edges(self):
        self.assertFalse(is_active(at(7, 59)))    # before open
        self.assertFalse(is_active(at(16, 0)))    # 16:00 is closed (caught hourly)

    def test_weekends_are_always_closed(self):
        self.assertFalse(is_active(at(12, 0, day=25)))  # Saturday
        self.assertFalse(is_active(at(12, 0, day=26)))  # Sunday


class TestNextSlotTradingHours(unittest.TestCase):
    def test_quarter_hour_cadence(self):
        self.assertEqual(next_slot(at(8, 0, 30)), at(8, 15))
        self.assertEqual(next_slot(at(8, 20)), at(8, 30))

    def test_exactly_on_a_slot_moves_to_the_following_one(self):
        # strictly-after, so finishing a scrape at :15:00 cannot re-fire it
        self.assertEqual(next_slot(at(8, 15)), at(8, 30))
        self.assertEqual(next_slot(at(8, 45)), at(9, 0))

    def test_the_trading_pattern(self):
        t = at(7, 59)
        got = []
        for _ in range(5):
            t = next_slot(t)
            got.append(f"{t:%H:%M}")
        self.assertEqual(got, ["08:00", "08:15", "08:30", "08:45", "09:00"])


class TestNextSlotClosed(unittest.TestCase):
    def test_hourly_overnight(self):
        # after the 15:45 trading slot, 16:00 is the first hourly one (the close)
        self.assertEqual(next_slot(at(15, 45)), at(16, 0))
        self.assertEqual(next_slot(at(16, 0)), at(17, 0))
        self.assertEqual(next_slot(at(18, 30)), at(19, 0))

    def test_quarter_marks_are_skipped_when_closed(self):
        # 20:15 / 20:30 are not slots overnight — next is 21:00
        self.assertEqual(next_slot(at(20, 15)), at(21, 0))

    def test_morning_reopens_at_15_min(self):
        # last overnight slot 07:00, then trading resumes at 08:00, 08:15…
        self.assertEqual(next_slot(at(7, 0)), at(8, 0))
        self.assertEqual(next_slot(at(8, 0)), at(8, 15))

    def test_across_midnight_stays_hourly(self):
        self.assertEqual(next_slot(at(23, 30)), datetime(2026, 7, 28, 0, 0, tzinfo=WIB))


class TestNextSlotWeekend(unittest.TestCase):
    def test_saturday_is_every_three_hours(self):
        # 09:00 is a slot; 10:20 falls between slots, next is 12:00
        self.assertEqual(next_slot(at(9, 0, day=25)), at(12, 0, day=25))
        self.assertEqual(next_slot(at(10, 20, day=25)), at(12, 0, day=25))

    def test_off_step_weekend_hours_are_skipped(self):
        # 10:00 and 11:00 are not weekend slots (only 00,03,…,21) — next is 12:00
        self.assertEqual(next_slot(at(10, 0, day=25)), at(12, 0, day=25))
        self.assertEqual(next_slot(at(11, 0, day=25)), at(12, 0, day=25))

    def test_the_weekend_pattern(self):
        t = at(23, 30, day=24)  # Fri night, into Saturday
        got = []
        for _ in range(5):
            t = next_slot(t)
            got.append(f"{t:%a %H:%M}")
        self.assertEqual(got, ["Sat 00:00", "Sat 03:00", "Sat 06:00", "Sat 09:00", "Sat 12:00"])

    def test_friday_close_into_the_weekend(self):
        # Fri 24th 15:45 -> 16:00 (hourly close), then Fri stays hourly (weeknight)
        self.assertEqual(next_slot(at(15, 45, day=24)), at(16, 0, day=24))
        self.assertEqual(next_slot(at(20, 0, day=24)), at(21, 0, day=24))  # Fri: hourly
        # last Friday hour rolls into the first weekend slot, Sat 00:00
        self.assertEqual(next_slot(at(23, 0, day=24)), datetime(2026, 7, 25, 0, 0, tzinfo=WIB))

    def test_monday_open_after_the_weekend(self):
        # Sunday 26th: three-hourly. 07:30 -> 09:00 (not 08:00 — Sunday isn't trading)
        self.assertEqual(next_slot(at(7, 30, day=26)), at(9, 0, day=26))
        self.assertEqual(next_slot(at(21, 0, day=26)), at(0, 0, day=27))  # Sun 21:00 -> Mon 00:00
        # Monday 27th: weeknight hourly overnight, then 15-min from 08:00
        self.assertEqual(next_slot(at(0, 0, day=27)), at(1, 0, day=27))
        self.assertEqual(next_slot(at(7, 0, day=27)), at(8, 0, day=27))
        self.assertEqual(next_slot(at(8, 0, day=27)), at(8, 15, day=27))


class TestSeconds(unittest.TestCase):
    def test_wait_is_never_negative(self):
        self.assertGreaterEqual(seconds_until_next(at(8, 14, 59)), 0)
        self.assertAlmostEqual(seconds_until_next(at(8, 14, 59)), 1.0, places=3)


if __name__ == "__main__":
    unittest.main()
