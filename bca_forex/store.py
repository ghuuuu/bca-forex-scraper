"""SQLite storage. One row per (scrape, currency).

SQLite because this has to be movable: the whole history is one file you can
copy to a USB stick, and there is no server to install, secure or keep running
on a mini PC. `sqlite3` ships with Python.

Timestamps are stored as UTC ISO-8601 with an offset. Storing local time without
one is how a history quietly becomes unreadable after a clock change.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from .parser import FIELDS, CurrencyRates

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS rates (
    scraped_at   TEXT NOT NULL,          -- UTC ISO-8601
    currency     TEXT NOT NULL,
    {chr(10).join(f'    {f}   REAL,' for f in FIELDS)}
    page_updated TEXT,                   -- what BCA printed on the page
    PRIMARY KEY (scraped_at, currency)
);
CREATE INDEX IF NOT EXISTS idx_rates_currency_time ON rates (currency, scraped_at);

CREATE TABLE IF NOT EXISTS runs (
    started_at TEXT NOT NULL,
    ok         INTEGER NOT NULL,
    currencies INTEGER NOT NULL DEFAULT 0,
    message    TEXT
);
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        # survive an abrupt power cut on a mini PC without corrupting the file
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def save(
        self,
        rates: Iterable[CurrencyRates],
        scraped_at: str | None = None,
        page_updated: str | None = None,
    ) -> int:
        """Insert one scrape. Idempotent: re-running the same instant is a no-op."""
        ts = scraped_at or utc_now_iso()
        cols = ", ".join(FIELDS)
        marks = ", ".join("?" for _ in FIELDS)
        rows = [
            (ts, r.code, *[getattr(r, f) for f in FIELDS], page_updated) for r in rates
        ]
        cur = self.conn.executemany(
            f"INSERT OR IGNORE INTO rates (scraped_at, currency, {cols}, page_updated) "
            f"VALUES (?, ?, {marks}, ?)",
            rows,
        )
        self.conn.commit()
        return cur.rowcount

    def log_run(self, ok: bool, currencies: int = 0, message: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO runs (started_at, ok, currencies, message) VALUES (?, ?, ?, ?)",
            (utc_now_iso(), 1 if ok else 0, currencies, message),
        )
        self.conn.commit()

    # ---- reads ----------------------------------------------------------

    def latest(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM rates WHERE scraped_at = (SELECT MAX(scraped_at) FROM rates) "
                "ORDER BY currency"
            )
        )

    def history(self, currency: str, limit: int = 1000) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM rates WHERE currency = ? ORDER BY scraped_at DESC LIMIT ?",
                (currency.upper(), limit),
            )
        )

    def stats(self) -> dict[str, object]:
        row = self.conn.execute(
            "SELECT COUNT(*) n, COUNT(DISTINCT scraped_at) scrapes, "
            "COUNT(DISTINCT currency) currencies, MIN(scraped_at) first, MAX(scraped_at) last "
            "FROM rates"
        ).fetchone()
        fails = self.conn.execute("SELECT COUNT(*) FROM runs WHERE ok = 0").fetchone()[0]
        return {**dict(row), "failed_runs": fails}
