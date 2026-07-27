"""Parser tests, run against a real page saved from BCA.

    python -m unittest discover -s tests -v      (from the folder root)
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bca_forex.parser import FIELDS, ParseError, parse, parse_number  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "bca-kurs.html"


class TestParseNumber(unittest.TestCase):
    def test_indonesian_format(self):
        # the one that matters: dots are thousands, comma is the decimal
        self.assertEqual(parse_number("17.980,00"), 17980.0)
        self.assertEqual(parse_number("1.234.567,89"), 1234567.89)

    def test_other_formats_still_read_correctly(self):
        self.assertEqual(parse_number("17980"), 17980.0)
        self.assertEqual(parse_number("17980.00"), 17980.0)
        self.assertEqual(parse_number("17,980.00"), 17980.0)

    def test_junk_is_none_not_zero(self):
        # a bad cell must never look like a real rate of 0
        for junk in ("", "   ", "-", "n/a", "abc"):
            self.assertIsNone(parse_number(junk), junk)


class TestParsePage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not FIXTURE.exists():
            raise unittest.SkipTest(f"missing fixture {FIXTURE}")
        cls.pull = parse(FIXTURE.read_text(encoding="utf-8"))

    def test_finds_the_majors(self):
        for code in ("USD", "SGD", "EUR", "JPY", "CNH", "AUD"):
            self.assertIn(code, self.pull.rates)

    def test_every_currency_has_every_rate(self):
        for code, r in self.pull.rates.items():
            for f in FIELDS:
                self.assertIsNotNone(getattr(r, f), f"{code}.{f} is missing")

    def test_sell_is_never_below_buy(self):
        # a bank sells higher than it buys; inverted means the buy/sell columns
        # were read the wrong way round, which is the costly silent failure
        for code, r in self.pull.rates.items():
            self.assertGreaterEqual(r.e_rate_sell, r.e_rate_buy, f"{code} e-Rate")
            self.assertGreaterEqual(r.tt_sell, r.tt_buy, f"{code} TT")
            self.assertGreaterEqual(r.bank_notes_sell, r.bank_notes_buy, f"{code} notes")

    def test_usd_is_in_a_sane_range(self):
        # guards against a decimal-separator misread: 17.98 or 1798000 both fail
        usd = self.pull.rates["USD"]
        self.assertTrue(5_000 < usd.e_rate_sell < 50_000, usd.e_rate_sell)

    def test_no_duplicate_rows_from_the_mobile_layout(self):
        codes = list(self.pull.rates)
        self.assertEqual(len(codes), len(set(codes)))

    def test_page_updated_captured(self):
        # optional on the page, but if present it should carry the WIB suffix
        if self.pull.page_updated:
            self.assertTrue(self.pull.page_updated.endswith("WIB"))


class TestParseFailsLoudly(unittest.TestCase):
    def test_layout_change_raises(self):
        with self.assertRaises(ParseError):
            parse("<html><body><table><tr><td>USD</td></tr></table></body></html>")


if __name__ == "__main__":
    unittest.main()
