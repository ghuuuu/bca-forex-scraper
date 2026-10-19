"""The JSON API — the contract other apps (downstream services) price against."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bca_forex.api import write_api  # noqa: E402
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

    def test_no_temp_files_left_behind(self):
        self.assertEqual(list(self.out.rglob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
