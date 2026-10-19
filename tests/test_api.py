"""The JSON API — the contract other apps (downstream services) price against."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bca_forex.api import in_trading_hours, tight_threshold, write_api  # noqa: E402
from test_chart import FIELDS, db_with  # noqa: E402

# 09:30 WIB on a Monday (trading), then 20:00 WIB the same day (closed).
T1 = "2026-10-19T02:30:00+00:00"
T2 = "2026-10-19T13:00:00+00:00"


class TestWriteApi(unittest.TestCase):
    def setUp(self):
        self.conn = db_with([
            (T1, "USD", 18030, 18050, 17830, 18110, 17900, 18200),
            (T1, "AED", 4900, 4930, 4800, 5000, 0, 0),
            (T2, "USD", 17990, 18080, 17820, 18100, 17900, 18200),
            (T2, "AED", 4901, 4931, 4800, 5000, 0, 0),
        ])
        self.out = Path(tempfile.mkdtemp())
        self.assertEqual(write_api(self.conn, self.out), 3)

    def read(self, name: str) -> dict:
        return json.loads((self.out / name).read_text(encoding="utf-8"))

    def test_latest_is_newest_scrape_only(self):
        latest = self.read("latest.json")
        self.assertEqual(latest["scraped_at"], T2)
        self.assertFalse(latest["trading_hours"])
        self.assertEqual(latest["rates"]["USD"]["e_rate_sell"], 18080)
        self.assertEqual(sorted(latest["rates"]), ["AED", "USD"])

    def test_zero_is_null_never_a_rate(self):
        self.assertIsNone(self.read("latest.json")["rates"]["AED"]["bank_notes_sell"])
        self.assertIsNone(self.read("history/AED.json")["rows"][0][-1])

    def test_history_oldest_first_with_columns(self):
        h = self.read("history/USD.json")
        self.assertEqual(h["columns"], ["scraped_at", *FIELDS])
        self.assertEqual([r[0] for r in h["rows"]], [T1, T2])
        self.assertEqual(h["rows"][0][2], 18050)

    def test_trading_hours_is_the_tight_e_rate_window(self):
        # UTC stamps; WIB is +7. Mon 19 Oct 2026, then Sat 17 Oct.
        cases = {
            "2026-10-19T02:00:20+00:00": False,  # 09:00 — still the wide quote
            "2026-10-19T02:15:20+00:00": True,   # 09:15 — first tight reading
            "2026-10-19T08:00:20+00:00": True,   # 15:00 — last tight reading
            "2026-10-19T08:15:20+00:00": False,  # 15:15
            "2026-10-17T04:00:00+00:00": False,  # Saturday 11:00
        }
        for ts, want in cases.items():
            self.assertEqual(in_trading_hours(ts), want, ts)

    def test_tight_is_null_until_there_is_history_to_judge(self):
        self.assertIsNone(self.read("latest.json")["rates"]["USD"]["e_rate_tight"])

    def test_tight_threshold_splits_dealing_from_overnight_quote(self):
        inside = [(f"2026-10-19T03:{m:02d}:00+00:00", 0.11) for m in range(60)]  # 10:xx WIB
        weeknight = [(f"2026-10-19T14:{m:02d}:00+00:00", 0.50) for m in range(60)]  # 21:xx WIB
        weekend = [(f"2026-10-17T14:{m:02d}:00+00:00", 1.60) for m in range(60)]   # Sat
        thr = tight_threshold(inside + weeknight + weekend)
        # Must fall between the dealing quote and the WEEKNIGHT one — a line
        # pulled up by weekends would wave the weeknight quote through.
        self.assertTrue(0.11 < thr < 0.50, thr)
        self.assertIsNone(tight_threshold(inside[:10] + weeknight))

    def test_no_temp_files_left_behind(self):
        self.assertEqual(list(self.out.rglob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
