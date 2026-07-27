"""The chart payload — the part that decides what the page is allowed to draw."""

from __future__ import annotations

import json
import re
import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bca_forex.chart import BUNDLED_LOGO, build, collect, logo_markup  # noqa: E402
from bca_forex.store import Store  # noqa: E402

FIELDS = ("e_rate_buy", "e_rate_sell", "tt_buy", "tt_sell", "bank_notes_buy", "bank_notes_sell")


def db_with(rows: list[tuple]) -> sqlite3.Connection:
    """In-memory store seeded with (scraped_at, currency, *six rates)."""
    store = Store(Path(tempfile.mkdtemp()) / "t.sqlite3")
    store.conn.executemany(
        f"INSERT INTO rates (scraped_at, currency, {', '.join(FIELDS)}) "
        f"VALUES (?, ?, {', '.join('?' * len(FIELDS))})",
        rows,
    )
    store.conn.commit()
    return store.conn


T1 = "2026-07-27T02:00:00+00:00"
T2 = "2026-07-27T02:15:00+00:00"


class TestCollect(unittest.TestCase):
    def test_zero_bank_notes_is_dropped_not_plotted(self):
        # BCA prints 0,00 for currencies it has no physical cash in. Charted as
        # a real number it drags the whole series to the axis.
        conn = db_with([(T1, "MYR", 4397.0, 4415.0, 4337.0, 4438.0, 0.0, 0.0)])
        out = collect(conn)
        self.assertNotIn("bank_notes", out["data"]["MYR"])
        self.assertIn("e_rate", out["data"]["MYR"])

    def test_a_partly_zero_series_keeps_its_real_points(self):
        conn = db_with([
            (T1, "USD", 1.0, 2.0, 1.0, 2.0, 17795.0, 18070.0),
            (T2, "USD", 1.0, 2.0, 1.0, 2.0, 0.0, 0.0),
        ])
        self.assertEqual(collect(conn)["data"]["USD"]["bank_notes"],
                         [[17795.0, None], [18070.0, None]])

    def test_points_come_out_oldest_first(self):
        conn = db_with([
            (T2, "USD", 9.0, 9.0, 9.0, 9.0, 9.0, 9.0),
            (T1, "USD", 1.0, 1.0, 1.0, 1.0, 1.0, 1.0),
        ])
        out = collect(conn)["data"]["USD"]
        self.assertEqual(out["t"], sorted(out["t"]))
        self.assertEqual(out["e_rate"][0], [1.0, 9.0])  # oldest value first

    def test_limit_keeps_the_most_recent_scrapes(self):
        rows = [
            (f"2026-07-27T0{h}:00:00+00:00", "USD", float(h), 1.0, 1.0, 1.0, 1.0, 1.0)
            for h in range(5)
        ]
        out = collect(db_with(rows), limit=2)["data"]["USD"]
        self.assertEqual(out["e_rate"][0], [3.0, 4.0])

    def test_currency_filter(self):
        conn = db_with([
            (T1, "USD", 1.0, 1.0, 1.0, 1.0, 1.0, 1.0),
            (T1, "EUR", 2.0, 2.0, 2.0, 2.0, 2.0, 2.0),
        ])
        self.assertEqual(collect(conn, currency="usd")["currencies"], ["USD"])

    def test_timestamps_are_epoch_seconds(self):
        conn = db_with([(T1, "USD", 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)])
        # 2026-07-27T02:00:00Z — 09:00 WIB, the slot this would have run in
        self.assertEqual(collect(conn)["data"]["USD"]["t"], [1785117600])


