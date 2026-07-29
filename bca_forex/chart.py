"""Build a self-contained HTML chart from the stored history.

One file, no dependencies, no server: the SVG is drawn by hand in vanilla JS and
the history is embedded as JSON, so the output opens from a USB stick or an
email attachment with no network at all. That is the same constraint the rest of
this project works under, and it rules out every charting library.

The three rate types are drawn as BANDS rather than six independent lines: buy
and sell for one type belong together — the gap between them is the spread, the
thing a reader actually wants to see — so one control toggles the pair and the
fill between them carries the meaning.

Zeros are dropped on the way in. BCA prints 0,00 in Bank Notes for currencies it
does not deal in physical cash (AED, CNH, DKK, MYR, SEK at the time of writing),
and that is a "not offered" marker, not a rate. Plotted literally it drags the
line to the axis and flattens every real movement on the chart into a hairline.
"""

from __future__ import annotations

import base64
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import __version__

#: Chart series -> the (buy, sell) columns behind it, in display order.
SERIES: tuple[tuple[str, str, str, str], ...] = (
    ("e_rate", "e-Rate", "e_rate_buy", "e_rate_sell"),
    ("tt", "TT Counter", "tt_buy", "tt_sell"),
    ("bank_notes", "Bank Notes", "bank_notes_buy", "bank_notes_sell"),
)

#: Raster formats we will inline, and what to call them in the data URI.
_IMAGE_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
}

#: BCA's own logo, the file their kurs page links in its header. Bundled inside
#: the package rather than sitting beside it so it travels with the module —
#: the Docker image copies only `bca_forex/`, and this has to be in there.
BUNDLED_LOGO = Path(__file__).resolve().parent / "assets" / "logo-bca.svg"

# Fallback if the asset is missing. A plain monogram, not a traced imitation of
# BCA's mark: something obviously not the real logo beats a bad copy of it.
FALLBACK_LOGO = """<svg viewBox="0 0 96 96" role="img" aria-label="BCA">
  <rect width="96" height="96" rx="20" fill="#0060af"/>
  <text x="48" y="62" text-anchor="middle" fill="#fff"
        style="font:700 34px system-ui,-apple-system,'Segoe UI',sans-serif;letter-spacing:1px">BCA</text>
</svg>"""


def _inline_svg(raw: str) -> str:
    """Strip what cannot appear (or should not show) inside an HTML body.

    An <?xml?> declaration is invalid mid-document, and the asset's Sketch
    <title> would otherwise surface as a tooltip reading 'atoms/icon/general/
    logo' when you hover the header.
    """
    out = re.sub(r"<\?xml[^>]*\?>|<!DOCTYPE[^>]*>", "", raw)
    out = re.sub(r"<!--.*?-->", "", out, flags=re.S)
    out = re.sub(r"<(title|desc)\b[^>]*>.*?</\1>", "", out, flags=re.S | re.I)
    out = re.sub(r"^\s+|\s+$", "", re.sub(r">\s+<", "><", out))
    return out.replace("<svg", '<svg role="img" aria-label="BCA"', 1)


def logo_markup(path: Path | None = None) -> str:
    """Header logo markup: the supplied file, else the bundled BCA asset.

    Always embedded, never linked — the point of the output is that it survives
    being copied off the machine, and <img src="logo.svg"> would show a broken
    icon the moment the html travels alone.

    SVG is inlined as markup rather than base64'd into a data URI: the asset is
    70 KB of path geometry, and base64 would add a third again for nothing.
    Raster formats have no such option and become data URIs.

    Both are wrapped on a white plate. BCA's logo is a single blue (#0F60AD)
    wordmark drawn for white paper; dropped straight onto this dark header it
    reads as a smudge.
    """
    if path is None:
        if not BUNDLED_LOGO.exists():
            return FALLBACK_LOGO
        path = BUNDLED_LOGO

    suffix = path.suffix.lower()
    if suffix == ".svg":
        inner = _inline_svg(path.read_text(encoding="utf-8"))
    elif suffix in _IMAGE_MIME:
        uri = f"data:{_IMAGE_MIME[suffix]};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
        inner = f'<img src="{uri}" alt="BCA">'
    else:
        raise ValueError(
            f"unsupported logo format '{suffix}' — use one of: .svg, "
            + ", ".join(sorted(_IMAGE_MIME))
        )
    return f'<span class="plate">{inner}</span>'


def _epoch(iso: str) -> int:
    """'2026-07-27T02:43:18+00:00' -> unix seconds."""
    return int(datetime.fromisoformat(iso).timestamp())


def _clean(v: float | None) -> float | None:
    """Zero means 'not offered' on BCA's page, never a real rate."""
    return None if v is None or v == 0 else v


def collect(
    conn: sqlite3.Connection, limit: int = 20000, currency: str | None = None
) -> dict:
    """Pull the history into the compact shape the page expects.

    Parallel arrays per series rather than a list of row objects: the same
    numbers in a fraction of the bytes, which is what keeps a year of 15-minute
    scrapes inside a file you can still email.
    """
    where, params = "", []
    if currency:
        where = "WHERE currency = ?"
        params.append(currency.upper())

    rows = conn.execute(
        f"SELECT * FROM rates {where} ORDER BY currency, scraped_at DESC", params
    ).fetchall()

    per_currency: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        bucket = per_currency.setdefault(r["currency"], [])
        if len(bucket) < limit:
            bucket.append(r)

    data: dict[str, dict] = {}
    for code, bucket in per_currency.items():
        bucket.reverse()  # back to oldest-first for plotting
        entry: dict[str, object] = {"t": [_epoch(r["scraped_at"]) for r in bucket]}
        for key, _label, buy_col, sell_col in SERIES:
            buy = [_clean(r[buy_col]) for r in bucket]
            sell = [_clean(r[sell_col]) for r in bucket]
            # a series with nothing in it is omitted, so the page can grey out
            # the control instead of offering an empty chart
            if any(v is not None for v in buy) or any(v is not None for v in sell):
                entry[key] = [buy, sell]
        data[code] = entry

    page_updated = conn.execute(
        "SELECT page_updated FROM rates WHERE page_updated IS NOT NULL "
        "ORDER BY scraped_at DESC LIMIT 1"
    ).fetchone()
    failed = conn.execute("SELECT COUNT(*) FROM runs WHERE ok = 0").fetchone()[0]

    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pageUpdated": page_updated[0] if page_updated else None,
        "failedRuns": failed,
        "series": [{"key": k, "label": lb} for k, lb, _b, _s in SERIES],
        "currencies": sorted(data),
        "data": data,
    }


#: Icon files copied out beside the page when --pwa is used.
PWA_ICONS = ("icon-192.png", "icon-512.png", "icon-512-maskable.png", "apple-touch-icon.png")

# Only emitted with --pwa. Referencing a manifest or a service worker that is
# not there would turn a file you can open from a USB stick into one that logs
# 404s on every load.
PWA_HEAD = """
<link rel="manifest" href="manifest.webmanifest">
<meta name="theme-color" content="#0b0e14">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="BCA Forex">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<link rel="icon" type="image/png" sizes="192x192" href="icon-192.png">
<script>
  if ("serviceWorker" in navigator) {
    addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
  }
</script>"""

