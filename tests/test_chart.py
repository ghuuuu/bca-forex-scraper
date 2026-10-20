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

from bca_forex.chart import (  # noqa: E402
    BUNDLED_ICON, BUNDLED_LOGO, PWA_ICONS, app_icon_svg, build, collect, logo_markup,
    write_pwa,
)
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
        m = re.search(r"let DATA = (\{.*?\});\n", html, re.S)
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
        # reversed out white on the dark header: recoloured, no plate
        self.assertIn('fill="currentColor"', markup)
        self.assertNotIn('fill="#039"', markup)
        self.assertNotIn('class="plate"', markup)

    def test_a_supplied_logo_keeps_its_white_plate(self):
        p = Path(tempfile.mkdtemp()) / "logo.svg"
        p.write_text("<svg><path d='M0 0'/></svg>", encoding="utf-8")
        self.assertIn('class="plate"', logo_markup(p))

    def test_the_app_icon_is_the_white_mark_on_blue(self):
        svg = app_icon_svg()
        self.assertIn('fill="#fff"', svg)
        self.assertNotIn('fill="#039"', svg)
        self.assertEqual(svg.count("<path"), BUNDLED_ICON.read_text(encoding="utf-8").count("<path"))

    def test_the_page_carries_its_favicon_inline(self):
        conn = db_with([(T1, "USD", 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)])
        html = build(conn, Path(tempfile.mkdtemp()) / "c.html")[0].read_text(encoding="utf-8")
        self.assertIn('rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,', html)
        self.assertNotIn("__ICON__", html)

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
        # a rate card per DATA.series picks the series, the Buy / Sell segment
        # the side, both at runtime
        self.assertIn('SIDES = ["Buy", "Sell"]', html)
        self.assertIn('data-k="${s.key}"', html)
        self.assertIn('data-side="${side}"', html)
        self.assertIn("setMetric(b.dataset.k, state.metric.side)", html)

    def test_the_placeholder_is_gone_from_the_built_page(self):
        conn = db_with([(T1, "USD", 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)])
        out = Path(tempfile.mkdtemp()) / "c.html"
        html = build(conn, out)[0].read_text(encoding="utf-8")
        self.assertNotIn("__LOGO__", html)


class TestPWA(unittest.TestCase):
    def test_icons_ship_with_the_package(self):
        # iOS will not take an SVG home-screen icon, so these have to be real
        # PNGs and have to be inside bca_forex/ for the Docker image to get them
        for name in PWA_ICONS:
            p = BUNDLED_LOGO.parent / name
            self.assertTrue(p.exists(), f"missing {p} — run tools/make_icons.py")
            self.assertEqual(p.read_bytes()[:8], b"\x89PNG\r\n\x1a\n", f"{name} is not a PNG")

    def test_write_pwa_emits_the_whole_set(self):
        d = Path(tempfile.mkdtemp())
        written = write_pwa(d)
        names = {p.name for p in written}
        self.assertEqual(names, {"manifest.webmanifest", "sw.js", *PWA_ICONS})

    def test_manifest_is_valid_and_standalone(self):
        d = Path(tempfile.mkdtemp())
        write_pwa(d)
        man = json.loads((d / "manifest.webmanifest").read_text(encoding="utf-8"))
        self.assertEqual(man["display"], "standalone")
        self.assertTrue(any(i.get("purpose") == "maskable" for i in man["icons"]))

    def test_service_worker_refuses_to_cache_a_login_redirect(self):
        # behind Cloudflare Access an expired session answers with a login page;
        # caching it would pin the app to a screen it cannot get past
        d = Path(tempfile.mkdtemp())
        write_pwa(d)
        sw = (d / "sw.js").read_text(encoding="utf-8")
        self.assertIn("res.redirected", sw)
        self.assertIn("opaqueredirect", sw)

    def test_pwa_flag_adds_the_head_tags(self):
        conn = db_with([(T1, "USD", 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)])
        d = Path(tempfile.mkdtemp())
        html = build(conn, d / "index.html", pwa=True)[0].read_text(encoding="utf-8")
        self.assertIn('rel="manifest"', html)
        self.assertIn("serviceWorker", html)
        self.assertIn("apple-mobile-web-app-capable", html)

    def test_without_the_flag_nothing_is_referenced_or_written(self):
        # the standalone file has to stay standalone — referencing a manifest
        # that is not beside it would 404 on every load from a USB stick
        conn = db_with([(T1, "USD", 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)])
        d = Path(tempfile.mkdtemp())
        html = build(conn, d / "chart.html")[0].read_text(encoding="utf-8")
        for token in ("manifest", "sw.js", "serviceWorker", "__PWA__"):
            self.assertNotIn(token, html)
        self.assertEqual([p.name for p in d.iterdir()], ["chart.html"])


if __name__ == "__main__":
    unittest.main()