class TestBuild(unittest.TestCase):
    def test_output_is_one_self_contained_file(self):
        conn = db_with([(T1, "USD", 17980.0, 18000.0, 17795.0, 18070.0, 17795.0, 18070.0)])
        out = Path(tempfile.mkdtemp()) / "c.html"
        path, currencies, points = build(conn, out)
        html = path.read_text(encoding="utf-8")

        self.assertEqual((currencies, points), (1, 1))
        # nothing may be fetched at view time — the file has to work offline
        self.assertNotIn("<script src", html)
        self.assertNotIn("<link rel=\"stylesheet\"", html)
        self.assertNotIn("http://", html.split("</head>")[0])

    def test_the_data_placeholder_is_replaced_with_valid_json(self):
        conn = db_with([(T1, "USD", 17980.0, 18000.0, 17795.0, 18070.0, 0.0, 0.0)])
        out = Path(tempfile.mkdtemp()) / "c.html"
        html = build(conn, out)[0].read_text(encoding="utf-8")

        self.assertNotIn("/*__DATA__*/null", html)
        m = re.search(r"const DATA = (\{.*?\});\n", html, re.S)
        assert m is not None, "embedded payload not found"
        payload = json.loads(m.group(1))
        self.assertEqual(payload["currencies"], ["USD"])
        self.assertEqual(payload["data"]["USD"]["e_rate"], [[17980.0], [18000.0]])
        self.assertNotIn("bank_notes", payload["data"]["USD"])


class TestLogo(unittest.TestCase):
    def test_the_official_asset_ships_with_the_package(self):
        # the Docker image copies only bca_forex/, so the logo has to live in it
        self.assertTrue(BUNDLED_LOGO.exists(), f"missing {BUNDLED_LOGO}")
        self.assertIn("svg", BUNDLED_LOGO.read_text(encoding="utf-8")[:200].lower())

    def test_no_argument_uses_the_bundled_logo(self):
        markup = logo_markup()
        self.assertIn("<svg", markup)
        self.assertIn('class="plate"', markup)

    def test_svg_is_inlined_rather_than_base64(self):
        # base64 would cost a third again on a 70 KB asset for no benefit
        p = Path(tempfile.mkdtemp()) / "logo.svg"
        p.write_text(
            "<?xml version='1.0'?><svg xmlns='http://www.w3.org/2000/svg'>"
            "<title>atoms/icon/general/logo</title><path d='M0 0'/></svg>",
            encoding="utf-8",
        )
        markup = logo_markup(p)
        self.assertIn("<path", markup)
        self.assertNotIn("base64", markup)
        self.assertNotIn("<?xml", markup)          # invalid inside an html body
        self.assertNotIn("atoms/icon", markup)     # would show as a tooltip

    def test_raster_is_inlined_as_a_data_uri(self):
        p = Path(tempfile.mkdtemp()) / "logo.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        markup = logo_markup(p)
        self.assertIn("data:image/png;base64,", markup)
        # linking would leave a broken image the moment the html travels alone
        self.assertNotIn('src="logo.png"', markup)

    def test_an_unsupported_format_says_what_is_allowed(self):
        p = Path(tempfile.mkdtemp()) / "logo.pdf"
        p.write_bytes(b"%PDF")
        with self.assertRaises(ValueError) as cm:
            logo_markup(p)
        self.assertIn(".svg", str(cm.exception))

    def test_the_headline_defaults_to_e_rate_sell(self):
        # sell is the number you pay to buy foreign currency, so it is the one
        # worth leading with; buy stays one pick away in the dropdown
        conn = db_with([(T1, "USD", 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)])
        out = Path(tempfile.mkdtemp()) / "c.html"
        html = build(conn, out)[0].read_text(encoding="utf-8")
        self.assertIn('metric: { key: "e_rate", side: 1 }', html)

    def test_every_series_and_side_is_offered_as_a_headline(self):
        conn = db_with([(T1, "USD", 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)])
        out = Path(tempfile.mkdtemp()) / "c.html"
        html = build(conn, out)[0].read_text(encoding="utf-8")
        # the picker is built from DATA.series x [buy, sell] at runtime
        self.assertIn('SIDES = ["Buy", "Sell"]', html)
        self.assertIn("${s.key}:${side}", html)

    def test_the_placeholder_is_gone_from_the_built_page(self):
        conn = db_with([(T1, "USD", 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)])
        out = Path(tempfile.mkdtemp()) / "c.html"
        html = build(conn, out)[0].read_text(encoding="utf-8")
        self.assertNotIn("__LOGO__", html)


if __name__ == "__main__":
    unittest.main()
