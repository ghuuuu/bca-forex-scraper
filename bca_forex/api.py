"""A read-only JSON API, written as static files beside the chart.

Other apps — downstream services's quotation FX rate first — read rates from here instead
of scraping the chart page or touching the database:

    GET /api/v1/latest.json        newest scrape, every currency, six rates each
    GET /api/v1/history/USD.json   every stored scrape for one currency

Static files, not a server. The scraper already rewrites `web/` after every
scrape and `bca-forex-web` already serves it through the tunnel, so the API
costs no new container, no new port, and — the point — the database still never
gets mounted anywhere the tunnel can reach. The numbers here are the same ones
the chart page already embeds, so this publishes nothing new.

Every file is replaced atomically (write a temp file, then rename over), so a
client polling mid-scrape gets the old JSON or the new one, never half of one.

Zero means "not offered" on BCA's page (Bank Notes in AED, CNH, DKK…), and is
written as `null` here, the same as the chart. A client should never see a 0
rate it could multiply by.
"""

from __future__ import annotations

import json
import os
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import __version__
from .chart import _clean
from .fetcher import URL
from .parser import FIELDS
from .schedule import WIB

#: Bump only for a breaking change to the shape below; add fields freely.
API_VERSION = 1

#: When BCA's e-Rate is a tight dealing quote (~20-26 IDR spread), weekdays,
#: inclusive, in minutes since WIB midnight. BCA tightens it between 09:00 and
#: 09:15 and widens it between 15:00 and 15:15 (spread 90-120 outside), so on
#: our quarter-hour scrapes the first tight reading is 09:15 and the last 15:00.
#: Measured over Jul-Oct 2026: 58-59 of 60 weekdays at each end. This is NOT the
#: scrape schedule — that runs wider on purpose to catch the moves either side.
E_RATE_OPEN_MIN, E_RATE_CLOSE_MIN = 9 * 60 + 15, 15 * 60


def in_trading_hours(scraped_at: str) -> bool:
    """Weekday, 09:15-15:00 WIB inclusive. Public holidays are not known here."""
    t = datetime.fromisoformat(scraped_at).astimezone(WIB)
    return t.weekday() < 5 and E_RATE_OPEN_MIN <= t.hour * 60 + t.minute <= E_RATE_CLOSE_MIN


#: How much recent history calibrates each currency's tight/wide line, and the
#: least of each kind it needs before it will call one. Recent, so a change in
#: how BCA quotes is followed within weeks rather than averaged in forever.
CALIBRATION_DAYS = 60
CALIBRATION_MIN_ROWS = 50


def _spread_pct(buy: float | None, sell: float | None) -> float | None:
    if not buy or not sell:
        return None
    return (sell - buy) / ((sell + buy) / 2) * 100


def tight_threshold(rows: list[tuple[str, float | None]]) -> float | None:
    """Spread % at or under which this currency's e-Rate counts as tight.

    `rows` is (scraped_at, spread %) over recent history. One % cannot serve
    every currency — a tight USD quote is 0.11%, a tight THB one 1.15% — and
    neither can e-Rate spread / TT spread. So each currency calibrates its own
    line from history: halfway between its typical in-hours spread and the low
    end (10th percentile) of its out-of-hours spread. The low end, not the
    middle, because weekends quote wider than weeknights, and a line pulled up
    by weekends lets the weeknight quote pass as tight.

    Over Jul-Oct 2026 that called every weekday-hours reading tight for 17 of
    18 currencies, both public holidays wide, and only 6 of ~1,700 off-hours
    readings tight — each a real tight quote at 09:00 or 15:15. KRW is the
    exception: BCA barely narrows it, so its flag flips (~63%) and means little.
    """
    inside = [s for t, s in rows if s is not None and in_trading_hours(t)]
    outside = [s for t, s in rows if s is not None and not in_trading_hours(t)]
    if len(inside) < CALIBRATION_MIN_ROWS or len(outside) < CALIBRATION_MIN_ROWS:
        return None  # not enough history yet to say
    return (statistics.median(inside) + statistics.quantiles(outside, n=10)[0]) / 2


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def _rates(row: sqlite3.Row) -> dict[str, float | None]:
    return {f: _clean(row[f]) for f in FIELDS}


def write_api(conn: sqlite3.Connection, directory: Path) -> int:
    """Write latest.json and one history file per currency. Returns files written."""
    now = datetime.now(timezone.utc)
    meta = {
        "api_version": API_VERSION,
        "app_version": __version__,
        "source": URL,
        "generated": now.isoformat(timespec="seconds"),
    }

    latest = conn.execute(
        "SELECT * FROM rates WHERE scraped_at = (SELECT MAX(scraped_at) FROM rates) "
        "ORDER BY currency"
    ).fetchall()
    if not latest:
        return 0

    written = 0
    since = (now - timedelta(days=CALIBRATION_DAYS)).isoformat(timespec="seconds")
    thresholds: dict[str, float | None] = {}
    for (code,) in conn.execute("SELECT DISTINCT currency FROM rates ORDER BY currency"):
        rows = conn.execute(
            f"SELECT scraped_at, {', '.join(FIELDS)} FROM rates "
            "WHERE currency = ? ORDER BY scraped_at",
            (code,),
        ).fetchall()
        thresholds[code] = tight_threshold([
            (r["scraped_at"], _spread_pct(r["e_rate_buy"], r["e_rate_sell"]))
            for r in rows if r["scraped_at"] >= since
        ])
        # Rows as arrays, not objects: the same numbers in half the bytes.
        _write_json(directory / "history" / f"{code}.json", {
            **meta,
            "currency": code,
            "columns": ["scraped_at", *FIELDS],
            "rows": [[r["scraped_at"], *(_clean(r[f]) for f in FIELDS)] for r in rows],
        })
        written += 1

    def entry(r: sqlite3.Row) -> dict[str, object]:
        # Is this e-Rate BCA's tight dealing quote? null = not enough history
        # to judge. This, not the clock, is what to trust before locking a rate:
        # it reads wide on a public holiday, and tight on the odd 09:00.
        thr = thresholds.get(r["currency"])
        spread = _spread_pct(r["e_rate_buy"], r["e_rate_sell"])
        tight = None if thr is None or spread is None else spread <= thr
        return {**_rates(r), "e_rate_tight": tight}

    scraped_at = latest[0]["scraped_at"]
    _write_json(directory / "latest.json", {
        **meta,
        "scraped_at": scraped_at,
        "page_updated": latest[0]["page_updated"],
        # The clock's view: weekday 09:15-15:00 WIB. Per-currency e_rate_tight
        # below is the measured one, and the one to price from.
        "trading_hours": in_trading_hours(scraped_at),
        "fields": list(FIELDS),
        "rates": {r["currency"]: entry(r) for r in latest},
    })
    return written + 1