MANIFEST = {
    "name": "BCA Forex Rates",
    "short_name": "BCA Forex",
    "description": "BCA published exchange-rate history, updated every 15 minutes.",
    "start_url": ".",
    "scope": ".",
    "display": "standalone",
    "orientation": "any",
    "background_color": "#0b0e14",
    "theme_color": "#0b0e14",
    "icons": [
        {"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "icon-512-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ],
}

# Network-first for the page, cache-first for the icons.
#
# The page IS the data — a new one is written after every scrape — so a stale
# cached copy served in preference to the network would defeat the point. Going
# to the network first and falling back to cache gives the behaviour actually
# wanted: current rates on the LAN, last-known rates when the NAS is
# unreachable, which is the whole reason for doing this.
#
# Redirected or non-OK responses are never cached. Behind Cloudflare Access an
# expired session answers with a login page, and caching that would pin the app
# to a login screen it can no longer get past.
SERVICE_WORKER = """\
const CACHE = "bca-forex-v1";
const SHELL = ["./", "./manifest.webmanifest", "./icon-192.png", "./icon-512.png",
               "./icon-512-maskable.png", "./apple-touch-icon.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys()
      .then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function cacheable(res) {
  return res && res.ok && !res.redirected && res.type !== "opaqueredirect";
}

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  // The page's own poll for fresh data carries a cache-busting query. Leave it
  // entirely alone: it must reach the network, and caching a copy per timestamp
  // would grow the cache without bound.
  if (new URL(req.url).search) return;

  const isPage = req.mode === "navigate" || new URL(req.url).pathname.endsWith("/");

  if (isPage) {
    e.respondWith(
      fetch(req)
        .then(res => {
          if (cacheable(res)) {
            const copy = res.clone();
            caches.open(CACHE).then(c => c.put("./", copy));
          }
          return res;
        })
        .catch(() => caches.match("./").then(hit => hit || Response.error()))
    );
    return;
  }

  e.respondWith(
    caches.match(req).then(hit => hit || fetch(req).then(res => {
      if (cacheable(res)) {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(req, copy));
      }
      return res;
    }))
  );
});
"""


def write_pwa(directory: Path) -> list[Path]:
    """Drop the manifest, service worker and icons beside the page."""
    directory.mkdir(parents=True, exist_ok=True)
    written = []

    manifest = directory / "manifest.webmanifest"
    manifest.write_text(json.dumps(MANIFEST, indent=2), encoding="utf-8")
    written.append(manifest)

    sw = directory / "sw.js"
    sw.write_text(SERVICE_WORKER, encoding="utf-8")
    written.append(sw)

    for name in PWA_ICONS:
        src = BUNDLED_LOGO.parent / name
        if not src.exists():
            raise FileNotFoundError(
                f"missing icon {src} — regenerate with `python tools/make_icons.py`"
            )
        dest = directory / name
        dest.write_bytes(src.read_bytes())
        written.append(dest)

    return written


def build(
    conn: sqlite3.Connection,
    out: Path,
    limit: int = 20000,
    currency: str | None = None,
    logo: Path | None = None,
    pwa: bool = False,
) -> tuple[Path, int, int]:
    """Write the chart. Returns (path, currencies, total points)."""
    payload = collect(conn, limit, currency)
    html = (
        TEMPLATE
        .replace("/*__DATA__*/null", json.dumps(payload, separators=(",", ":")))
        .replace("<!--__LOGO__-->", logo_markup(logo))
        .replace("<!--__PWA__-->", PWA_HEAD if pwa else "")
        .replace("<!--__VERSION__-->", __version__)
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    if pwa:
        write_pwa(out.parent)
    points = sum(len(v["t"]) for v in payload["data"].values())
    return out, len(payload["currencies"]), points


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>BCA Forex — rate history</title>
<!--__PWA__-->
<style>
  :root {
    --bg: #0b0e14; --panel: #121722; --panel-2: #171d2a; --line: #232b3b;
    --text: #e6edf7; --muted: #8b97ab;
    --up: #22c55e; --down: #ef4444;
    --c-e_rate: #38bdf8; --c-tt: #fbbf24; --c-bank_notes: #c084fc;
  }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  html, body { margin: 0; height: 100%; }
  body {
    background: var(--bg); color: var(--text);
    font: 13px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-variant-numeric: tabular-nums; -webkit-font-smoothing: antialiased;
  }
  .wrap { max-width: 1420px; margin: 0 auto; padding: 14px; display: flex; flex-direction: column; gap: 12px; }

  header.brand {
    display: flex; align-items: center; gap: 13px;
    padding-bottom: 12px; border-bottom: 1px solid var(--line);
  }
  header.brand .logo { flex: none; display: flex; align-items: center; }
  /* The bank's mark is one flat blue for white paper, so it gets a white plate
     to sit on rather than being recoloured — a repainted logo is the wrong
     logo. The fallback monogram carries its own background and skips this. */
  header.brand .logo .plate {
    display: flex; align-items: center; background: #fff;
    padding: 7px 11px; border-radius: 9px;
  }
  header.brand .logo svg, header.brand .logo img {
    display: block; height: 34px; width: auto; max-width: 118px;
  }
  header.brand .logo > svg { height: 42px; border-radius: 9px; }
  header.brand .who { flex: 1; min-width: 0; }
  header.brand .t {
    font-size: 16px; font-weight: 700; letter-spacing: -.2px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  header.brand .t .ver {
    font-size: 10.5px; font-weight: 600; color: var(--muted); letter-spacing: .02em;
    border: 1px solid var(--line); border-radius: 999px; padding: 2px 7px;
    margin-left: 7px; vertical-align: middle;
  }
  header.brand .s { color: var(--muted); font-size: 11.5px; }
  header.brand a.src {
    flex: none; color: var(--muted); font-size: 11.5px; text-decoration: none;
    border: 1px solid var(--line); border-radius: 999px; padding: 6px 12px; white-space: nowrap;
  }
  header.brand a.src:hover { color: var(--text); border-color: #38455e; }
  header.brand .market {
    flex: none; display: inline-flex; align-items: center; gap: 7px;
    font-size: 11.5px; font-weight: 600; color: var(--muted);
    border: 1px solid var(--line); border-radius: 999px; padding: 6px 12px; white-space: nowrap;
  }
  header.brand .market::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
  header.brand .market.open { color: var(--up); }
  header.brand .market.closed { color: var(--muted); }
  @media (max-width: 620px) { header.brand .market { display: none; } }

  select, button { font-family: inherit; }
  select.sel {
    background: var(--panel-2); color: var(--text); border: 1px solid var(--line);
    border-radius: 8px; padding: 8px 11px; font: 600 13px/1 inherit; cursor: pointer;
  }
  select.sel:focus { outline: 2px solid #2f6fed; outline-offset: 1px; }
  select.cur { font-size: 15px; letter-spacing: .04em; padding: 9px 12px; min-width: 104px; }

  /* Global controls: everything here applies to every panel at once, which is
     the whole point of the second panel — two currencies only line up if
     they are on the same metric, the same series and the same range. */
  .globals { display: flex; flex-wrap: wrap; align-items: center; gap: 9px 14px; }
  .field { display: flex; align-items: center; gap: 8px; }
  .field > span { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .05em; font-weight: 600; }
  button.pill {
    background: var(--panel); border: 1px solid var(--line); color: var(--muted);
    border-radius: 999px; padding: 8px 15px; font: 600 12.5px/1 inherit; cursor: pointer;
  }
  button.pill:hover { border-color: #38455e; color: var(--text); }
  button.pill.on { background: var(--panel-2); border-color: #38455e; color: var(--text); }
  button.pill .dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: #3d4a63; margin-right: 7px; vertical-align: middle; }
  button.pill.on .dot { background: var(--up); }

  .controls { display: flex; flex-wrap: wrap; gap: 10px; justify-content: space-between; align-items: center; }
  .toggles { display: flex; flex-wrap: wrap; gap: 8px; }
  .tg {
    display: inline-flex; align-items: center; gap: 8px; cursor: pointer; user-select: none;
    background: var(--panel); border: 1px solid var(--line); border-radius: 999px;
    padding: 7px 14px 7px 11px; font-weight: 600; color: var(--muted); transition: .12s;
  }
  .tg:hover { border-color: #33405a; }
  .tg input { position: absolute; opacity: 0; pointer-events: none; }
  .tg .sw { width: 22px; height: 3px; border-radius: 2px; background: currentColor; opacity: .3; }
  .tg.on { color: var(--text); background: var(--panel-2); border-color: #38455e; }
  .tg.on .sw { opacity: 1; }
  .tg.off-data { opacity: .38; cursor: not-allowed; }
  .tg[data-k="e_rate"] .sw { color: var(--c-e_rate); }
  .tg[data-k="tt"] .sw { color: var(--c-tt); }
  .tg[data-k="bank_notes"] .sw { color: var(--c-bank_notes); }

  .ranges { display: flex; gap: 4px; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 3px; }
  .ranges button {
    background: none; border: 0; color: var(--muted); font: 600 12px/1 inherit;
    padding: 7px 11px; border-radius: 6px; cursor: pointer;
  }
  .ranges button:hover:not(:disabled) { color: var(--text); }
  .ranges button.on { background: #2b3547; color: #fff; }
  .ranges button:disabled { opacity: .3; cursor: not-allowed; }

  /* One column by default. Two only when there is genuinely room for two —
     side-by-side charts narrower than this are worse than stacked ones. */
  .panels { display: grid; grid-template-columns: 1fr; gap: 14px; }
  /* Side by side only once a half-width panel can hold the whole stats table.
     Below this the columns overflowed and put a scrollbar under every chart —
     and two cramped charts read worse than two full-width stacked ones, so the
     threshold is set by what actually fits rather than by a round number. */
  @media (min-width: 1200px) { .panels.split { grid-template-columns: 1fr 1fr; } }

  .panel { display: flex; flex-direction: column; gap: 11px; min-width: 0; }
  .panel .top { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 16px; }
  .quote { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
  .price { font-size: 29px; font-weight: 650; letter-spacing: -.5px; line-height: 1; }
  .chg { font-size: 13.5px; font-weight: 600; }
  .up { color: var(--up); } .down { color: var(--down); } .flat { color: var(--muted); }
  .sub { color: var(--muted); font-size: 11.5px; }
  .sub b { color: #b9c4d6; font-weight: 600; }

  /* overflow:hidden is load-bearing: the tooltip is absolutely positioned, and
     without this a stale offset could extend the page's scroll width and give
     the whole document a horizontal scrollbar. */
  .chartbox { position: relative; overflow: hidden; background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 4px; }
  .chart {
    display: block; width: 100%; height: 60vh; min-height: 330px; max-height: 600px;
    /* Vertical drags scroll the page; sideways ones are the chart's. */
    touch-action: pan-y;
    /* A long press would otherwise start a text selection or pop the iOS
       callout, and either one cancels the gesture mid-scrub. */
    -webkit-user-select: none; user-select: none; -webkit-touch-callout: none;
  }
  .chart.scrubbing { touch-action: none; }
  .panels.split .chart { height: 46vh; min-height: 280px; max-height: 460px; }
  .chart text { fill: var(--muted); font-size: 11px; }
  .notice {
    position: absolute; inset: auto 0 0 0; margin: 0 auto 14px; width: max-content; max-width: 92%;
    background: rgba(30,38,54,.94); border: 1px solid var(--line); color: var(--muted);
    padding: 7px 13px; border-radius: 8px; font-size: 12px; text-align: center;
  }
  .tip {
    position: absolute; pointer-events: none; opacity: 0; visibility: hidden;
    transition: opacity .09s; max-width: calc(100% - 12px);
    background: rgba(13,17,26,.97); border: 1px solid #2c3648; border-radius: 9px;
    padding: 9px 11px; min-width: 190px; box-shadow: 0 10px 28px rgba(0,0,0,.55); z-index: 5;
  }
  .tip.show { opacity: 1; visibility: visible; }
  .tip .when { color: var(--muted); font-size: 11px; margin-bottom: 7px; }
  .tip .grp { margin-top: 7px; }
  .tip .grp:first-of-type { margin-top: 0; }
  .tip .nm { display: flex; align-items: center; gap: 6px; font-weight: 700; font-size: 11.5px; margin-bottom: 3px; }
  .tip .dot { width: 9px; height: 3px; border-radius: 2px; }
  .tip .kv { display: flex; justify-content: space-between; gap: 18px; font-size: 12px; }
  .tip .kv span:first-child { color: var(--muted); }
  .tip .kv.sp span:last-child { color: #b9c4d6; }
  .tip .kv.pick span { color: #fff; font-weight: 700; }

  .stats { width: 100%; border-collapse: collapse; background: var(--panel); border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
  .stats th, .stats td { padding: 9px 12px; text-align: right; white-space: nowrap; }
  .stats th { color: var(--muted); font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: .05em; border-bottom: 1px solid var(--line); }
  .stats th:first-child, .stats td:first-child { text-align: left; }
  .stats tbody tr + tr td { border-top: 1px solid #1b2231; }
  .stats .nm { display: flex; align-items: center; gap: 8px; font-weight: 650; }
  .stats .dot { width: 11px; height: 3px; border-radius: 2px; }
  /* Split view halves the width and the eight-column table stopped fitting,
     putting a scrollbar across the bottom of the left panel. Tightening the
     cells buys the width back rather than making the reader drag for it. */
  .panels.split .stats th, .panels.split .stats td { padding: 7px 6px; font-size: 11.5px; }
  .panels.split .stats th { letter-spacing: .02em; }

  /* Kept as a fallback for genuinely narrow screens, but slim and dark instead
     of the platform's default slab. */
  .scroller { overflow-x: auto; scrollbar-width: thin; scrollbar-color: #2b3547 transparent; }
  .scroller::-webkit-scrollbar { height: 6px; }
  .scroller::-webkit-scrollbar-thumb { background: #2b3547; border-radius: 3px; }
  .scroller::-webkit-scrollbar-track { background: transparent; }

  footer { color: var(--muted); font-size: 11.5px; display: flex; flex-wrap: wrap; gap: 6px 16px; }
  .warn { color: #fbbf24; }
  /* Cached-and-offline has to announce itself. A rate page that silently shows
     yesterday's numbers as though they were current is worse than one that
     fails outright. */
  #offline[hidden] { display: none; }
  #offline {
    display: inline-flex; align-items: center; gap: 7px; color: #fbbf24;
    background: rgba(251,191,36,.10); border: 1px solid rgba(251,191,36,.35);
    border-radius: 999px; padding: 5px 12px; font-weight: 600;
  }
  #offline::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: #fbbf24; }

  /* Phones. The controls break first: three pills and six range buttons cannot
     share one 360px line, so each row takes the full width instead. */
  @media (max-width: 620px) {
    .wrap { padding: 11px; gap: 11px; }
    header.brand { gap: 10px; padding-bottom: 10px; }
    header.brand .logo .plate { padding: 5px 8px; border-radius: 7px; }
    header.brand .logo svg, header.brand .logo img { height: 26px; max-width: 92px; }
    header.brand .logo > svg { height: 34px; }
    header.brand .t { font-size: 14.5px; }
    header.brand a.src { display: none; }

    .globals { gap: 8px; }
    .globals .field { flex: 1; min-width: 0; }
    .globals select.sel { flex: 1; min-width: 0; }
    button.pill { flex: none; }

    .panel .top { align-items: center; gap: 9px 13px; }
    .price { font-size: 23px; }
    .chg { font-size: 12.5px; }
    select.cur { padding: 8px 10px; font-size: 14px; min-width: 88px; }

    .controls { flex-direction: column; align-items: stretch; gap: 9px; }
    .toggles { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }
    .tg { justify-content: center; padding: 9px 6px; font-size: 11.5px; gap: 5px; }
    .tg .sw { width: 14px; }
    .ranges { justify-content: space-between; }
    .ranges button { flex: 1; padding: 9px 4px; }

    .chart { height: 44vh; min-height: 270px; }
    /* Stacked on a phone, so both charts have to fit a thumb-scroll apart. */
    .panels.split .chart { height: 36vh; min-height: 230px; }
    .stats th, .stats td { padding: 8px 9px; font-size: 12px; }
    .tip { min-width: 168px; padding: 8px 10px; }
  }

  @media (max-width: 380px) {
    .tg span:last-child { font-size: 11px; }
    .price { font-size: 20px; }
  }

  @media (max-height: 480px) and (orientation: landscape) {
    .chart { height: 62vh; min-height: 200px; }
    .panels.split .chart { height: 58vh; min-height: 180px; }
  }
</style>
</head>
<body>
<div class="wrap">

  <header class="brand">
    <span class="logo"><!--__LOGO__--></span>
    <div class="who">
      <div class="t">BCA Forex Rates <span class="ver">v<!--__VERSION__--></span></div>
      <div class="s">Kurs history &middot; Bank Central Asia</div>
    </div>
    <span class="market" id="market" hidden></span>
    <a class="src" href="https://www.bca.co.id/id/informasi/kurs" target="_blank" rel="noopener noreferrer">Source &#8599;</a>
  </header>

  <div class="globals">
    <label class="field">
      <span>Headline</span>
      <select class="sel" id="metric"></select>
    </label>
    <button class="pill" id="split"><span class="dot"></span>Two charts</button>
  </div>

  <div class="controls">
    <div class="toggles" id="toggles"></div>
    <div class="ranges" id="ranges"></div>
  </div>

  <div class="panels" id="panels"></div>

  <footer>
    <span id="offline" hidden>Offline — showing last synced data</span>
    <span id="cov"></span>
    <span id="stamp"></span>
    <span id="fails"></span>
    <span>Times are WIB (UTC+7). Buy = solid, sell = dashed, shaded band = spread.</span>
  </footer>
</div>

<script>
"use strict";
// `let`, not `const`: the page reloads its own payload on the scrape schedule
// and swaps this out in place. See pullFresh().
let DATA = /*__DATA__*/null;

const COLORS = { e_rate: "#38bdf8", tt: "#fbbf24", bank_notes: "#c084fc" };
const RANGES = [
  { k: "1D", s: 86400 }, { k: "1W", s: 604800 }, { k: "1M", s: 2592000 },
  { k: "3M", s: 7776000 }, { k: "1Y", s: 31536000 }, { k: "ALL", s: Infinity },
];
const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const SIDES = ["Buy", "Sell"];

const nf = new Intl.NumberFormat("id-ID", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmt = v => (v == null || !isFinite(v)) ? "—" : nf.format(v);

// WIB is a fixed +7 with no DST, so shifting the epoch and reading UTC parts is
// exact — and, unlike toLocaleString, it cannot be moved by the viewer's own
// timezone. A chart of Indonesian bank rates should read the same everywhere.
function wibParts(ts) {
  const d = new Date((ts + 7 * 3600) * 1000);
  return { y: d.getUTCFullYear(), mo: d.getUTCMonth(), d: d.getUTCDate(),
           h: d.getUTCHours(), mi: d.getUTCMinutes() };
}
// Trading window the chart greys out: Mon-Fri 08:00-16:00 WIB, so anything from
// 16:00 through 08:00 the next morning reads as closed. A display constant — the
// chart decides what to grey, independent of the scraper's own sampling window.
const OPEN_MIN = 8 * 60, CLOSE_MIN = 16 * 60;
function isClosed(ts) {
  const d = new Date((ts + 7 * 3600) * 1000);
  const day = d.getUTCDay();                 // 0 Sun .. 6 Sat
  if (day === 0 || day === 6) return true;
  const mins = d.getUTCHours() * 60 + d.getUTCMinutes();
  return mins < OPEN_MIN || mins >= CLOSE_MIN;
}

const p2 = n => String(n).padStart(2, "0");
const fmtTime = ts => { const p = wibParts(ts); return p2(p.h) + ":" + p2(p.mi); };
const fmtDay = ts => { const p = wibParts(ts); return p.d + " " + MONTHS[p.mo]; };
const fmtFull = ts => { const p = wibParts(ts);
  return p.d + " " + MONTHS[p.mo] + " " + p.y + ", " + p2(p.h) + ":" + p2(p.mi) + " WIB"; };

const el = id => document.getElementById(id);

// Metric, series and range are global on purpose: two currencies side by side
// only mean anything if they are being read the same way.
const state = {
  range: "ALL",
  on: Object.fromEntries(DATA.series.map(s => [s.key, true])),
  metric: { key: "e_rate", side: 1 },   // side 0 = buy, 1 = sell
  split: false,
  hoverT: null,      // hover is shared by TIME, so both panels track together
  tipOwner: null,    // ...but only the panel under the pointer shows a tooltip
};

const panels = [];

/* ---------- data selection ------------------------------------------------ */

// Which series a currency actually carries. Bank Notes is missing entirely for
// the currencies BCA does not deal in cash, so its control is disabled rather
// than silently drawing nothing.
function availableSeries(cur) {
  const rec = DATA.data[cur] || {};
  return DATA.series.filter(s => Array.isArray(rec[s.key]));
}
function activeSeries(cur) {
  return availableSeries(cur).filter(s => state.on[s.key]);
}

function visibleIndices(cur, range) {
  const t = (DATA.data[cur] || { t: [] }).t;
  if (!t.length) return [];
  const span = RANGES.find(r => r.k === range).s;
  if (span === Infinity) return t.map((_, i) => i);
  const cut = t[t.length - 1] - span;
  const out = [];
  for (let i = 0; i < t.length; i++) if (t[i] >= cut) out.push(i);
  // never render a single lonely point when earlier history exists
  if (out.length < 2 && t.length >= 2) return [t.length - 2, t.length - 1];
  return out;
}

function niceTicks(min, max, count) {
  const span = (max - min) || Math.abs(max) || 1;
  const raw = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const n = raw / mag;
  const step = (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * mag;
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) out.push(v);
  return out;
}

/* ---------- one chart panel ----------------------------------------------- */

const PANEL_HTML = `
  <div class="top">
    <select class="sel cur"></select>
    <div style="flex:1;min-width:0">
      <div class="quote"><span class="price">—</span><span class="chg"></span></div>
      <div class="sub"></div>
    </div>
  </div>
  <div class="chartbox">
    <svg class="chart"></svg>
    <div class="tip"></div>
    <div class="notice" hidden></div>
  </div>
  <div class="scroller">
    <table class="stats">
      <thead><tr>
        <th>Series</th><th>Buy</th><th>Sell</th><th>Spread</th><th>Spread %</th>
        <th>High</th><th>Low</th><th>Change</th>
      </tr></thead>
      <tbody></tbody>
    </table>
  </div>`;

function makePanel(code) {
  const root = document.createElement("section");
  root.className = "panel";
  root.innerHTML = PANEL_HTML;
  const q = s => root.querySelector(s);
  const svg = q(".chart"), tip = q(".tip"), notice = q(".notice"), sel = q("select.cur");
  const priceEl = q(".price"), chgEl = q(".chg"), subEl = q(".sub"), body = q("tbody");
  let cur = code, view = null, pan = 0;   // pan: points the window is slid back

  sel.innerHTML = DATA.currencies.map(c => `<option>${c}</option>`).join("");
  sel.value = cur;
  sel.addEventListener("change", () => { cur = sel.value; pan = 0; syncControls(); renderAll(); savePrefs(); });

  // The visible slice, plus any pan. Pan slides an equal-width window back
  // through the stored history — 0 keeps it anchored to the newest point, the
  // default — and is re-clamped here so shrinking data can never strand it.
  function idxWindow() {
    const base = visibleIndices(cur, state.range);
    const n = (DATA.data[cur] || { t: [] }).t.length, width = base.length;
    const maxPan = Math.max(0, n - width);
    if (pan > maxPan) pan = maxPan;
    if (pan < 0) pan = 0;
    if (!pan || !width) return base;
    const end = (n - 1) - pan, start = end - (width - 1), out = [];
    for (let i = start; i <= end; i++) out.push(i);
    return out;
  }
  function canPan() {
    const base = visibleIndices(cur, state.range);
    return (DATA.data[cur] || { t: [] }).t.length - base.length > 0;
  }

  // Hover is stored as a timestamp, not an index, so the second panel lands on
  // the same moment even if it were ever sampled differently.
  function hoverIndex() {
    if (state.hoverT == null) return -1;
    const t = (DATA.data[cur] || { t: [] }).t;
    let best = -1, bd = Infinity;
    for (const i of idxWindow()) {
      const d = Math.abs(t[i] - state.hoverT);
      if (d < bd) { bd = d; best = i; }
    }
    return best;
  }

  function render() {
    const rec = DATA.data[cur];
    const idx = idxWindow();
    const series = activeSeries(cur);
    const W = svg.clientWidth || 700, H = svg.clientHeight || 380;
    const M = { t: 14, r: 66, b: 26, l: 10 };
    const iw = Math.max(10, W - M.l - M.r), ih = Math.max(10, H - M.t - M.b);

    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("width", W); svg.setAttribute("height", H);

    if (!rec || !idx.length || !series.length) {
      svg.innerHTML = `<text x="${W / 2}" y="${H / 2}" text-anchor="middle">${
        !series.length ? "No series selected" : "No data for this currency"}</text>`;
      view = null; notice.hidden = true;
      renderQuote([]); renderStats([], []);
      return;
    }

    let lo = Infinity, hi = -Infinity;
    for (const s of series) for (const arm of rec[s.key]) for (const i of idx) {
      const v = arm[i];
      if (v != null) { if (v < lo) lo = v; if (v > hi) hi = v; }
    }
    if (!isFinite(lo)) { lo = 0; hi = 1; }
    if (hi - lo < Math.max(hi * 1e-6, 1e-9)) { const p = Math.abs(hi) * 0.002 || 1; lo -= p; hi += p; }
    const pad = (hi - lo) * 0.10; lo -= pad; hi += pad;

    const t = rec.t;
    // Ordinal x-axis: equal width per point, not per elapsed hour. Closed hours
    // are sampled less often (hourly vs every 15 min), so they compress on their
    // own and the trading day fills the width — a session chart, the way Moomoo
    // and TradingView draw one — with no 15-hour overnight block eating space.
    const rank = new Map();
    idx.forEach((i, k) => rank.set(i, k));
    const xOf = i => idx.length === 1 ? M.l + iw / 2
      : M.l + (rank.get(i) / (idx.length - 1)) * iw;
    const yOf = v => M.t + (1 - (v - lo) / (hi - lo)) * ih;

    const parts = [];

    // Current-value flags for the headlined side, de-collided up front so two
    // near-equal rates (18,000 and 18,010) do not stack their labels on top of
    // each other. Computing them here also lets a gridline number sitting under
    // a flag be dropped, rather than bleeding through it.
    const FLAG_H = 16, flags = [];
    for (const s of series) {
      const arm = rec[s.key][state.metric.side];
      const last = [...idx].reverse().find(i => arm[i] != null);
      if (last != null) flags.push({ c: COLORS[s.key], v: arm[last], y: yOf(arm[last]) });
    }
    flags.sort((a, b) => a.y - b.y);
    for (let i = 1; i < flags.length; i++)
      if (flags[i].y - flags[i - 1].y < FLAG_H) flags[i].y = flags[i - 1].y + FLAG_H;
    // If spreading pushed the stack past an edge, nudge it back inside the plot.
    const fTop = M.t + 8, fBot = M.t + ih - 8;
    if (flags.length && flags[flags.length - 1].y > fBot) {
      flags[flags.length - 1].y = fBot;
      for (let i = flags.length - 2; i >= 0; i--)
        if (flags[i + 1].y - flags[i].y < FLAG_H) flags[i].y = flags[i + 1].y - FLAG_H;
    }
    if (flags.length && flags[0].y < fTop) {
      flags[0].y = fTop;
      for (let i = 1; i < flags.length; i++)
        if (flags[i].y - flags[i - 1].y < FLAG_H) flags[i].y = flags[i - 1].y + FLAG_H;
    }

    for (const v of niceTicks(lo, hi, Math.max(3, Math.min(8, Math.round(ih / 54))))) {
      const y = yOf(v);
      if (y < M.t - 1 || y > M.t + ih + 1) continue;
      parts.push(`<line x1="${M.l}" y1="${y.toFixed(1)}" x2="${M.l + iw}" y2="${y.toFixed(1)}" stroke="#1c2433"/>`);
      // Drop the axis number when a value flag already occupies that row.
      if (!flags.some(f => Math.abs(f.y - y) < 9))
        parts.push(`<text x="${M.l + iw + 8}" y="${(y + 3.5).toFixed(1)}">${fmt(v)}</text>`);
    }

    // 1D only: shade the closed spans (nights, weekends) and label the first
    // one, so the trading day reads at a glance. Longer ranges skip it — they
    // would be mostly grey, and the ordinal axis already keeps closed hours thin.
    if (state.range === "1D") {
      let s0 = -1, labelled = false;
      const shade = (a, b) => {
        const x1 = a <= 0 ? M.l : (xOf(idx[a - 1]) + xOf(idx[a])) / 2;
        const x2 = b >= idx.length - 1 ? M.l + iw : (xOf(idx[b]) + xOf(idx[b + 1])) / 2;
        parts.push(`<rect x="${x1.toFixed(1)}" y="${M.t}" width="${(x2 - x1).toFixed(1)}" height="${ih}" fill="#8b97ab" fill-opacity=".06"/>`);
        if (!labelled && x2 - x1 > 34) {
          parts.push(`<text x="${((x1 + x2) / 2).toFixed(1)}" y="${(M.t + 13).toFixed(1)}" text-anchor="middle" fill="#7a879e" style="font-size:10px;letter-spacing:.06em">Closed</text>`);
          labelled = true;
        }
      };
      for (let k = 0; k < idx.length; k++) {
        const closed = isClosed(t[idx[k]]);
        if (closed && s0 < 0) s0 = k;
        else if (!closed && s0 >= 0) { shade(s0, k - 1); s0 = -1; }
      }
      if (s0 >= 0) shade(s0, idx.length - 1);
    }

    const spanSec = t[idx[idx.length - 1]] - t[idx[0]];
    const want = Math.max(2, Math.min(7, Math.floor(iw / 108)));
    const step = Math.max(1, Math.floor((idx.length - 1) / (want - 1)) || 1);
    const label = spanSec > 86400 * 2 ? fmtDay : fmtTime;
    const seen = new Set();
    for (let k = 0; k < idx.length; k += step) {
      const i = idx[k], x = xOf(i), txt = label(t[i]);
      if (seen.has(txt) && idx.length > 2) continue;
      seen.add(txt);
      const anchor = x < M.l + 26 ? "start" : x > M.l + iw - 26 ? "end" : "middle";
      parts.push(`<line x1="${x.toFixed(1)}" y1="${M.t + ih}" x2="${x.toFixed(1)}" y2="${M.t + ih + 4}" stroke="#2a3345"/>`);
      parts.push(`<text x="${x.toFixed(1)}" y="${M.t + ih + 17}" text-anchor="${anchor}">${txt}</text>`);
    }

    // The scraper never spaces reads more than an hour apart — hourly even when
    // closed — so any jump past ~90 min is a genuine outage (the process was
    // down), not the normal overnight cadence. That is the only thing that
    // breaks a line; closed hours have data and draw straight through.
    const OUTAGE = 90 * 60;

    const dots = idx.length <= 60;
    const GAP = "#6b7890";  // grey for the bridge across a gap — clearly not a rate line
    for (const s of series) {
      const [buy, sell] = rec[s.key], c = COLORS[s.key];

      // Break the series into contiguous runs — a run ends on a null or a real
      // outage — then draw each, and bridge the breaks separately.
      const runs = [];
      let run = [];
      for (const i of idx) {
        if (buy[i] == null || sell[i] == null) { if (run.length) runs.push(run), run = []; continue; }
        if (run.length && t[i] - t[run[run.length - 1]] > OUTAGE) runs.push(run), run = [];
        run.push(i);
      }
      if (run.length) runs.push(run);

      // A grey dotted line across each break, so the eye follows one series
      // through the gap without the solid coloured line claiming data is there.
      for (let r = 1; r < runs.length; r++) {
        const a = runs[r - 1][runs[r - 1].length - 1], b = runs[r][0];
        for (const arm of [buy, sell]) {
          parts.push(`<path d="M${xOf(a).toFixed(1)},${yOf(arm[a]).toFixed(1)}L${xOf(b).toFixed(1)},${yOf(arm[b]).toFixed(1)}" fill="none" stroke="${GAP}" stroke-width="1.3" stroke-dasharray="2 3" stroke-linecap="round"/>`);
        }
      }

      for (const run of runs) {
        if (run.length > 1) {
          const up = run.map(i => `${xOf(i).toFixed(1)},${yOf(buy[i]).toFixed(1)}`);
          const dn = run.slice().reverse().map(i => `${xOf(i).toFixed(1)},${yOf(sell[i]).toFixed(1)}`);
          parts.push(`<path d="M${up.join("L")}L${dn.join("L")}Z" fill="${c}" fill-opacity=".11"/>`);
          parts.push(`<path d="M${up.join("L")}" fill="none" stroke="${c}" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>`);
          parts.push(`<path d="M${run.map(i => `${xOf(i).toFixed(1)},${yOf(sell[i]).toFixed(1)}`).join("L")}" fill="none" stroke="${c}" stroke-width="1.5" stroke-dasharray="5 3.5" stroke-linecap="round"/>`);
        }
        if (dots) for (const i of run) {
          parts.push(`<circle cx="${xOf(i).toFixed(1)}" cy="${yOf(buy[i]).toFixed(1)}" r="2.8" fill="${c}"/>`);
          parts.push(`<circle cx="${xOf(i).toFixed(1)}" cy="${yOf(sell[i]).toFixed(1)}" r="2.8" fill="${c}" fill-opacity=".45" stroke="${c}" stroke-width="1"/>`);
        }
      }

    }

    // The de-collided value flags, drawn over the lines so they stay readable.
    for (const f of flags) {
      parts.push(`<rect x="${M.l + iw + 2}" y="${(f.y - 8).toFixed(1)}" width="${M.r - 6}" height="16" rx="3" fill="${f.c}" fill-opacity=".16"/>`);
      parts.push(`<text x="${M.l + iw + 8}" y="${(f.y + 3.5).toFixed(1)}" fill="${f.c}" style="font-weight:700">${fmt(f.v)}</text>`);
    }

    // High and low of the headline line over the visible range. Only the
    // headline series/side, so three series do not litter the chart with six
    // markers. Skipped while hovering, when the crosshair is doing the reading.
    const hm = state.metric;
    if (state.on[hm.key] && availableSeries(cur).some(s => s.key === hm.key) && state.hoverT == null) {
      const arm = rec[hm.key][hm.side];
      let hiI = -1, loI = -1;
      for (const i of idx) {
        if (arm[i] == null) continue;
        if (hiI < 0 || arm[i] > arm[hiI]) hiI = i;
        if (loI < 0 || arm[i] < arm[loI]) loI = i;
      }
      const mark = (i, above) => {
        if (i < 0) return;
        const x = xOf(i), y = yOf(arm[i]), dy = above ? -9 : 15;
        const anchor = x < M.l + 30 ? "start" : x > M.l + iw - 30 ? "end" : "middle";
        parts.push(`<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.6" fill="#e6edf7"/>`);
        parts.push(`<text x="${x.toFixed(1)}" y="${(y + dy).toFixed(1)}" text-anchor="${anchor}" fill="#b9c4d6" style="font-size:10px;font-weight:600">${fmt(arm[i])}</text>`);
      };
      if (hiI !== loI) { mark(hiI, true); mark(loI, false); }
    }

    const hv = hoverIndex();
    if (hv >= 0 && idx.includes(hv)) {
      const x = xOf(hv);
      parts.push(`<line x1="${x.toFixed(1)}" y1="${M.t}" x2="${x.toFixed(1)}" y2="${M.t + ih}" stroke="#5f6f8c" stroke-dasharray="3 3"/>`);
      for (const s of series) {
        for (const arm of rec[s.key]) {
          if (arm[hv] != null) parts.push(`<circle cx="${x.toFixed(1)}" cy="${yOf(arm[hv]).toFixed(1)}" r="4" fill="${COLORS[s.key]}" stroke="#0b0e14" stroke-width="1.5"/>`);
        }
      }
    }

    svg.innerHTML = parts.join("");
    view = { idx, xOf, iw, width: idx.length };

    const n = idx.length;
    notice.hidden = n > 3;
    if (!notice.hidden) {
      notice.textContent = n === 1
        ? "Only one scrape stored — there is no line to draw yet."
        : `Only ${n} scrapes stored so far — the shape will fill in as history builds.`;
    }

    renderQuote(idx);
    renderStats(series, idx);
  }

  function renderQuote(idx) {
    const rec = DATA.data[cur];
    const m = state.metric, meta = DATA.series.find(s => s.key === m.key);
    const arms = rec && rec[m.key];
    if (!arms || !idx.length) {
      priceEl.textContent = "—";
      chgEl.textContent = ""; chgEl.className = "chg";
      subEl.innerHTML = arms ? "" : `<b>${meta.label}</b> not offered for ${cur}`;
      return;
    }
    const pick = arms[m.side], other = arms[1 - m.side];
    const last = [...idx].reverse().find(i => pick[i] != null);
    const first = idx.find(i => pick[i] != null);
    priceEl.textContent = last == null ? "—" : fmt(pick[last]);

    if (last != null && first != null && first !== last && pick[first]) {
      const d = pick[last] - pick[first], pct = (d / pick[first]) * 100;
      chgEl.textContent = (d > 0 ? "+" : "") + fmt(d) + "  (" + (d > 0 ? "+" : "") + pct.toFixed(2) + "%)";
      chgEl.className = "chg " + (d > 0 ? "up" : d < 0 ? "down" : "flat");
    } else {
      chgEl.textContent = "unchanged over this range";
      chgEl.className = "chg flat";
    }
    const sp = (last != null && other[last] != null) ? Math.abs(other[last] - pick[last]) : null;
    subEl.innerHTML =
      `<b>${meta.label} ${SIDES[m.side].toLowerCase()}</b>` +
      (last == null ? "" : ` &middot; ${SIDES[1 - m.side].toLowerCase()} ${fmt(other[last])}`) +
      (sp == null ? "" : ` &middot; spread ${fmt(sp)}`) +
      (last == null ? "" : ` &middot; ${fmtFull(rec.t[last])}`);
  }

  function renderStats(series, idx) {
    const rec = DATA.data[cur];
    if (!series.length) { body.innerHTML = `<tr><td colspan="8" style="color:var(--muted)">Nothing selected.</td></tr>`; return; }
    body.innerHTML = series.map(s => {
      const [buy, sell] = rec[s.key];
      const track = rec[s.key][state.metric.side];   // high/low/change follow the headline side
      const vals = idx.map(i => track[i]).filter(v => v != null);
      const last = [...idx].reverse().find(i => track[i] != null);
      const first = idx.find(i => track[i] != null);
      const hi = vals.length ? Math.max(...vals) : null, lowv = vals.length ? Math.min(...vals) : null;
      const b = last == null ? null : buy[last], sv = last == null ? null : sell[last];
      const sp = (b != null && sv != null) ? sv - b : null;
      let chg = "—", cls = "flat";
      if (last != null && first != null && track[first]) {
        const d = track[last] - track[first];
        chg = (d > 0 ? "+" : "") + fmt(d) + " (" + (d > 0 ? "+" : "") + ((d / track[first]) * 100).toFixed(2) + "%)";
        cls = d > 0 ? "up" : d < 0 ? "down" : "flat";
      }
      return `<tr>
        <td><span class="nm"><span class="dot" style="background:${COLORS[s.key]}"></span>${s.label}</span></td>
        <td>${fmt(b)}</td><td>${fmt(sv)}</td>
        <td>${fmt(sp)}</td>
        <td>${sp == null || !b ? "—" : ((sp / b) * 100).toFixed(3) + "%"}</td>
        <td>${fmt(hi)}</td><td>${fmt(lowv)}</td>
        <td class="${cls}">${chg}</td>
      </tr>`;
    }).join("");
  }

  function nearestTime(clientX) {
    if (!view) return null;
    const box = svg.getBoundingClientRect();
    const x = clientX - box.left;
    let best = null, bd = Infinity;
    for (const i of view.idx) {
      const d = Math.abs(view.xOf(i) - x);
      if (d < bd) { bd = d; best = i; }
    }
    return best == null ? null : DATA.data[cur].t[best];
  }

  function showTip(clientX) {
    const rec = DATA.data[cur], series = activeSeries(cur), i = hoverIndex();
    if (i < 0 || !series.length) return hideTip();
    let html = `<div class="when">${fmtFull(rec.t[i])}</div>`;
    for (const s of series) {
      const [buy, sell] = rec[s.key];
      if (buy[i] == null && sell[i] == null) continue;
      const sp = (buy[i] != null && sell[i] != null) ? sell[i] - buy[i] : null;
      const hlBuy = s.key === state.metric.key && state.metric.side === 0;
      const hlSell = s.key === state.metric.key && state.metric.side === 1;
      html += `<div class="grp">
        <div class="nm"><span class="dot" style="background:${COLORS[s.key]}"></span>${s.label}</div>
        <div class="kv${hlBuy ? " pick" : ""}"><span>Buy</span><span>${fmt(buy[i])}</span></div>
        <div class="kv${hlSell ? " pick" : ""}"><span>Sell</span><span>${fmt(sell[i])}</span></div>
        <div class="kv sp"><span>Spread</span><span>${fmt(sp)}</span></div>
      </div>`;
    }
    tip.innerHTML = html;
    tip.classList.add("show");

    // Sit beside the cursor, and flip to whichever side has room. Always
    // trailing right means that on the right-hand third of the chart the panel
    // covers the very points being inspected — the crosshair ends up behind it.
    const box = svg.parentElement.getBoundingClientRect();
    const w = tip.offsetWidth, x = clientX - box.left;
    const GAP = 14, PAD = 6;
    let left;
    if (x + GAP + w <= box.width - PAD) {
      left = x + GAP;                       // room on the right, preferred
    } else if (x - GAP - w >= PAD) {
      left = x - GAP - w;                   // flip to the left of the cursor
    } else {
      // Too wide for either side: park it on the emptier half so it covers as
      // little of the live region as possible.
      left = x > box.width / 2 ? PAD : box.width - w - PAD;
    }
    tip.style.left = Math.max(PAD, Math.min(box.width - w - PAD, left)) + "px";
    tip.style.top = "14px";
  }
  // Park it back at the origin as well as hiding it. Left where it was, a
  // tooltip positioned for a wide window keeps that offset after a resize to a
  // narrow one, and an absolutely positioned box still counts towards layout
  // even at zero opacity.
  function hideTip() { tip.classList.remove("show"); tip.style.left = "0px"; }

  /* Pointer events, not mouse + touch events.
   *
   * A touchscreen fires COMPATIBILITY MOUSE EVENTS after touchend — a phantom
   * mousemove at the lifted finger's position. With a separate mousemove
   * handler that re-opens the tooltip a frame after the touch closed it, and
   * no mouseleave ever follows on a touchscreen, so it stays open forever.
   * One pointer stream has no phantoms to fight.
   */
  const fromMouse = e => e.pointerType === "mouse";

  function clearHover() {
    state.hoverT = null; state.tipOwner = null;
    panels.forEach(p => p.hideTip());
    renderAll();
  }

  function moveTo(clientX) {
    const ts = nearestTime(clientX);
    if (ts !== state.hoverT) { state.hoverT = ts; renderAll(); }
    state.tipOwner = api;
    showTip(clientX);
  }

  /* Two touch gestures share the chart, and the job is telling them apart
   * without stealing the page's own vertical scroll:
   *
   *   drag sideways   -> pan the timeline back and forth through history
   *   press and hold  -> scrub, showing the crosshair and detail tooltip
   *   drag upwards    -> left to the browser, so the page still scrolls
   *
   * `mode` latches to the FIRST gesture to qualify and nothing else fires until
   * the finger lifts, so a pan can never trip the hold-to-scrub or vice versa —
   * that mutual exclusion is what keeps the two from colliding. A press that has
   * not moved for HOLD_MS becomes a scrub; one that moves sideways first (when
   * there is history to pan to) becomes a pan.
   */
  const HOLD_MS = 260, SLOP = 10;
  let mode = null;              // null | "pan" | "scrub"
  let holdTimer = null, pid = null, sx = 0, sy = 0, panStart = 0;

  function pointsPerPx() {
    return (view && view.width > 1 && view.iw) ? (view.width - 1) / view.iw : 0;
  }
  // Drag right -> older data slides in from the left (pan grows), the way
  // grabbing and pulling the paper would move it.
  function panTo(clientX) {
    const ppx = pointsPerPx();
    if (!ppx) return;
    pan = panStart + (clientX - sx) * ppx;   // idxWindow() clamps it to the ends
    render();
  }

  function beginPan() {
    clearTimeout(holdTimer);
    mode = "pan"; panStart = pan;
    state.hoverT = null; hideTip();
    // Capture so the finger can wander off the chart and the pan still tracks.
    try { svg.setPointerCapture(pid); } catch (_) {}
  }

  function beginScrub(clientX) {
    clearTimeout(holdTimer);
    mode = "scrub";
    // Capture, so the finger can leave the chart — off the top, onto the stats
    // table — and the scrub still tracks instead of dying mid-gesture.
    try { svg.setPointerCapture(pid); } catch (_) {}
    // touch-action cannot be changed mid-gesture; the browser has already
    // classified this one. The class is for the NEXT gesture, and preventDefault
    // below carries this one.
    svg.classList.add("scrubbing");
    moveTo(clientX);
    if (navigator.vibrate) navigator.vibrate(8);
  }

  function endGesture() {
    clearTimeout(holdTimer);
    const wasScrub = mode === "scrub";
    if (mode) { try { svg.releasePointerCapture(pid); } catch (_) {} }
    svg.classList.remove("scrubbing");
    mode = null; pid = null;
    if (wasScrub) clearHover();          // panning just leaves the view put
  }

  svg.addEventListener("pointerdown", e => {
    if (fromMouse(e)) { pid = e.pointerId; sx = e.clientX; sy = e.clientY; panStart = pan; mode = null; return; }
    pid = e.pointerId; sx = e.clientX; sy = e.clientY; mode = null;
    clearTimeout(holdTimer);
    holdTimer = setTimeout(() => { if (pid === e.pointerId && mode === null) beginScrub(sx); }, HOLD_MS);
  });

  svg.addEventListener("pointermove", e => {
    if (fromMouse(e)) {
      if (e.buttons & 1) {                 // button held: drag to pan
        if (mode !== "pan") {
          mode = "pan"; sx = e.clientX; panStart = pan; state.hoverT = null; hideTip();
          try { svg.setPointerCapture(pid); } catch (_) {}
        }
        panTo(e.clientX);
      } else {
        if (mode === "pan") mode = null;
        moveTo(e.clientX);                 // no button: plain hover scrub
      }
      return;
    }
    if (e.pointerId !== pid) return;

    if (mode === null) {
      const dx = e.clientX - sx, dy = e.clientY - sy;
      if (Math.abs(dx) > SLOP && Math.abs(dx) > Math.abs(dy)) {
        // Sideways: pan if there is history off-screen, otherwise fall back to
        // scrubbing so a swipe is never a dead gesture.
        if (canPan()) { beginPan(); panTo(e.clientX); } else beginScrub(e.clientX);
      } else if (Math.abs(dy) > SLOP) { clearTimeout(holdTimer); pid = null; }  // upwards: page scroll
      return;
    }
    e.preventDefault();
    if (mode === "pan") panTo(e.clientX); else moveTo(e.clientX);
  }, { passive: false });

  // Lifting the finger ends whatever was going on; a scrub also closes its tip.
  svg.addEventListener("pointerup", e => {
    if (fromMouse(e)) { if (mode) { try { svg.releasePointerCapture(pid); } catch (_) {} } mode = null; pid = null; }
    else endGesture();
  });
  svg.addEventListener("pointercancel", e => {
    if (fromMouse(e)) { mode = null; pid = null; } else endGesture();
  });
  svg.addEventListener("pointerleave", e => { if (fromMouse(e) && mode !== "pan") clearHover(); });

  const api = {
    root, render, hideTip,
    resetPan() { pan = 0; },
    get currency() { return cur; },
    set currency(v) { cur = v; sel.value = v; },
  };
  return api;
}

/* ---------- global controls ----------------------------------------------- */

function renderAll() {
  panels.forEach(p => p.render());
  if (state.tipOwner) panels.forEach(p => { if (p !== state.tipOwner) p.hideTip(); });
}

function buildMetric() {
  const opts = [];
  for (const s of DATA.series) for (let side = 0; side < 2; side++) {
    opts.push(`<option value="${s.key}:${side}">${s.label} ${SIDES[side]}</option>`);
  }
  el("metric").innerHTML = opts.join("");
  el("metric").value = `${state.metric.key}:${state.metric.side}`;
  el("metric").addEventListener("change", e => {
    const [key, side] = e.target.value.split(":");
    state.metric = { key, side: +side };
    // headlining a series nobody can see is a dead number, so switch it on
    if (!state.on[key]) { state.on[key] = true; syncControls(); }
    renderAll(); savePrefs();
  });
}

// A series control is only dead if NONE of the shown currencies carries it —
// with USD and CNH side by side, Bank Notes still matters to USD.
function shownCurrencies() { return panels.map(p => p.currency); }

function syncControls() {
  const usable = new Set();
  for (const c of shownCurrencies()) for (const s of availableSeries(c)) usable.add(s.key);

  el("toggles").innerHTML = DATA.series.map(s => {
    const has = usable.has(s.key), on = has && state.on[s.key];
    return `<label class="tg ${on ? "on" : ""} ${has ? "" : "off-data"}" data-k="${s.key}"
              title="${has ? "Show buy and sell for " + s.label : s.label + " is not offered for the selected currencies"}">
      <input type="checkbox" ${on ? "checked" : ""} ${has ? "" : "disabled"} data-k="${s.key}">
      <span class="sw"></span><span>${s.label}</span>
    </label>`;
  }).join("");
  el("toggles").querySelectorAll("input").forEach(inp => {
    inp.addEventListener("change", () => {
      state.on[inp.dataset.k] = inp.checked;
      inp.closest(".tg").classList.toggle("on", inp.checked);
      renderAll();
    });
  });

  const t = (DATA.data[shownCurrencies()[0]] || { t: [] }).t;
  const span = t.length > 1 ? t[t.length - 1] - t[0] : 0;
  el("ranges").innerHTML = RANGES.map(r => {
    const usableR = r.s === Infinity || span >= r.s * 0.15 || r.s <= 86400;
    return `<button data-r="${r.k}" class="${state.range === r.k ? "on" : ""}" ${usableR ? "" : "disabled"}>${r.k}</button>`;
  }).join("");
  el("ranges").querySelectorAll("button").forEach(b => {
    b.addEventListener("click", () => {
      state.range = b.dataset.r;
      el("ranges").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
      state.hoverT = null; panels.forEach(p => { p.resetPan(); p.hideTip(); });
      renderAll(); savePrefs();
    });
  });

  el("cov").textContent = `${t.length} scrape${t.length === 1 ? "" : "s"} stored` +
    (t.length ? ` · ${fmtFull(t[0])} → ${fmtFull(t[t.length - 1])}` : "");
}

function pick(preferred, avoid) {
  if (DATA.currencies.includes(preferred) && preferred !== avoid) return preferred;
  return DATA.currencies.find(c => c !== avoid) || DATA.currencies[0];
}

function setSplit(on) {
  state.split = on;
  const host = el("panels");
  host.classList.toggle("split", on);
  el("split").classList.toggle("on", on);

  if (on && panels.length === 1) {
    const second = makePanel(pick("CNH", panels[0].currency));
    panels.push(second);
    host.appendChild(second.root);
  } else if (!on && panels.length === 2) {
    host.removeChild(panels.pop().root);
  }
  state.hoverT = null;
  syncControls();
  renderAll();
  savePrefs();
}

/* ---------- keeping itself current ---------------------------------------- *
 *
 * The page is a snapshot: the history is baked in as JSON when the scraper
 * writes it, so without this it shows whatever was true at load and never
 * moves. Rather than reload — which would throw away the chosen currencies,
 * range and metric — it refetches itself, lifts the payload back out and
 * re-renders in place.
 *
 * Timed to the scrape slots instead of a blind interval: a poll landing at
 * :07 finds exactly what the one at :02 already had.
 */
const REFRESH_GRACE_MS = 30000;   // let the scrape finish and the file be written

function msToNextSlot() {
  const now = new Date();
  // WIB is a whole-hour offset, so quarter-hour boundaries land at the same
  // instant whatever timezone the viewer's clock is set to.
  const toQuarter = 15 - (now.getMinutes() % 15);
  return (toQuarter * 60 - now.getSeconds()) * 1000 - now.getMilliseconds() + REFRESH_GRACE_MS;
}

function newestStamp(d) {
  const first = d.currencies[0];
  const t = first ? d.data[first].t : [];
  return t.length ? t[t.length - 1] : 0;
}

async function pullFresh() {
  const url = location.pathname + "?_=" + Date.now();
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) return false;
  const text = await res.text();
  const m = text.match(/let DATA = (\{[\s\S]*?\});\s*\n/);
  if (!m) return false;
  const fresh = JSON.parse(m[1]);
  if (newestStamp(fresh) === newestStamp(DATA)) return false;

  DATA = fresh;
  // A currency could in principle vanish from the page; keep panels on
  // something that still exists.
  panels.forEach(p => {
    if (!DATA.currencies.includes(p.currency)) p.currency = DATA.currencies[0];
  });
  syncControls();
  renderAll();
  return true;
}

function flashUpdated() {
  const cov = el("cov");
  cov.style.color = "var(--up)";
  setTimeout(() => { cov.style.color = ""; }, 2500);
}

function scheduleRefresh() {
  setTimeout(async () => {
    // Never yank the ground out from under a finger mid-scrub.
    if (state.hoverT != null) { setTimeout(scheduleRefresh, 15000); return; }
    try {
      if (await pullFresh()) flashUpdated();
    } catch (_) {
      /* offline or the NAS is unreachable — the footer badge already says so,
         and the next slot will try again */
    }
    scheduleRefresh();
  }, msToNextSlot());
}

/* ---------- market status + remembering the view -------------------------- */

const DAY_ABBR = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function nextOpenLabel(nowSec) {
  const d = new Date((nowSec + 7 * 3600) * 1000);
  const day = d.getUTCDay(), mins = d.getUTCHours() * 60 + d.getUTCMinutes();
  if (day >= 1 && day <= 5 && mins < OPEN_MIN) return "08:00 WIB";
  let add = 1, wd = (day + 1) % 7;
  while (wd === 0 || wd === 6) { add++; wd = (wd + 1) % 7; }
  return DAY_ABBR[(day + add) % 7] + " 08:00";
}

function updateMarket() {
  const m = el("market");
  const closed = isClosed(Date.now() / 1000);
  m.hidden = false;
  m.className = "market " + (closed ? "closed" : "open");
  m.textContent = closed ? "Closed · opens " + nextOpenLabel(Date.now() / 1000) : "Market open";
}

const PREFS_KEY = "bca-forex-view";

function savePrefs() {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify({
      range: state.range, metric: state.metric, split: state.split,
      currencies: panels.map(p => p.currency),
    }));
  } catch (_) { /* private mode / storage full — remembering is a nicety */ }
}

function loadPrefs() {
  try { return JSON.parse(localStorage.getItem(PREFS_KEY)) || {}; }
  catch (_) { return {}; }
}

function init() {
  if (!DATA.currencies.length) {
    document.querySelector(".wrap").innerHTML =
      "<p style='color:#8b97ab'>The database has no rates yet. Run <code>python -m bca_forex once</code> and rebuild this chart.</p>";
    return;
  }

  // Restore the last view, validating everything — a saved currency or metric
  // may have vanished since, and a stale range must not wedge the page.
  const saved = loadPrefs();
  if (saved.range && RANGES.some(r => r.k === saved.range)) state.range = saved.range;
  if (saved.metric && DATA.series.some(s => s.key === saved.metric.key) &&
      (saved.metric.side === 0 || saved.metric.side === 1)) state.metric = saved.metric;

  buildMetric();

  const savedCur = saved.currencies || [];
  const c0 = DATA.currencies.includes(savedCur[0]) ? savedCur[0] : pick("USD", null);
  const first = makePanel(c0);
  panels.push(first);
  el("panels").appendChild(first.root);

  el("split").addEventListener("click", () => setSplit(!state.split));

  if (saved.split) {
    setSplit(true);
    if (DATA.currencies.includes(savedCur[1])) { panels[1].currency = savedCur[1]; savePrefs(); }
  }

  updateMarket();
  setInterval(updateMarket, 60000);   // flip at 08:00 / 17:00 without a reload

  el("stamp").textContent = DATA.pageUpdated ? "· BCA page: " + DATA.pageUpdated : "";
  el("fails").innerHTML = DATA.failedRuns
    ? `· <span class="warn">${DATA.failedRuns} failed scrape${DATA.failedRuns === 1 ? "" : "s"}</span>` : "";

  syncControls();
  renderAll();
  addEventListener("resize", () => {
    state.hoverT = null; panels.forEach(p => p.hideTip());
    renderAll();
  });

  // Installed to a home screen, the page can be opened well away from the NAS,
  // where the service worker serves the last copy it saw. Say so.
  const offline = el("offline");
  const flagOffline = () => { offline.hidden = navigator.onLine; };
  addEventListener("online", flagOffline);
  addEventListener("offline", flagOffline);
  flagOffline();

  scheduleRefresh();
  // Coming back to a page left open on a phone should not show yesterday.
  addEventListener("visibilitychange", () => {
    if (!document.hidden) pullFresh().then(ok => ok && flashUpdated()).catch(() => {});
  });
}
init();
</script>
</body>
</html>
"""
