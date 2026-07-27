"""Command line: `python -m bca_forex <command>`.

    once     scrape now, store, print a summary
    run      stay running and scrape every 15 minutes, WIB (this is the service)
    latest   show the most recent stored scrape
    export   write the history to CSV
    chart    build a self-contained HTML chart of the history
    status   how much history there is, and whether anything has been failing
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import webbrowser
from pathlib import Path

from . import __version__
from .chart import build as build_chart
from .fetcher import URL, FetchError, fetch
from .parser import FIELDS, ParseError, parse
from .schedule import next_slot, now_wib, seconds_until_next
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


def scrape_once(store: Store, html_file: str | None = None) -> int:
    """Fetch (or read a saved page), parse, store. Returns currencies stored."""
    try:
        html = Path(html_file).read_text(encoding="utf-8") if html_file else fetch()
        pull = parse(html)
    except (FetchError, ParseError) as e:
        store.log_run(False, 0, str(e))
        _log(f"FAILED: {e}")
        return 0
    written = store.save(pull.rates.values(), utc_now_iso(), pull.page_updated)
    store.log_run(True, len(pull.rates))
    _log(
        f"stored {written} row(s) for {len(pull.rates)} currencies"
        + (f" | page says: {pull.page_updated}" if pull.page_updated else "")
    )
    return len(pull.rates)


def cmd_once(args: argparse.Namespace) -> int:
    with Store(args.db) as store:
        return 0 if scrape_once(store, args.html) else 1


def _refresh_chart(store: Store, args: argparse.Namespace) -> None:
    """Rebuild the chart after a scrape, if `run` was asked to keep one current.

    Failing here must not take the service down: a stale chart is a nuisance,
    a stopped scraper is a hole in the history that cannot be filled in later.
    """
    if not args.chart:
        return
    try:
        build_chart(
            store.conn, Path(args.chart), args.limit, None,
            Path(args.logo) if args.logo else None,
        )
    except Exception as e:  # noqa: BLE001
        _log(f"chart refresh failed (scraping continues): {e}")


def cmd_run(args: argparse.Namespace) -> int:
    _log(f"BCA Forex Scraper {__version__} — every 15 min on the quarter hour WIB, db {args.db}")
    if args.chart:
        _log(f"chart refreshed after each scrape -> {args.chart}")
    with Store(args.db) as store:
        if not args.no_initial:
            scrape_once(store)
            _refresh_chart(store, args)
        while True:
            wait = seconds_until_next()
            _log(f"next scrape {next_slot():%H:%M} WIB (in {wait / 60:.1f} min)")
            try:
                time.sleep(wait)
            except KeyboardInterrupt:
                _log("stopped")
                return 0
            # never let one bad scrape kill the service — the whole point of
            # this process is that it is still running tomorrow
            try:
                scrape_once(store)
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
        print(f"\nas of {rows[0]['scraped_at']} (UTC) | page: {rows[0]['page_updated'] or '-'}")
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
                Path(args.logo) if args.logo else None,
            )
        except (ValueError, OSError) as e:  # a bad --logo is a typo, not a crash
            _log(f"FAILED: {e}")
            return 1
    _log(f"wrote {out} — {currencies} currencies, {points} points")
    if args.open:
        webbrowser.open(out.resolve().as_uri())
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

    r = sub.add_parser("run", help="run forever, scraping every 15 minutes WIB")
    r.add_argument("--no-initial", action="store_true", help="wait for the first slot instead of scraping at start")
    r.add_argument("--chart", help="rebuild this HTML chart after every scrape")
    r.add_argument("--logo", help="image to embed in the chart header")
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
    c.add_argument("--open", action="store_true", help="open it in the browser when done")
    c.set_defaults(func=cmd_chart)

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
