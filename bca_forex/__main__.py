"""Command line: `python -m bca_forex <command>`.

    once     scrape now, store, print a summary
    run      stay running and scrape on the trading-hours schedule (the service)
    latest   show the most recent stored scrape
    export   write the history to CSV
    chart    build a self-contained HTML chart of the history
    status   how much history there is, and whether anything has been failing
    timing   when BCA switches its e-Rate tight/wide, and moves TT/Bank Notes
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import statistics
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path

from . import __version__
from .api import _spread_pct, is_tight, tight_threshold, write_api
from .chart import build as build_chart
from .fetcher import URL, FetchError, fetch
from .parser import FIELDS, ParseError, parse
from .schedule import WIB, is_heartbeat, next_slot, now_wib, seconds_until_next
from .store import Store, utc_now_iso

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "bca-forex.sqlite3"


# A Windows console often runs a legacy code page, and printing a character it
# cannot encode raises UnicodeEncodeError — which would kill an unattended
# service over a log line. Force UTF-8 and never fail on output; log text is
# kept to ASCII anyway so it stays readable whatever the console is set to.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):  # already redirected, or not a TTY
        pass


def _log(msg: str) -> None:
    """Timestamped line on stdout — Task Scheduler / journald capture this."""
    print(f"{now_wib():%Y-%m-%d %H:%M:%S} WIB  {msg}", flush=True)


def scrape_once(
    store: Store, html_file: str | None = None, heartbeat: bool = True,
    last: dict[str, tuple] | None = None,
) -> tuple[int, int]:
    """Fetch (or read a saved page), parse, store. Returns (currencies read, rows stored).

    A heartbeat stores every currency. Otherwise only the currencies whose
    rates differ from `last` (each one's newest stored values, kept up to date
    here) are written — most minutes in trading hours change nothing, and a
    row that repeats the one before it says nothing the chart does not already
    show.
    """
    try:
        html = Path(html_file).read_text(encoding="utf-8") if html_file else fetch()
        pull = parse(html)
    except (FetchError, ParseError) as e:
        store.log_run(False, 0, str(e))
        _log(f"FAILED: {e}")
        return 0, 0
    if last is None:
        last = store.last_values()
    rows = [
        r for r in pull.rates.values()
        if heartbeat or last.get(r.code) != tuple(getattr(r, f) for f in FIELDS)
    ]
    written = store.save(rows, utc_now_iso(), pull.page_updated) if rows else 0
    for r in rows:
        last[r.code] = tuple(getattr(r, f) for f in FIELDS)
    store.log_run(True, len(pull.rates))
    if heartbeat:
        _log(f"stored {written} row(s) for {len(pull.rates)} currencies (heartbeat)"
             + (f" | page says: {pull.page_updated}" if pull.page_updated else ""))
    elif written:
        _log(f"changed: {' '.join(r.code for r in rows)}")
    return len(pull.rates), written


def cmd_once(args: argparse.Namespace) -> int:
    with Store(args.db) as store:
        return 0 if scrape_once(store, args.html)[0] else 1


def _refresh_chart(store: Store, args: argparse.Namespace) -> None:
    """Rebuild the chart after a scrape, if `run` was asked to keep one current.

    Failing here must not take the service down: a stale chart is a nuisance,
    a stopped scraper is a hole in the history that cannot be filled in later.
    """
    if args.api:
        try:
            write_api(store.conn, Path(args.api))
        except Exception as e:  # noqa: BLE001
            _log(f"api refresh failed (scraping continues): {e}")
    if not args.chart:
        return
    try:
        build_chart(
            store.conn, Path(args.chart), args.limit, None,
            Path(args.logo) if args.logo else None, args.pwa,
        )
    except Exception as e:  # noqa: BLE001
        _log(f"chart refresh failed (scraping continues): {e}")


def cmd_run(args: argparse.Namespace) -> int:
    _log(f"BCA Forex Scraper {__version__} — every minute 08:30-15:30 weekdays (stored on change, "
         f"heartbeat hourly), hourly weeknights, 3 h weekends WIB, db {args.db}")
    if args.chart:
        _log(f"chart refreshed after each scrape -> {args.chart}")
    if args.api:
        _log(f"JSON API refreshed after each scrape -> {args.api}")
    with Store(args.db) as store:
        last = store.last_values()
        if not args.no_initial:
            scrape_once(store, last=last)
            _refresh_chart(store, args)
        while True:
            slot = next_slot()
            wait = seconds_until_next()
            # Every minute in trading hours would bury the log; say it only
            # before the long waits.
            if wait > 120:
                _log(f"next scrape {slot:%H:%M} WIB (in {wait / 60:.1f} min)")
            try:
                time.sleep(wait)
            except KeyboardInterrupt:
                _log("stopped")
                return 0
            # never let one bad scrape kill the service — the whole point of
            # this process is that it is still running tomorrow
            try:
                # Rebuild the chart and API only when a row went in: the page
                # is ~3 MB, and a minute with no change has nothing to add.
                if scrape_once(store, heartbeat=is_heartbeat(slot), last=last)[1]:
                    _refresh_chart(store, args)
            except Exception as e:  # noqa: BLE001
                store.log_run(False, 0, f"unexpected: {e}")
                _log(f"UNEXPECTED ERROR (continuing): {e}")


def cmd_latest(args: argparse.Namespace) -> int:
    with Store(args.db) as store:
        rows = store.latest()
        if not rows:
            _log("no data yet — run `once` first")
            return 1
        if args.json:
            print(json.dumps([dict(r) for r in rows], indent=2))
            return 0
        print(f"{'CUR':<5}" + "".join(f"{f:>17}" for f in FIELDS))
        for r in rows:
            vals = "".join(
                f"{(f'{r[f]:,.2f}' if r[f] is not None else '-'):>17}" for f in FIELDS
            )
            print(f"{r['currency']:<5}{vals}")
        newest = max(rows, key=lambda r: r["scraped_at"])
        print(f"\nlast change {newest['scraped_at']} (UTC) | checked {store.last_ok_run() or '-'} (UTC)")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    with Store(args.db) as store:
        sql = "SELECT * FROM rates"
        params: tuple[str, ...] = ()
        if args.currency:
            sql += " WHERE currency = ?"
            params = (args.currency.upper(),)
        sql += " ORDER BY scraped_at, currency"
        rows = list(store.conn.execute(sql, params))
    if not rows:
        _log("nothing to export")
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(rows[0].keys())
        w.writerows([tuple(r) for r in rows])
    _log(f"wrote {len(rows)} rows to {out}")
    return 0


def cmd_chart(args: argparse.Namespace) -> int:
    with Store(args.db) as store:
        if not store.stats()["n"]:
            _log("no data yet — run `once` first")
            return 1
        try:
            out, currencies, points = build_chart(
                store.conn, Path(args.out), args.limit, args.currency,
                Path(args.logo) if args.logo else None, args.pwa,
            )
        except (ValueError, OSError) as e:  # a bad --logo is a typo, not a crash
            _log(f"FAILED: {e}")
            return 1
    _log(f"wrote {out} — {currencies} currencies, {points} points")
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


def cmd_timing(args: argparse.Namespace) -> int:
    """When BCA actually switches its e-Rate between the wide and the tight
    quote each weekday, and when it moves its TT and Bank Notes rates.

    Each time is the first read that saw the new state. How far it can be off
    is the gap back to the read before it (the `runs` table logs every read,
    stored or not): about a minute in trading hours since 0.6.0, up to fifteen
    on older days. Only days read every minute or two go into the summary.
    """
    code = args.currency.upper()
    with Store(args.db) as store:
        conn = store.conn
        rows = conn.execute(
            f"SELECT scraped_at, {', '.join(FIELDS)} FROM rates WHERE currency = ? ORDER BY scraped_at",
            (code,),
        ).fetchall()
        runs = [r[0] for r in conn.execute("SELECT started_at FROM runs WHERE ok = 1 ORDER BY started_at")]
    if not rows:
        _log(f"no rows for {code}")
        return 1
    thr = tight_threshold([(r["scraped_at"], _spread_pct(r["e_rate_buy"], r["e_rate_sell"])) for r in rows])
    if thr is None:
        _log(f"not enough history for {code} to tell tight from wide yet")
        return 1

    def slack(ts: str) -> float:
        """Minutes back to the read before this one."""
        i = bisect.bisect_left(runs, ts)
        if i == 0:
            return float("inf")
        return (datetime.fromisoformat(ts) - datetime.fromisoformat(runs[i - 1])).total_seconds() / 60

    days: dict[str, list] = {}
    for r in rows:
        t = datetime.fromisoformat(r["scraped_at"]).astimezone(WIB)
        if t.weekday() < 5:
            days.setdefault(t.date().isoformat(), []).append((t, r))
    days = dict(list(days.items())[-args.days:])

    print(f"{code}, times WIB: first read that saw it (± minutes back to the read before)\n")
    print(f"{'day':<12}{'tight from':<16}{'wide from':<16}TT / Bank Notes moved")
    found: dict[str, list[int]] = {"tight": [], "wide": []}
    for day, items in days.items():
        cells = {}
        prev = None
        for t, r in items:
            tight = is_tight(thr, r["e_rate_buy"], r["e_rate_sell"])
            if prev is not None and tight is not None and tight != prev:
                key = "tight" if tight else "wide"
                if key not in cells:     # the first switch each way that day
                    s = slack(r["scraped_at"])
                    cells[key] = f"{t:%H:%M} (±{s:.0f})" if s < 1e9 else f"{t:%H:%M}"
                    if s <= 2:
                        found[key].append(t.hour * 60 + t.minute)
            if tight is not None:
                prev = tight
        moved, before = [], None
        for t, r in items:
            counter = (r["tt_buy"], r["tt_sell"], r["bank_notes_buy"], r["bank_notes_sell"])
            if before is not None and counter != before:
                moved.append(f"{t:%H:%M}")
            before = counter
        print(f"{day:<12}{cells.get('tight', '-'):<16}{cells.get('wide', '-'):<16}{', '.join(moved) or '-'}")

    print()
    for key, label in (("tight", "Tight from"), ("wide", "Wide from")):
        mins = found[key]
        if not mins:
            print(f"{label}: no days read every minute yet")
            continue
        hm = lambda m: f"{int(m) // 60:02d}:{int(m) % 60:02d}"
        print(f"{label}: earliest {hm(min(mins))}, median {hm(statistics.median(mins))}, "
              f"latest {hm(max(mins))}  ({len(mins)} days at 1-2 min resolution)")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    with Store(args.db) as store:
        s = store.stats()
    _log(f"db {args.db}")
    print(
        f"  rows        {s['n']}\n"
        f"  scrapes     {s['scrapes']}\n"
        f"  currencies  {s['currencies']}\n"
        f"  first       {s['first'] or '-'}\n"
        f"  last        {s['last'] or '-'}\n"
        f"  failed runs {s['failed_runs']}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bca_forex",
        description=f"Scrape BCA forex rates from {URL} into SQLite. No dependencies.",
    )
    p.add_argument("--db", default=str(DEFAULT_DB), help="SQLite file (default: ./data/bca-forex.sqlite3)")
    sub = p.add_subparsers(dest="cmd", required=True)

    o = sub.add_parser("once", help="scrape now and exit")
    o.add_argument("--html", help="parse this saved HTML file instead of fetching")
    o.set_defaults(func=cmd_once)

    r = sub.add_parser("run", help="run forever, scraping on the trading-hours schedule")
    r.add_argument("--no-initial", action="store_true", help="wait for the first slot instead of scraping at start")
    r.add_argument("--chart", help="rebuild this HTML chart after every scrape")
    r.add_argument("--api", help="rewrite the JSON API (latest.json, history/*.json) into this folder after every scrape")
    r.add_argument("--logo", help="image to embed in the chart header")
    r.add_argument("--pwa", action="store_true", help="also write manifest, service worker and icons")
    r.add_argument("--limit", type=int, default=20000, help="chart points per currency (default: 20000)")
    r.set_defaults(func=cmd_run)

    l = sub.add_parser("latest", help="show the newest stored scrape")
    l.add_argument("--json", action="store_true")
    l.set_defaults(func=cmd_latest)

    e = sub.add_parser("export", help="write history to CSV")
    e.add_argument("--out", default="export.csv")
    e.add_argument("--currency", help="only this code, e.g. USD")
    e.set_defaults(func=cmd_export)

    c = sub.add_parser("chart", help="build a self-contained HTML chart")
    c.add_argument("--out", default="chart.html", help="output file (default: chart.html)")
    c.add_argument("--currency", help="only this code, e.g. USD (default: all, switchable in the page)")
    c.add_argument("--limit", type=int, default=20000, help="most recent N scrapes per currency (default: 20000)")
    c.add_argument("--logo", help="image file to embed in the header (png/svg/jpg/gif/webp)")
    c.add_argument("--pwa", action="store_true", help="also write manifest, service worker and icons (needs HTTPS to install)")
    c.add_argument("--open", action="store_true", help="open it in the browser when done")
    c.set_defaults(func=cmd_chart)

    tm = sub.add_parser("timing", help="when BCA switches its e-Rate tight/wide each weekday")
    tm.add_argument("--currency", default="USD")
    tm.add_argument("--days", type=int, default=30, help="most recent N weekdays (default: 30)")
    tm.set_defaults(func=cmd_timing)

    s = sub.add_parser("status", help="history size and failure count")
    s.set_defaults(func=cmd_status)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        _log("stopped")
        return 0


if __name__ == "__main__":
    sys.exit(main())
