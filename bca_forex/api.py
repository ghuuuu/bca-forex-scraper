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
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .chart import _clean
from .fetcher import URL
from .parser import FIELDS
from .schedule import is_active

#: Bump only for a breaking change to the shape below; add fields freely.
API_VERSION = 1


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
    scraped_at = latest[0]["scraped_at"]
    _write_json(directory / "latest.json", {
        **meta,
        "scraped_at": scraped_at,
        "page_updated": latest[0]["page_updated"],
        # BCA's e-Rate is a tight quote only in trading hours; outside them the
        # spread widens ~5x. A consumer pricing off e_rate_sell needs to know.
        "trading_hours": is_active(datetime.fromisoformat(scraped_at)),
        "fields": list(FIELDS),
        "rates": {r["currency"]: _rates(r) for r in latest},
    })

    written = 1
    for (code,) in conn.execute("SELECT DISTINCT currency FROM rates ORDER BY currency"):
        rows = conn.execute(
            f"SELECT scraped_at, {', '.join(FIELDS)} FROM rates "
            "WHERE currency = ? ORDER BY scraped_at",
            (code,),
        ).fetchall()
        # Rows as arrays, not objects: the same numbers in half the bytes.
        _write_json(directory / "history" / f"{code}.json", {
            **meta,
            "currency": code,
            "columns": ["scraped_at", *FIELDS],
            "rows": [[r["scraped_at"], *(_clean(r[f]) for f in FIELDS)] for r in rows],
        })
        written += 1
    return written
