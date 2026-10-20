"""A read-only JSON API, written as static files beside the chart.

Other apps — downstream services's quotation FX rate first — read rates from here instead
of scraping the chart page or touching the database:

    GET /api/v1/latest.json        each currency's newest rates, six each
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
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .chart import _clean
from .fetcher import URL
from .parser import FIELDS
from .schedule import WIB
from .store import latest_rows

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


#: The least history of each kind (in hours, out of hours) a currency needs
#: before its tight/wide line is drawn.
CALIBRATION_MIN_ROWS = 50

#: Within this factor of the usual in-hours spread always counts as tight.
TIGHT_TOLERANCE = 1.2

#: The three rates BCA quotes, each a _buy/_sell pair in FIELDS.
KINDS: tuple[str, ...] = tuple(dict.fromkeys(f.rsplit("_", 1)[0] for f in FIELDS))
TIGHT_FIELDS: tuple[str, ...] = tuple(f"{k}_tight" for k in KINDS)


def _spread_pct(buy: float | None, sell: float | None) -> float | None:
    if not buy or not sell:
        return None
    return (sell - buy) / ((sell + buy) / 2) * 100


def tight_threshold(rows: list[tuple[str, float | None]]) -> float | None:
    """Spread % at or under which one currency's rate counts as tight.

    `rows` is (scraped_at, spread %) over the stored history of one currency
    and one rate (e-Rate, TT or Bank Notes). The line is

        max( (m + p10) / 2,  m * TIGHT_TOLERANCE )

    m   = median spread in trading hours, the rate's normal tight quote
    p10 = 10th percentile out of hours, the low end of its wide quote

    One % cannot serve every currency — a tight USD e-Rate is 0.11%, a tight
    THB one 1.15% — so each learns its own m and p10. The first term splits
    the e-Rate's two quotes halfway; p10, not the median, because weekends
    quote wider than weeknights, and a line pulled up by weekends lets the
    weeknight quote pass as tight. The second term is for TT and Bank Notes,
    which keep one fixed margin round the clock (USD TT 1.57% in and out of
    hours): with nothing to split, the first term would cut that one quote in
    half by noise. For them a reading is tight unless BCA posts an abnormal
    spread — it says nothing about how fresh the rate is (their mid moves
    only a few times a day).

    Over Jul-Oct 2026 on the e-Rate: every weekday-hours reading tight for 17
    of 18 currencies, both public holidays wide, and a handful of off-hours
    readings tight — each a real tight quote at 09:00 or 15:15. KRW is the
    exception: BCA barely narrows it (6.4% vs 6.8%), so it reads tight always.
    """
    inside = [s for t, s in rows if s is not None and in_trading_hours(t)]
    outside = [s for t, s in rows if s is not None and not in_trading_hours(t)]
    if len(inside) < CALIBRATION_MIN_ROWS or len(outside) < CALIBRATION_MIN_ROWS:
        return None  # not enough history yet to say
    m = statistics.median(inside)
    return max((m + statistics.quantiles(outside, n=10)[0]) / 2, m * TIGHT_TOLERANCE)


def is_tight(threshold: float | None, buy: float | None, sell: float | None) -> bool | None:
    """True/False for one reading; None if the rate is not offered or the line
    is not drawn yet."""
    spread = _spread_pct(buy, sell)
    return None if threshold is None or spread is None else spread <= threshold


def e_rate_open(conn: sqlite3.Connection, code: str = "USD") -> bool | None:
    """Is BCA dealing? True when the newest `code` e-Rate is the tight quote.

    Every currency's e-Rate flips tight and wide on the same read (KRW aside,
    which reads tight always), so USD speaks for the market. None until there
    is history to judge.
    """
    rows = conn.execute(
        "SELECT scraped_at, e_rate_buy, e_rate_sell FROM rates WHERE currency = ? "
        "ORDER BY scraped_at", (code,),
    ).fetchall()
    if not rows:
        return None
    thr = tight_threshold([(r[0], _spread_pct(r[1], r[2])) for r in rows])
    return is_tight(thr, rows[-1][1], rows[-1][2])


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

    # Rows are stored only when a rate changes (plus an hourly heartbeat), so
    # each currency's newest row has its own time.
    latest = latest_rows(conn)
    if not latest:
        return 0

    written = 0
    thresholds: dict[str, dict[str, float | None]] = {}

    # Is each rate BCA's tight quote? null = not offered, or not enough history
    # to judge. This, not the clock, is what to trust before locking a rate:
    # it reads wide on a public holiday, and tight on the odd 09:00. One line
    # per currency and rate, drawn from all stored history and applied to every
    # row, so past readings carry the flag too.
    def flags(r: sqlite3.Row) -> list[bool | None]:
        thr = thresholds[r["currency"]]
        return [is_tight(thr[k], r[f"{k}_buy"], r[f"{k}_sell"]) for k in KINDS]

    for (code,) in conn.execute("SELECT DISTINCT currency FROM rates ORDER BY currency"):
        rows = conn.execute(
            f"SELECT scraped_at, currency, {', '.join(FIELDS)} FROM rates "
            "WHERE currency = ? ORDER BY scraped_at",
            (code,),
        ).fetchall()
        thresholds[code] = {
            k: tight_threshold([
                (r["scraped_at"], _spread_pct(r[f"{k}_buy"], r[f"{k}_sell"])) for r in rows
            ])
            for k in KINDS
        }
        # Rows as arrays, not objects: the same numbers in half the bytes.
        _write_json(directory / "history" / f"{code}.json", {
            **meta,
            "currency": code,
            "columns": ["scraped_at", *FIELDS, *TIGHT_FIELDS],
            "rows": [
                [r["scraped_at"], *(_clean(r[f]) for f in FIELDS), *flags(r)] for r in rows
            ],
        })
        written += 1

    def entry(r: sqlite3.Row) -> dict[str, object]:
        # scraped_at here is when this rate last CHANGED (or the last hourly
        # heartbeat); it was still current at checked_at.
        return {**_rates(r), **dict(zip(TIGHT_FIELDS, flags(r))), "scraped_at": r["scraped_at"]}

    newest = max(latest, key=lambda r: r["scraped_at"])
    ok = conn.execute("SELECT MAX(started_at) FROM runs WHERE ok = 1").fetchone()[0]
    # The last successful read of BCA's page, changed or not. Never older than
    # the newest row, which a run that stored it must have come after.
    checked_at = max(filter(None, [ok, newest["scraped_at"]]))
    _write_json(directory / "latest.json", {
        **meta,
        "checked_at": checked_at,
        "scraped_at": newest["scraped_at"],
        "page_updated": newest["page_updated"],
        # The clock's view: weekday 09:15-15:00 WIB. Per-currency e_rate_tight
        # below is the measured one, and the one to price from.
        "trading_hours": in_trading_hours(checked_at),
        "fields": list(FIELDS),
        "rates": {r["currency"]: entry(r) for r in latest},
    })
    return written + 1
