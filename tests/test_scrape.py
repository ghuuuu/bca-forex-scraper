"""Change-only storage, the hourly heartbeat, and reading only the rate table."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bca_forex.__main__ import scrape_once  # noqa: E402
from bca_forex.api import write_api  # noqa: E402
from bca_forex.fetcher import table_end  # noqa: E402
from bca_forex.parser import parse  # noqa: E402
from bca_forex.store import Store  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bca-kurs.html"


def rates(pull):
    return {k: v.as_dict() for k, v in pull.rates.items()}


class TestTableEnd(unittest.TestCase):
    def test_the_table_alone_parses_like_the_whole_page(self):
        raw = FIXTURE.read_bytes()
        end = table_end(raw)
        self.assertGreater(end, 0)
        self.assertLess(end, len(raw) * 0.6)   # the point: most of the page is skipped
        cut = parse(raw[:end].decode("utf-8"))
        self.assertEqual(rates(cut), rates(parse(raw.decode("utf-8"))))
        self.assertIsNotNone(cut.page_updated)

    def test_not_there_yet(self):
        self.assertEqual(table_end(b"<html><body>"), -1)
        self.assertEqual(table_end(b'<td rate-type="eRate-buy"><p>1</p>'), -1)


class TestChangeOnly(unittest.TestCase):
    def setUp(self):
        self.store = Store(Path(tempfile.mkdtemp()) / "t.sqlite3")
        self.html = str(FIXTURE)
        self.quiet = redirect_stdout(io.StringIO())
        self.quiet.__enter__()

    def tearDown(self):
        self.quiet.__exit__(None, None, None)
        self.store.close()

    def rows(self):
        return self.store.conn.execute("SELECT COUNT(*) FROM rates").fetchone()[0]

    def test_a_repeat_stores_nothing_but_a_heartbeat_stores_all(self):
        last = {}
        read, stored = scrape_once(self.store, self.html, heartbeat=False, last=last)
        self.assertEqual((read, stored), (18, 18))      # first sight: all new
        self.assertEqual(scrape_once(self.store, self.html, heartbeat=False, last=last), (18, 0))
        self.assertEqual(self.rows(), 18)
        # every run is still logged, stored or not
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM runs WHERE ok = 1").fetchone()[0], 2)

    def test_only_the_currency_that_moved_is_stored(self):
        last = self.store.last_values()
        scrape_once(self.store, self.html, heartbeat=True, last=last)
        last["USD"] = (1, 2, 3, 4, 5, 6)                 # pretend USD was different before
        time.sleep(1.1)                                  # rows are keyed by the second
        _, stored = scrape_once(self.store, self.html, heartbeat=False, last=last)
        self.assertEqual(stored, 1)
        self.assertEqual(self.store.last_values()["USD"], last["USD"])

    def test_latest_is_per_currency(self):
        last = {}
        scrape_once(self.store, self.html, heartbeat=True, last=last)
        first = self.store.conn.execute("SELECT MAX(scraped_at) FROM rates").fetchone()[0]
        last["USD"] = None
        time.sleep(1.1)                                  # rows are keyed by the second
        scrape_once(self.store, self.html, heartbeat=False, last=last)
        latest = {r["currency"]: r["scraped_at"] for r in self.store.latest()}
        self.assertEqual(len(latest), 18)                # nobody dropped out
        self.assertGreater(latest["USD"], first)
        self.assertEqual(latest["EUR"], first)

        out = Path(tempfile.mkdtemp())
        write_api(self.store.conn, out)
        api = json.loads((out / "latest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(api["rates"]), 18)
        self.assertEqual(api["rates"]["EUR"]["scraped_at"], first)
        self.assertEqual(api["scraped_at"], latest["USD"])
        self.assertGreaterEqual(api["checked_at"], api["scraped_at"])


if __name__ == "__main__":
    unittest.main()
