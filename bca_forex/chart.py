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

#: BCA's wordmark (the mark plus "BCA") for the header, and the mark alone for
#: the app icon and favicon. Bundled inside the package rather than beside it
#: so they travel with the module — the Docker image copies only `bca_forex/`.
#: Both are drawn in one flat navy (#039); the page recolours them.
ASSETS = Path(__file__).resolve().parent / "assets"
BUNDLED_LOGO = ASSETS / "BCA-Logo.svg"
BUNDLED_ICON = ASSETS / "BCA-Icon.svg"
ART_FILL = 'fill="#039"'

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

    The bundled wordmark is recoloured to `currentColor` and sits on the dark
    header directly, white, the way a one-colour mark is reversed out of a
    dark background. A supplied file has colours of its own we cannot know,
    so it goes on a white plate instead — dropped straight onto this header,
    a mark drawn for white paper reads as a smudge.
    """
    if path is None:
        if not BUNDLED_LOGO.exists():
            return FALLBACK_LOGO
        art = _inline_svg(BUNDLED_LOGO.read_text(encoding="utf-8"))
        return f'<span class="mark">{art.replace(ART_FILL, "fill=\"currentColor\"")}</span>'

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


def app_icon_svg(corner: float = 0.22, content: float = 0.64) -> str:
    """The app icon: BCA's mark in white on a square of the bank's blue.

    One source for every icon — the page's favicon uses it directly, and
    tools/make_icons.py renders it to the PWA PNGs. `corner` is the rounding
    as a fraction of the side (0 for icons the OS masks itself), `content` how
    much of the side the mark spans (smaller for a maskable icon, which may be
    cropped to a circle).
    """
    raw = BUNDLED_ICON.read_text(encoding="utf-8")
    x0, y0, w, h = (float(v) for v in re.search(r'viewBox="([^"]+)"', raw).group(1).split())
    paths = "".join(re.findall(r"<path\b[^>]*/>", raw)).replace(ART_FILL, 'fill="#fff"')
    k = content * 100 / max(w, h)
    tx, ty = (100 - w * k) / 2 - x0 * k, (100 - h * k) / 2 - y0 * k
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        '<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#0a74c9"/><stop offset="1" stop-color="#003f8f"/>'
        "</linearGradient></defs>"
        f'<rect width="100" height="100" rx="{corner * 100:g}" fill="url(#bg)"/>'
        f'<g transform="translate({tx:.3f} {ty:.3f}) scale({k:.5f})">{paths}</g></svg>'
    )


def favicon_link() -> str:
    """The icon as a data URI, so even the plain one-file chart has a tab icon.

    Base64 rather than URL-encoded: the SVG's xmlns would otherwise put an
    http:// URL in the page head, and nothing in there is meant to be fetched.
    """
    if not BUNDLED_ICON.exists():
        return ""
    data = base64.b64encode(app_icon_svg().encode("utf-8")).decode("ascii")
    return f'<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,{data}">'


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
    from .api import e_rate_open  # here, not at the top: api imports this module

    return {
        "open": e_rate_open(conn),
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
#
# The icons are cached cache-first, so new artwork under the same file names
# never reaches an installed app. Bump CACHE whenever the icons change: the
# activate step deletes every cache not named here.
SERVICE_WORKER = """\
const CACHE = "bca-forex-v2";
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
  // The JSON API is for other apps. Opening it in a browser counts as a
  // navigation, which would otherwise be cached over the page shell.
  if (new URL(req.url).pathname.includes("/api/")) return;

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
        .replace("<!--__ICON__-->", favicon_link())
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
<!--__ICON__-->
<!--__PWA__-->
<style>
  :root {
    --bg: #06080d; --panel: #0d121b; --panel-2: #141a26; --raise: #1c2433;
    --line: rgba(148,163,184,.11); --line-2: rgba(148,163,184,.21);
    --text: #e8eef8; --muted: #8592a8; --dim: #5d687c;
    --up: #34d399; --down: #f87171; --warn: #fbbf24;
    --c-e_rate: #38bdf8; --c-tt: #fbbf24; --c-bank_notes: #c084fc;
    --ease: cubic-bezier(.22,1,.36,1); --spring: cubic-bezier(.34,1.56,.64,1);
    --pad: 16px;
    color-scheme: dark;
  }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  html, body { margin: 0; min-height: 100%; }
  body {
    background: var(--bg); color: var(--text);
    font: 13px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI Variable Text", "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-variant-numeric: tabular-nums; -webkit-font-smoothing: antialiased;
  }
  /* Soft colour washes behind everything. A fixed pseudo-element rather than
     background-attachment: fixed, which stutters on iOS. */
  body::before {
    content: ""; position: fixed; inset: 0; z-index: -1; pointer-events: none;
    background:
      radial-gradient(900px 520px at 8% -12%, rgba(56,189,248,.13), transparent 62%),
      radial-gradient(780px 480px at 104% -6%, rgba(192,132,252,.10), transparent 60%),
      radial-gradient(700px 420px at 50% 112%, rgba(251,191,36,.05), transparent 60%);
  }
  .wrap { max-width: 1420px; margin: 0 auto; padding: 16px; display: flex; flex-direction: column; gap: 14px; }
  button, select { font-family: inherit; }
  :focus-visible { outline: 2px solid var(--c-e_rate); outline-offset: 2px; }

  /* Everything arrives once, staggered, instead of popping in as one slab. */
  .rise { animation: rise .7s var(--ease) both; animation-delay: calc(var(--i, 0) * 70ms); }
  @keyframes rise { from { opacity: 0; transform: translateY(12px); } }
  @keyframes spin { to { transform: rotate(360deg); } }
  @keyframes pulse {
    0% { box-shadow: 0 0 0 0 rgba(52,211,153,.55); }
    70%, 100% { box-shadow: 0 0 0 7px rgba(52,211,153,0); }
  }

  .card {
    position: relative;
    background: linear-gradient(180deg, rgba(255,255,255,.04), rgba(255,255,255,.012) 38%), var(--panel);
    border: 1px solid var(--line); border-radius: 18px;
    box-shadow: inset 0 1px 0 rgba(255,255,255,.05), 0 24px 60px -30px rgba(0,0,0,.8);
  }

  /* ---- header ---- */
  header.brand { display: flex; align-items: center; gap: 14px; padding: 2px 2px 0; }
  header.brand .logo { flex: none; display: flex; align-items: center; }
  /* The bank's mark is one flat blue for white paper, so it gets a white plate
     to sit on rather than being recoloured — a repainted logo is the wrong
     logo. The fallback monogram carries its own background and skips this. */
  header.brand .logo .plate {
    display: flex; align-items: center; background: #fff;
    padding: 7px 11px; border-radius: 11px;
    box-shadow: 0 8px 24px -10px rgba(56,189,248,.55);
  }
  header.brand .logo svg, header.brand .logo img { display: block; height: 34px; width: auto; max-width: 118px; }
  header.brand .logo > svg { height: 42px; border-radius: 11px; }
  header.brand .logo .mark { display: flex; color: #fff; filter: drop-shadow(0 4px 14px rgba(56,189,248,.35)); }
  header.brand .logo .mark svg { height: 36px; }
  header.brand .who { flex: 1; min-width: 0; }
  header.brand .t { font-size: 17px; font-weight: 700; letter-spacing: -.3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  header.brand .t .ver {
    font-size: 10.5px; font-weight: 600; color: var(--muted); letter-spacing: .02em;
    border: 1px solid var(--line-2); border-radius: 999px; padding: 2px 7px; margin-left: 7px; vertical-align: middle;
  }
  header.brand .s { color: var(--muted); font-size: 11.5px; }
  .status { flex: none; display: flex; align-items: center; gap: 10px; }
  .market {
    display: inline-flex; align-items: center; gap: 8px; white-space: nowrap;
    font-size: 11.5px; font-weight: 600; color: var(--muted);
    background: rgba(255,255,255,.03); border: 1px solid var(--line); border-radius: 999px; padding: 6px 12px;
  }
  .market::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
  .market.open { color: var(--up); border-color: rgba(52,211,153,.3); background: rgba(52,211,153,.07); }
  .market.open::before { animation: pulse 2s infinite; }
  /* Loading state for the self-refresh: a ring that spins only while a fetch
     is in flight, beside how old the newest scrape is. */
  .sync { display: inline-flex; align-items: center; gap: 7px; color: var(--muted); font-size: 11.5px; white-space: nowrap; }
  .sync .ring { width: 11px; height: 11px; border-radius: 50%; border: 1.6px solid var(--line-2); border-top-color: var(--c-e_rate); opacity: .0; transition: opacity .2s; }
  .sync.busy .ring { opacity: 1; animation: spin .7s linear infinite; }
  a.src {
    flex: none; color: var(--muted); font-size: 11.5px; text-decoration: none; white-space: nowrap;
    border: 1px solid var(--line); border-radius: 999px; padding: 6px 12px; transition: color .2s, border-color .2s;
  }
  a.src:hover { color: var(--text); border-color: var(--line-2); }

  /* ---- toolbar: applies to every panel at once, which is the whole point of
     the second panel — two currencies only line up if they are on the same
     side, the same series and the same range. ---- */
  .toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 8px; border-radius: 16px; }
  .toolbar .grow { flex: 1; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .tg {
    position: relative; display: inline-flex; align-items: center; justify-content: center; gap: 8px;
    cursor: pointer; user-select: none; white-space: nowrap;
    padding: 8px 14px 8px 10px; border-radius: 999px; font-weight: 600; color: var(--muted);
    background: rgba(255,255,255,.02); border: 1px solid var(--line);
    transition: color .2s, background .25s, border-color .25s, transform .2s var(--spring);
  }
  .tg:hover { border-color: var(--line-2); color: var(--text); }
  .tg:active { transform: scale(.95); }
  .tg input { position: absolute; opacity: 0; pointer-events: none; }
  .tg .sw { position: relative; width: 15px; height: 15px; border-radius: 50%; border: 2px solid var(--c); transition: background .25s var(--ease), box-shadow .25s; }
  .tg .sw::after {
    content: ""; position: absolute; left: 3.6px; top: .6px; width: 3.4px; height: 7px;
    border: solid #06080d; border-width: 0 2px 2px 0;
    transform: rotate(45deg) scale(0); transition: transform .3s var(--spring);
  }
  .tg.on { color: var(--text); background: color-mix(in srgb, var(--c) 11%, transparent); border-color: color-mix(in srgb, var(--c) 38%, transparent); }
  .tg.on .sw { background: var(--c); box-shadow: 0 0 12px color-mix(in srgb, var(--c) 65%, transparent); }
  .tg.on .sw::after { transform: rotate(45deg) scale(1); }
  .tg.off-data { opacity: .35; cursor: not-allowed; }
  .tg:has(input:focus-visible) { outline: 2px solid var(--c-e_rate); outline-offset: 2px; }

  /* Segmented control with a thumb that slides to the chosen button. */
  .seg { position: relative; display: inline-flex; padding: 3px; border-radius: 12px; background: rgba(0,0,0,.3); border: 1px solid var(--line); }
  .seg .thumb {
    position: absolute; top: 3px; bottom: 3px; left: 0; width: 0; border-radius: 9px; opacity: 0;
    background: var(--raise); border: 1px solid var(--line-2);
    box-shadow: 0 3px 10px rgba(0,0,0,.4), inset 0 1px 0 rgba(255,255,255,.07);
  }
  .seg.ready .thumb { transition: transform .4s var(--spring), width .4s var(--spring), opacity .2s; }
  .seg button {
    position: relative; z-index: 1; background: none; border: 0; color: var(--muted);
    font-size: 12px; font-weight: 650; line-height: 1; padding: 9px 12px; border-radius: 9px; cursor: pointer;
    transition: color .2s, transform .2s var(--spring);
  }
  .seg button:hover:not(:disabled) { color: var(--text); }
  .seg button:active:not(:disabled) { transform: scale(.94); }
  .seg button.on { color: #fff; }
  .seg button:disabled { opacity: .3; cursor: not-allowed; }

  /* Compare: a switch whose knob stretches while pressed. */
  .switch {
    display: inline-flex; align-items: center; gap: 9px; background: none; border: 0; cursor: pointer;
    color: var(--muted); font-size: 12.5px; font-weight: 650; padding: 6px 6px; white-space: nowrap; transition: color .2s;
  }
  .switch .track { position: relative; width: 36px; height: 21px; border-radius: 999px; background: var(--raise); border: 1px solid var(--line-2); transition: background .3s, border-color .3s; }
  .switch .knob {
    position: absolute; top: 2px; left: 2px; width: 15px; height: 15px; border-radius: 999px; background: #aab6c8;
    box-shadow: 0 2px 5px rgba(0,0,0,.4); transition: transform .4s var(--spring), width .2s var(--ease), background .3s;
  }
  .switch:active .knob { width: 20px; }
  .switch:hover { color: var(--text); }
  .switch.on { color: var(--text); }
  .switch.on .track { background: color-mix(in srgb, var(--c-e_rate) 60%, #0b1220); border-color: transparent; }
  .switch.on .knob { transform: translateX(15px); background: #fff; }
  .switch.on:active .knob { transform: translateX(10px); }

  /* ---- panels ---- */
  .panels { display: grid; grid-template-columns: 1fr; gap: 14px; }
  /* Side by side only once a half-width panel can hold three rate cards.
     Two cramped charts read worse than two full-width stacked ones. */
  @media (min-width: 1200px) { .panels.split { grid-template-columns: 1fr 1fr; } }

  .panel { display: flex; flex-direction: column; gap: 14px; min-width: 0; padding: var(--pad); overflow: hidden; }
  /* A glow in the headline series' colour, so the panel says at a glance
     which rate the big number is. */
  .panel::before {
    content: ""; position: absolute; inset: 0; pointer-events: none; border-radius: inherit;
    background: radial-gradient(520px 240px at 0% 0%, color-mix(in srgb, var(--glow, #38bdf8) 16%, transparent), transparent 70%);
    transition: background .5s;
  }
  .panel > * { position: relative; }
  .hero { display: flex; align-items: center; flex-wrap: wrap; gap: 12px 18px; }
  .curpick { position: relative; flex: none; }
  .curpick::after {
    content: ""; position: absolute; right: 15px; top: 50%; width: 7px; height: 7px; pointer-events: none;
    border: solid var(--muted); border-width: 0 2px 2px 0; transform: translateY(-70%) rotate(45deg);
  }
  select.cur {
    appearance: none; -webkit-appearance: none; cursor: pointer;
    background: var(--panel-2); color: var(--text); border: 1px solid var(--line-2); border-radius: 13px;
    font-size: 19px; font-weight: 750; letter-spacing: .05em; padding: 12px 38px 12px 15px;
    transition: border-color .2s, background .2s;
  }
  select.cur:hover { border-color: rgba(148,163,184,.35); background: var(--raise); }
  .q { flex: 1; min-width: 0; }
  .eyebrow { color: var(--muted); font-size: 11.5px; font-weight: 600; letter-spacing: .02em; margin-bottom: 3px; }
  .quote { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .price { font-size: 40px; font-weight: 750; letter-spacing: -1.2px; line-height: 1.02; }
  .price.flash-up { animation: flashUp 1.4s var(--ease); }
  .price.flash-down { animation: flashDown 1.4s var(--ease); }
  @keyframes flashUp { 0%, 25% { color: var(--up); text-shadow: 0 0 22px rgba(52,211,153,.55); } }
  @keyframes flashDown { 0%, 25% { color: var(--down); text-shadow: 0 0 22px rgba(248,113,113,.55); } }
  .chg { display: inline-flex; align-items: center; gap: 5px; font-size: 13px; font-weight: 650; padding: 4px 9px; border-radius: 999px; }
  .chg.up { color: var(--up); background: rgba(52,211,153,.1); }
  .chg.down { color: var(--down); background: rgba(248,113,113,.1); }
  .chg.flat { color: var(--muted); background: rgba(148,163,184,.08); }
  .up { color: var(--up); } .down { color: var(--down); } .flat { color: var(--muted); }
  .sub { color: var(--muted); font-size: 11.5px; margin-top: 6px; }
  .sub b { color: #c3cddc; font-weight: 600; }

  /* overflow:hidden is load-bearing: the tooltip is absolutely positioned, and
     without this a stale offset could extend the page's scroll width and give
     the whole document a horizontal scrollbar. */
  .chartbox { position: relative; overflow: hidden; margin: 0 -6px; }
  .chart {
    display: block; width: 100%; height: 58vh; min-height: 330px; max-height: 580px;
    /* Vertical drags scroll the page; sideways ones are the chart's. */
    touch-action: pan-y;
    /* A long press would otherwise start a text selection or pop the iOS
       callout, and either one cancels the gesture mid-scrub. */
    -webkit-user-select: none; user-select: none; -webkit-touch-callout: none;
  }
  .chart.scrubbing { touch-action: none; }
  .panels.split .chart { height: 44vh; min-height: 280px; max-height: 440px; }
  .chart text { fill: var(--muted); font-size: 10.5px; }
  .notice {
    position: absolute; inset: auto 0 0 0; margin: 0 auto 14px; width: max-content; max-width: 92%;
    background: rgba(20,26,38,.9); border: 1px solid var(--line-2); color: var(--muted);
    padding: 7px 13px; border-radius: 10px; font-size: 12px; text-align: center;
  }
  .tip {
    position: absolute; pointer-events: none; opacity: 0; visibility: hidden; z-index: 5;
    max-width: calc(100% - 12px); min-width: 190px; padding: 10px 12px;
    background: rgba(10,14,22,.78); border: 1px solid var(--line-2); border-radius: 13px;
    -webkit-backdrop-filter: blur(14px) saturate(150%); backdrop-filter: blur(14px) saturate(150%);
    box-shadow: 0 16px 40px rgba(0,0,0,.55);
    transform: translateY(4px) scale(.98); transition: opacity .14s, transform .22s var(--ease), visibility .14s;
  }
  .tip.show { opacity: 1; visibility: visible; transform: none; }
  .tip .when { color: var(--muted); font-size: 11px; margin-bottom: 8px; }
  .tip .grp { margin-top: 8px; }
  .tip .grp:first-of-type { margin-top: 0; }
  .tip .nm { display: flex; align-items: center; gap: 6px; font-weight: 700; font-size: 11.5px; margin-bottom: 3px; }
  .tip .dot { width: 8px; height: 8px; border-radius: 50%; }
  .tip .kv { display: flex; justify-content: space-between; gap: 18px; font-size: 12px; }
  .tip .kv span:first-child { color: var(--muted); }
  .tip .kv.sp span:last-child { color: #b9c4d6; }
  .tip .kv.pick span { color: #fff; font-weight: 700; }

  /* ---- rate cards: one per series, tap to headline it ---- */
  .tiles { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
  .tile {
    font: inherit; color: inherit; text-align: left; cursor: pointer; position: relative; overflow: hidden;
    display: flex; flex-direction: column; gap: 5px; min-width: 0; padding: 13px 14px 12px; border-radius: 14px;
    background: rgba(255,255,255,.022); border: 1px solid var(--line);
    transition: border-color .25s, background .3s, transform .25s var(--spring), opacity .25s, box-shadow .3s;
  }
  .tile:hover { border-color: var(--line-2); transform: translateY(-2px); }
  .tile:active { transform: scale(.98); }
  .tile.on {
    border-color: color-mix(in srgb, var(--c) 45%, transparent);
    background: linear-gradient(180deg, color-mix(in srgb, var(--c) 11%, transparent), transparent 75%);
    box-shadow: 0 14px 34px -22px var(--c);
  }
  .tile.off:not(.on) { opacity: .55; }
  .tile.na { cursor: default; opacity: .45; }
  .tile.na:hover { transform: none; border-color: var(--line); }
  .tile .th { display: flex; align-items: center; gap: 7px; color: var(--muted); font-size: 12px; font-weight: 650; }
  .tile .th .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--c); box-shadow: 0 0 8px var(--c); }
  .tile .tag {
    margin-left: auto; font-size: 9.5px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: var(--c);
    opacity: 0; transform: translateX(6px); transition: opacity .25s, transform .3s var(--ease);
  }
  .tile.on .tag { opacity: 1; transform: none; }
  .tile .tv { font-size: 21px; font-weight: 750; letter-spacing: -.4px; }
  .tile .tv small { font-size: 11px; font-weight: 600; color: var(--muted); letter-spacing: 0; margin-left: 4px; }
  .tile .tc { font-size: 12px; font-weight: 650; }
  .tile .spark { display: block; width: 100%; height: 34px; margin: 2px 0; }
  .tile dl { margin: 2px 0 0; display: grid; grid-template-columns: 1fr 1fr; gap: 3px 14px; font-size: 11.5px; }
  .tile dl div { display: flex; justify-content: space-between; gap: 8px; min-width: 0; }
  .tile dt { color: var(--dim); }
  .tile dd { margin: 0; color: #c3cddc; white-space: nowrap; }
  .tile .na-msg { color: var(--muted); font-size: 12px; margin-top: 6px; }
  .panels.split .tile dl { grid-template-columns: 1fr; }

  footer { color: var(--muted); font-size: 11.5px; display: flex; flex-wrap: wrap; align-items: center; gap: 6px 16px; padding: 0 4px 8px; }
  .warn { color: var(--warn); }
  /* Cached-and-offline has to announce itself. A rate page that silently shows
     yesterday's numbers as though they were current is worse than one that
     fails outright. */
  #offline[hidden] { display: none; }
  #offline {
    display: inline-flex; align-items: center; gap: 7px; color: var(--warn);
    background: rgba(251,191,36,.10); border: 1px solid rgba(251,191,36,.35);
    border-radius: 999px; padding: 5px 12px; font-weight: 600;
  }
  #offline::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: var(--warn); }

  /* Tablets: the toolbar wraps onto a second line before the chips squash. */
  @media (max-width: 900px) {
    .toolbar .grow { display: none; }
  }

  /* Phones. Each control row takes the full width, and the rate cards become
     a swipeable row instead of three squeezed columns. */
  @media (max-width: 620px) {
    :root { --pad: 13px; }
    .wrap { padding: 12px; gap: 12px; }
    header.brand { gap: 10px; }
    header.brand .logo .plate { padding: 5px 8px; border-radius: 9px; }
    header.brand .logo svg, header.brand .logo img { height: 26px; max-width: 92px; }
    header.brand .logo > svg { height: 34px; }
    header.brand .logo .mark svg { height: 28px; }
    header.brand .t { font-size: 15px; }
    header.brand .s { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    header.brand .t .ver, a.src, .sync, .market .long { display: none; }
    .market { padding: 5px 10px; font-size: 11px; }

    .toolbar { display: grid; grid-template-columns: 1fr auto; gap: 8px; }
    .chips { grid-column: 1 / -1; display: grid; grid-template-columns: repeat(3, 1fr); }
    .tg { padding: 9px 6px; font-size: 11.5px; gap: 6px; }
    /* Buy/Sell and Compare share a row; the ranges get a full one below. */
    #side { order: 1; justify-self: start; }
    .switch { order: 2; justify-self: end; }
    .toolbar { grid-template-columns: auto 1fr auto; }
    #live { justify-self: end; }
    .switch { gap: 7px; font-size: 12px; padding: 6px 2px; }
    #ranges { order: 3; grid-column: 1 / -1; display: flex; }
    #ranges button { flex: 1; padding: 10px 4px; }

    .hero { gap: 10px 14px; }
    select.cur { font-size: 16px; padding: 10px 34px 10px 12px; }
    .curpick::after { right: 13px; }
    .price { font-size: 31px; letter-spacing: -.8px; }
    .chg { font-size: 12px; }

    .chart { height: 44vh; min-height: 270px; }
    /* Stacked on a phone, so both charts have to fit a thumb-scroll apart. */
    .panels.split .chart { height: 36vh; min-height: 230px; }
    .tip { min-width: 168px; padding: 9px 10px; }

    .tiles {
      display: flex; overflow-x: auto; scroll-snap-type: x mandatory; scrollbar-width: none;
      margin: 0 calc(-1 * var(--pad)); padding: 2px var(--pad) 4px; scroll-padding: 0 var(--pad);
    }
    .tiles::-webkit-scrollbar { display: none; }
    .tile { flex: 0 0 80%; scroll-snap-align: start; }
    .panels.split .tile dl { grid-template-columns: 1fr 1fr; }
  }

  @media (max-width: 380px) {
    .tg span:last-child { font-size: 11px; }
    .price { font-size: 26px; }
  }

  @media (max-height: 480px) and (orientation: landscape) {
    .chart { height: 62vh; min-height: 200px; }
    .panels.split .chart { height: 58vh; min-height: 180px; }
  }

  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation: none !important; transition: none !important; }
  }
</style>
</head>
<body>
<div class="wrap">

  <header class="brand rise" style="--i:0">
    <span class="logo"><!--__LOGO__--></span>
    <div class="who">
      <div class="t">BCA Forex Rates <span class="ver">v<!--__VERSION__--></span></div>
      <div class="s">Kurs history &middot; Bank Central Asia</div>
    </div>
    <div class="status">
      <span class="sync" id="sync"><span class="ring"></span><span id="ago"></span></span>
      <span class="market" id="market" hidden></span>
      <a class="src" href="https://www.bca.co.id/id/informasi/kurs" target="_blank" rel="noopener noreferrer">Source &#8599;</a>
    </div>
  </header>

  <section class="toolbar card rise" style="--i:1">
    <div class="chips" id="toggles"></div>
    <div class="grow"></div>
    <div class="seg" id="side" role="group" aria-label="Headline side"><span class="thumb"></span></div>
    <div class="seg" id="ranges" role="group" aria-label="Range"><span class="thumb"></span></div>
    <button class="switch" id="live" aria-pressed="true" title="Hide nights and weekends: trading hours only, joined end to end"><span class="track"><span class="knob"></span></span>Market hours</button>
    <button class="switch" id="split" aria-pressed="false"><span class="track"><span class="knob"></span></span>Compare</button>
  </section>

  <div class="panels" id="panels"></div>

  <footer class="rise" style="--i:3">
    <span id="offline" hidden>Offline — showing last synced data</span>
    <span id="cov"></span>
    <span id="stamp"></span>
    <span id="fails"></span>
    <span>Times are WIB (UTC+7). Buy = solid, sell = dashed, shaded band = spread. Tap a rate card to headline it.</span>
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
const NAMES = {
  AED: "UAE Dirham", AUD: "Australian Dollar", CAD: "Canadian Dollar", CHF: "Swiss Franc",
  CNH: "Chinese Yuan (offshore)", DKK: "Danish Krone", EUR: "Euro", GBP: "British Pound",
  HKD: "Hong Kong Dollar", JPY: "Japanese Yen", KRW: "South Korean Won", MYR: "Malaysian Ringgit",
  NZD: "New Zealand Dollar", SAR: "Saudi Riyal", SEK: "Swedish Krona", SGD: "Singapore Dollar",
  THB: "Thai Baht", USD: "US Dollar",
};

const nf = new Intl.NumberFormat("id-ID", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmt = v => (v == null || !isFinite(v)) ? "—" : nf.format(v);
const signed = v => (v > 0 ? "+" : "") + fmt(v);
const arrow = d => d > 0 ? "▲" : d < 0 ? "▼" : "•";

// Motion is decoration. Anyone who has asked their OS for less of it gets
// none: no reveal, no count-up, no pulse (the CSS side switches off too).
const CALM = matchMedia("(prefers-reduced-motion: reduce)").matches;
const REVEAL_MS = 750;

// Count a number up or down to its new value instead of swapping it, so a
// change of currency or range reads as movement rather than a flicker.
function tweenNumber(node, to) {
  const from = parseFloat(node.dataset.v);
  node.dataset.v = to == null ? "" : to;
  cancelAnimationFrame(node._raf);
  if (to == null || CALM || !isFinite(from) || from === to) { node.textContent = fmt(to); return; }
  const t0 = performance.now(), D = 520;
  const step = now => {
    const k = Math.min(1, (now - t0) / D), e = 1 - Math.pow(1 - k, 3);
    node.textContent = fmt(from + (to - from) * e);
    if (k < 1) node._raf = requestAnimationFrame(step);
  };
  node._raf = requestAnimationFrame(step);
}

// The sliding thumb behind a segmented control's chosen button.
function slideThumb(seg) {
  const thumb = seg.querySelector(".thumb"), on = seg.querySelector("button.on");
  if (!on) { thumb.style.opacity = 0; return; }
  thumb.style.opacity = 1;
  thumb.style.width = on.offsetWidth + "px";
  thumb.style.transform = `translateX(${on.offsetLeft}px)`;
  // Placed without a transition the first time, so the page does not open
  // with every thumb flying in from the left edge.
  if (!seg.classList.contains("ready")) requestAnimationFrame(() => seg.classList.add("ready"));
}

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
  fresh: false,      // true for the one render after a live refresh brought new data
  live: true,        // market hours only: closed time cut out of the x-axis
  span: Infinity,    // seconds of history in view (Infinity = everything)...
  end: null,         // ...ending here, or at the newest scrape when null
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

/* ---------- the time window ---------------------------------------------- *
 *
 * The view is one span of TIME shared by every panel, so two currencies side
 * by side always show the same moments. A range button sets the span and
 * anchors the end to the newest scrape; a drag slides the end back through
 * history; a pinch changes the span. `end` stays null while anchored, so a
 * live refresh keeps a default view following new data.
 */
const MIN_SPAN = 30 * 60;   // zoomed all the way in: half an hour

/* With "Market hours" on, the x-axis runs on trading time: weekdays
 * OPEN_MIN-CLOSE_MIN WIB joined end to end, nights and weekends cut out, so
 * Friday's close sits right beside Monday's open instead of across a 64-hour
 * gap. Readings taken while closed are left out. Everything below works in
 * "axis units" — seconds of real time, or seconds of trading time — and only
 * the conversions here know which.
 */
const SESSION = (CLOSE_MIN - OPEN_MIN) * 60;   // trading seconds in a weekday
const WIB_OFF = 7 * 3600;
const dowOf = d => (d + 4) % 7;                 // day 0 (1 Jan 1970) was a Thursday
const isWeekday = d => { const w = dowOf(d); return w > 0 && w < 6; };

// Trading seconds from 1 Jan 1970 up to ts.
function tradingTime(ts) {
  const w = ts + WIB_OFF, d = Math.floor(w / 86400), into = w - d * 86400;
  let days = Math.floor(d / 7) * 5;
  for (let k = d - d % 7; k < d; k++) if (isWeekday(k)) days++;
  const today = isWeekday(d) ? Math.min(SESSION, Math.max(0, into - OPEN_MIN * 60)) : 0;
  return days * SESSION + today;
}
// The real moment at trading time u (the inverse, landing inside a session).
function realTime(u) {
  let n = Math.floor(u / SESSION);
  const rem = u - n * SESSION;
  let d = Math.floor(n / 5) * 7;
  for (n %= 5; ; d++) if (isWeekday(d) && n-- === 0) break;
  return d * 86400 - WIB_OFF + OPEN_MIN * 60 + rem;
}
const ax = ts => state.live ? tradingTime(ts) : ts;

// The indices a currency shows: all of them, or only those read in trading
// hours. Cached per data object, so a live refresh rebuilds it.
const openCache = new WeakMap();
function shownIdx(rec) {
  if (!state.live) return null;
  let v = openCache.get(rec);
  if (!v) {
    v = [];
    for (let i = 0; i < rec.t.length; i++) if (!isClosed(rec.t[i])) v.push(i);
    openCache.set(rec, v);
  }
  return v;
}
// Index list -> axis value of its k-th entry, either way round.
function axisAt(rec, list, k) { return ax(rec.t[list ? list[k] : k]); }
function shownCount(rec, list) { return list ? list.length : rec.t.length; }

function dataBounds() {
  let lo = Infinity, hi = -Infinity;
  for (const c of shownCurrencies()) {
    const rec = DATA.data[c];
    if (!rec) continue;
    const list = shownIdx(rec), n = shownCount(rec, list);
    if (n) { lo = Math.min(lo, axisAt(rec, list, 0)); hi = Math.max(hi, axisAt(rec, list, n - 1)); }
  }
  return isFinite(lo) ? [lo, hi] : [0, 1];
}
// A range button's span (real seconds) in axis units: the trading time inside
// the last N real seconds, so "1W" is still the last week.
function rangeSpan(k) {
  const s = RANGES.find(r => r.k === k).s;
  if (s === Infinity || !state.live) return s;
  let hi = -Infinity;
  for (const c of shownCurrencies()) { const t = (DATA.data[c] || { t: [] }).t; if (t.length) hi = Math.max(hi, t[t.length - 1]); }
  return isFinite(hi) ? tradingTime(hi) - tradingTime(hi - s) : s;
}
function clampSpan(span) {
  const [lo, hi] = dataBounds(), full = Math.max(1, hi - lo);
  return Math.min(full, Math.max(Math.min(MIN_SPAN, full), span));
}
function viewWindow() {
  const [lo, hi] = dataBounds(), span = clampSpan(state.span);
  const end = state.end == null ? hi : Math.min(hi, Math.max(lo + span, state.end));
  return [end - span, end];
}
function setView(span, end) {
  const hi = dataBounds()[1];
  state.span = clampSpan(span);
  state.end = end >= hi - 1 ? null : end;   // back at the newest scrape: anchor there
}
function canPan() {
  const [lo, hi] = dataBounds();
  return clampSpan(state.span) < hi - lo;
}
// First position whose axis value is >= x.
function lowerBound(rec, list, x) {
  let a = 0, b = shownCount(rec, list);
  while (a < b) { const m = (a + b) >> 1; if (axisAt(rec, list, m) < x) a = m + 1; else b = m; }
  return a;
}
// Market-hours axis ticks: times within sessions when zoomed in, else the
// start of each trading day, thinned to fit. Returns real timestamps.
function sessionTicks(u0, u1, want) {
  const out = [], span = u1 - u0;
  if (span <= 2 * SESSION) {
    const step = [900, 1800, 3600, 7200, 10800].find(s => span / s <= want) || 10800;
    for (let ts = Math.ceil((realTime(u0) + WIB_OFF) / step) * step - WIB_OFF; ts <= realTime(u1); ts += step)
      if (!isClosed(ts)) out.push(ts);
    return out;
  }
  const first = Math.ceil(u0 / SESSION), last = Math.floor(u1 / SESSION);
  const stride = Math.max(1, Math.ceil((last - first + 1) / want));
  for (let n = first; n <= last; n += stride) out.push(realTime(n * SESSION));
  return out;
}
// The closed stretches (weekday nights, whole weekends) inside [t0, t1].
function closedSpans(t0, t1) {
  const out = [], DAY = 86400, off = 7 * 3600;
  for (let m = Math.floor((t0 + off) / DAY) * DAY - off; m < t1; m += DAY) {
    const day = new Date((m + off) * 1000).getUTCDay();
    const parts = day === 0 || day === 6 ? [[m, m + DAY]]
      : [[m, m + OPEN_MIN * 60], [m + CLOSE_MIN * 60, m + DAY]];
    for (let [a, b] of parts) {
      a = Math.max(a, t0); b = Math.min(b, t1);
      if (b <= a) continue;
      const last = out[out.length - 1];
      if (last && last[1] >= a) last[1] = b; else out.push([a, b]);
    }
  }
  return out;
}
// Axis ticks on round WIB boundaries (whole hours, days, months), not on
// whichever scrape happens to sit nearby.
const TICK_STEPS = [300, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 172800, 604800, 1209600];
function timeTicks(t0, t1, want) {
  const span = t1 - t0, out = [], off = 7 * 3600;
  // Past two days the labels are dates, so ticks fall on whole days only.
  const step = TICK_STEPS.find(s => (span <= 2 * 86400 || s >= 86400) && span / s <= want);
  if (step) {
    for (let ts = Math.ceil((t0 + off) / step) * step - off; ts <= t1; ts += step) out.push(ts);
    return out;
  }
  const every = Math.max(1, Math.ceil(span / (30 * 86400) / want));
  let { y, mo } = wibParts(t0);
  for (mo++; ; mo++) {
    if (mo > 11) { mo -= 12; y++; }
    const ts = Date.UTC(y, mo, 1) / 1000 - off;
    if (ts > t1) break;
    if (mo % every === 0) out.push(ts);
  }
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
  <div class="hero">
    <label class="curpick"><select class="cur" aria-label="Currency"></select></label>
    <div class="q">
      <div class="eyebrow"></div>
      <div class="quote"><span class="price">—</span><span class="chg"></span></div>
      <div class="sub"></div>
    </div>
  </div>
  <div class="chartbox">
    <svg class="chart"></svg>
    <div class="tip"></div>
    <div class="notice" hidden></div>
  </div>
  <div class="tiles"></div>`;

let panelSeq = 0;

function makePanel(code) {
  const root = document.createElement("section");
  root.className = "panel card rise";
  root.style.setProperty("--i", 2);
  root.innerHTML = PANEL_HTML;
  const q = s => root.querySelector(s);
  const svg = q(".chart"), tip = q(".tip"), notice = q(".notice"), sel = q("select.cur");
  const priceEl = q(".price"), chgEl = q(".chg"), subEl = q(".sub"), eyebrowEl = q(".eyebrow");
  const tilesEl = q(".tiles");
  const uid = "p" + (++panelSeq);          // ids for this panel's SVG gradients and clip
  let cur = code, view = null;
  let revealAt = 0, tilesKey = "";

  // Wipe the lines in from the left. Timed rather than one-shot: a render
  // landing mid-wipe (the second panel, a hover) carries it on from where it
  // was instead of snapping it to the end.
  function reveal() { revealAt = performance.now(); }
  reveal();

  sel.innerHTML = DATA.currencies.map(c => `<option>${c}</option>`).join("");
  sel.value = cur;
  sel.addEventListener("change", () => {
    cur = sel.value; reveal(); syncControls(); renderAll(); savePrefs();
  });

  // A rate card puts its series in the headline, on whichever side is chosen.
  tilesEl.addEventListener("click", e => {
    const b = e.target.closest(".tile[data-k]");
    if (b) setMetric(b.dataset.k, state.metric.side);
  });

  // Indices inside the time window ("core"), and the same plus one either side
  // ("all"), so the lines run on to the edges of the plot instead of stopping
  // short at the last point inside it.
  function idxWindow() {
    const rec = DATA.data[cur] || { t: [] }, list = shownIdx(rec), n = shownCount(rec, list);
    const at = k => list ? list[k] : k;
    const [t0, t1] = viewWindow();
    const a = lowerBound(rec, list, t0), b = lowerBound(rec, list, t1 + 1e-6) - 1, core = [];
    for (let k = a; k <= b; k++) core.push(at(k));
    const all = core.slice();
    if (a > 0) all.unshift(at(a - 1));
    if (b + 1 < n) all.push(at(b + 1));
    return { core, all, t0, t1 };
  }

  // Hover is stored as a timestamp, not an index, so the second panel lands on
  // the same moment even if it were ever sampled differently.
  function hoverIndex() {
    if (state.hoverT == null) return -1;
    const t = (DATA.data[cur] || { t: [] }).t;
    let best = -1, bd = Infinity;
    for (const i of idxWindow().core) {
      const d = Math.abs(t[i] - state.hoverT);
      if (d < bd) { bd = d; best = i; }
    }
    return best;
  }

  function render() {
    // Clear any leftover pan translate; panTo re-applies it right after when a
    // drag is mid-flight, and every other caller wants a clean, un-shifted plot.
    svg.style.transform = "";
    const rec = DATA.data[cur];
    const win = idxWindow();
    // Zoomed into a gap with nothing inside, the neighbours still have values.
    const idx = win.core.length ? win.core : win.all, wide = win.all;
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
      renderQuote([]); renderTiles([]);
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
    // To scale: x is elapsed time, so scrapes close together sit close together
    // and a night or a weekend takes the room it really took.
    const tx = ts => M.l + (ts - win.t0) / ((win.t1 - win.t0) || 1) * iw;
    const xOf = i => tx(ax(t[i]));
    const yOf = v => M.t + (1 - (v - lo) / (hi - lo)) * ih;

    const parts = [];

    // A top-to-bottom fade per series for the spread band, and the clip that
    // wipes the lines in after a change of currency or range.
    const since = performance.now() - revealAt;
    const wiping = !CALM && since < REVEAL_MS;
    parts.push(`<defs>${series.map(s => `<linearGradient id="${uid}-g-${s.key}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="${COLORS[s.key]}" stop-opacity=".2"/>
        <stop offset="1" stop-color="${COLORS[s.key]}" stop-opacity=".03"/></linearGradient>`).join("")}
      <clipPath id="${uid}-clip"><rect x="${M.l}" y="${M.t}" width="${wiping ? 0 : iw}" height="${ih}">${wiping
        ? `<animate class="rv" attributeName="width" from="0" to="${iw}" dur="${REVEAL_MS}ms" begin="indefinite" fill="freeze" calcMode="spline" keyTimes="0;1" keySplines=".22 1 .36 1"/>`
        : ""}</rect></clipPath></defs>`);

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
      parts.push(`<line x1="${M.l}" y1="${y.toFixed(1)}" x2="${M.l + iw}" y2="${y.toFixed(1)}" stroke="#94a3b8" stroke-opacity=".08"/>`);
      // Drop the axis number when a value flag already occupies that row.
      if (!flags.some(f => Math.abs(f.y - y) < 9))
        parts.push(`<text x="${M.l + iw + 8}" y="${(y + 3.5).toFixed(1)}">${fmt(v)}</text>`);
    }

    // Shade the closed hours (nights, weekends) once zoomed in to a few days,
    // so the trading day reads at a glance. Further out it would be all grey.
    if (!state.live && win.t1 - win.t0 <= 3 * 86400) {
      let labelled = false;
      for (const [a, b] of closedSpans(win.t0, win.t1)) {
        const x1 = tx(a), x2 = tx(b);
        parts.push(`<rect x="${x1.toFixed(1)}" y="${M.t}" width="${(x2 - x1).toFixed(1)}" height="${ih}" fill="#8b97ab" fill-opacity=".06"/>`);
        if (!labelled && x2 - x1 > 34) {
          parts.push(`<text x="${((x1 + x2) / 2).toFixed(1)}" y="${(M.t + 13).toFixed(1)}" text-anchor="middle" fill="#7a879e" style="font-size:10px;letter-spacing:.06em">Closed</text>`);
          labelled = true;
        }
      }
    }

    const spanSec = win.t1 - win.t0;
    // Under the crosshair's time badge, the axis label would only peek out.
    const hv0 = hoverIndex(), hx = hv0 >= 0 && idx.includes(hv0) ? xOf(hv0) : null;
    // Market hours joins the days end to end, so mark every join: a faint
    // dashed line where a night was cut out, a stronger one, labelled, where a
    // weekend was. Overnight lines thin out once days get narrower than a few
    // pixels; weekend lines always show.
    if (state.live) {
      const perDay = iw * SESSION / ((win.t1 - win.t0) || 1);
      for (let n = Math.ceil(win.t0 / SESSION); n * SESSION <= win.t1; n++) {
        const x = tx(n * SESSION);
        if (x <= M.l + 1) continue;
        const weekend = new Date((realTime(n * SESSION) + WIB_OFF) * 1000).getUTCDay() === 1;
        if (!weekend && perDay < 8) continue;
        parts.push(`<line x1="${x.toFixed(1)}" y1="${M.t}" x2="${x.toFixed(1)}" y2="${M.t + ih}" stroke="#94a3b8" ${
          weekend ? `stroke-opacity=".32" stroke-width="1.2"` : `stroke-opacity=".14" stroke-dasharray="2 4"`}/>`);
        if (weekend && perDay >= 50)
          parts.push(`<text x="${(x + 4).toFixed(1)}" y="${(M.t + 11).toFixed(1)}" fill="#7a879e" style="font-size:9.5px;letter-spacing:.05em">Weekend</text>`);
      }
    }
    const want = Math.max(4, Math.floor(iw / 70));
    const ticks = state.live ? sessionTicks(win.t0, win.t1, want) : timeTicks(win.t0, win.t1, want);
    // Market hours, zoomed in: a day's first tick (its open) gets the date.
    const opening = ts => { const p = wibParts(ts); return p.h * 60 + p.mi === OPEN_MIN; };
    for (const ts of ticks) {
      const x = tx(ax(ts));
      if (hx != null && Math.abs(x - hx) < 80) continue;
      const p = wibParts(ts), midnight = p.h === 0 && p.mi === 0;
      const txt = state.live
        ? (spanSec <= 2 * SESSION && !opening(ts) ? fmtTime(ts) : fmtDay(ts))
        : (spanSec <= 2 * 86400 && !midnight ? fmtTime(ts) : fmtDay(ts));
      const anchor = x < M.l + 26 ? "start" : x > M.l + iw - 26 ? "end" : "middle";
      parts.push(`<line x1="${x.toFixed(1)}" y1="${M.t + ih}" x2="${x.toFixed(1)}" y2="${M.t + ih + 4}" stroke="#2a3345"/>`);
      parts.push(`<text x="${x.toFixed(1)}" y="${M.t + ih + 17}" text-anchor="${anchor}">${txt}</text>`);
    }

    // On weekdays the scraper never spaces reads more than an hour apart —
    // hourly even when closed — so any jump past ~90 min is a genuine outage
    // (the process was down), not the normal overnight cadence. That is the
    // only thing that breaks a line; closed hours have data and draw straight
    // through. Weekends are read every 3 h, so a gap that starts on a Saturday or Sunday
    // (Sun 21:00 -> Mon 00:00 included) is allowed 3.5 h before it counts.
    const OUTAGE = 90 * 60, WEEKEND_OUTAGE = 210 * 60;
    const isOutage = (a, b) => {
      if (state.live) return ax(b) - ax(a) > OUTAGE;
      const day = new Date((a + 7 * 3600) * 1000).getUTCDay();
      return b - a > (day === 0 || day === 6 ? WEEKEND_OUTAGE : OUTAGE);
    };

    const GAP = "#6b7890";  // grey for the bridge across a gap — clearly not a rate line
    parts.push(`<g clip-path="url(#${uid}-clip)">`);
    // The headline series first in the eye: drawn last and at full strength,
    // the others stepped back. Three bands at equal weight over a long range
    // read as one tangle.
    const ordered = series.filter(s => s.key !== state.metric.key)
      .concat(series.filter(s => s.key === state.metric.key));
    for (const s of ordered) {
      const [buy, sell] = rec[s.key], c = COLORS[s.key];
      const lead = s.key === state.metric.key || series.length === 1;
      parts.push(`<g opacity="${lead ? 1 : 0.42}">`);

      // Break the series into contiguous runs — a run ends on a null or a real
      // outage — then draw each, and bridge the breaks separately.
      const runs = [];
      let run = [];
      for (const i of wide) {
        if (buy[i] == null || sell[i] == null) { if (run.length) runs.push(run), run = []; continue; }
        if (run.length && isOutage(t[run[run.length - 1]], t[i])) runs.push(run), run = [];
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
          parts.push(`<path d="M${up.join("L")}L${dn.join("L")}Z" fill="url(#${uid}-g-${s.key})" fill-opacity="${lead ? 1 : .45}"/>`);
          parts.push(`<path d="M${up.join("L")}" fill="none" stroke="${c}" stroke-width="${lead ? 1.9 : 1.4}" stroke-linejoin="round" stroke-linecap="round"/>`);
          parts.push(`<path d="M${run.map(i => `${xOf(i).toFixed(1)},${yOf(sell[i]).toFixed(1)}`).join("L")}" fill="none" stroke="${c}" stroke-width="${lead ? 1.6 : 1.2}" stroke-dasharray="5 3.5" stroke-linecap="round"/>`);
        }
        // No per-point dots — on a dense line they are just clutter. The one
        // exception is a lone sample between gaps: it has no line to sit on, so
        // without a mark it would vanish entirely. Keep a dot only for those.
        if (run.length === 1) {
          const i = run[0];
          parts.push(`<circle cx="${xOf(i).toFixed(1)}" cy="${yOf(buy[i]).toFixed(1)}" r="2.6" fill="${c}"/>`);
          parts.push(`<circle cx="${xOf(i).toFixed(1)}" cy="${yOf(sell[i]).toFixed(1)}" r="2.6" fill="${c}" fill-opacity=".45" stroke="${c}" stroke-width="1"/>`);
        }
      }
      parts.push(`</g>`);
    }
    parts.push(`</g>`);

    // A live dot on the newest headline reading, pulsing, while the view is
    // anchored to now. Panned back into history it would be a lie.
    const hm0 = state.metric;
    if (state.end == null && state.on[hm0.key] && rec[hm0.key]) {
      const arm = rec[hm0.key][hm0.side];
      const last = [...idx].reverse().find(i => arm[i] != null);
      if (last != null && last === t.length - 1) {
        const x = xOf(last).toFixed(1), y = yOf(arm[last]).toFixed(1), c = COLORS[hm0.key];
        if (!CALM) parts.push(`<circle cx="${x}" cy="${y}" r="4" fill="none" stroke="${c}" stroke-width="1.5">
          <animate attributeName="r" values="4;13" dur="2s" repeatCount="indefinite"/>
          <animate attributeName="stroke-opacity" values=".8;0" dur="2s" repeatCount="indefinite"/></circle>`);
        parts.push(`<circle cx="${x}" cy="${y}" r="3.6" fill="${c}" stroke="#06080d" stroke-width="1.5"/>`);
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
          if (arm[hv] != null) parts.push(`<circle cx="${x.toFixed(1)}" cy="${yOf(arm[hv]).toFixed(1)}" r="4" fill="${COLORS[s.key]}" stroke="#06080d" stroke-width="1.5"/>`);
        }
      }
      // Read-outs on both axes, the way a trading chart pins the crosshair:
      // the headline value on the right edge, the time along the bottom.
      const hk = rec[state.metric.key], hval = hk && state.on[state.metric.key] ? hk[state.metric.side][hv] : null;
      if (hval != null) {
        const y = yOf(hval), c = COLORS[state.metric.key];
        parts.push(`<line x1="${M.l}" y1="${y.toFixed(1)}" x2="${M.l + iw}" y2="${y.toFixed(1)}" stroke="${c}" stroke-opacity=".45" stroke-dasharray="3 3"/>`);
        parts.push(`<rect x="${M.l + iw + 2}" y="${(y - 9).toFixed(1)}" width="${M.r - 4}" height="18" rx="5" fill="${c}"/>`);
        parts.push(`<text x="${M.l + iw + 7}" y="${(y + 3.8).toFixed(1)}" fill="#06080d" style="font-weight:750">${fmt(hval)}</text>`);
      }
      const when = (spanSec > 86400 * 2 ? fmtDay(t[hv]) + " " : "") + fmtTime(t[hv]);
      const tw = when.length * 6.2 + 16, tx = Math.max(M.l, Math.min(M.l + iw - tw, x - tw / 2));
      parts.push(`<rect x="${tx.toFixed(1)}" y="${M.t + ih + 4}" width="${tw.toFixed(1)}" height="18" rx="5" fill="#1c2433" stroke="#94a3b8" stroke-opacity=".25"/>`);
      parts.push(`<text x="${(tx + tw / 2).toFixed(1)}" y="${M.t + ih + 16.5}" text-anchor="middle" fill="#e8eef8" style="font-weight:650">${when}</text>`);
    }

    svg.innerHTML = parts.join("");
    const rv = wiping && svg.querySelector("animate.rv");
    if (rv) { try { rv.beginElementAt(-since / 1000); } catch (_) {} }
    view = { idx, xOf, iw, l: M.l };

    const n = t.length;
    notice.hidden = n > 3;
    if (!notice.hidden) {
      notice.textContent = n === 1
        ? "Only one scrape stored — there is no line to draw yet."
        : `Only ${n} scrapes stored so far — the shape will fill in as history builds.`;
    }

    renderQuote(idx);
    renderTiles(idx);
  }

  function renderQuote(idx) {
    const rec = DATA.data[cur];
    const m = state.metric, meta = DATA.series.find(s => s.key === m.key);
    const arms = rec && rec[m.key];
    eyebrowEl.textContent = (NAMES[cur] || cur) + " · " + meta.label + " " + SIDES[m.side].toLowerCase();
    root.style.setProperty("--glow", COLORS[m.key]);
    if (!arms || !idx.length) {
      tweenNumber(priceEl, null);
      chgEl.textContent = ""; chgEl.className = "chg";
      subEl.innerHTML = arms ? "" : `<b>${meta.label}</b> not offered for ${cur}`;
      return;
    }
    const pick = arms[m.side], other = arms[1 - m.side];
    const last = [...idx].reverse().find(i => pick[i] != null);
    const first = idx.find(i => pick[i] != null);
    const was = parseFloat(priceEl.dataset.v), now = last == null ? null : pick[last];
    tweenNumber(priceEl, now);
    // New data from the live refresh flashes the price green or red once.
    if (state.fresh && now != null && isFinite(was) && was !== now) {
      priceEl.classList.remove("flash-up", "flash-down");
      void priceEl.offsetWidth;   // restart the animation
      priceEl.classList.add(now > was ? "flash-up" : "flash-down");
    }

    if (last != null && first != null && first !== last && pick[first]) {
      const d = pick[last] - pick[first], pct = (d / pick[first]) * 100;
      chgEl.textContent = arrow(d) + " " + signed(d) + " (" + (d > 0 ? "+" : "") + pct.toFixed(2) + "%)";
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

  // One card per series: the chosen side's latest value, its change and a
  // sparkline over the range, then buy, sell, spread, high and low. Every
  // series gets a card, switched off or not offered included, so the row
  // never reshuffles. Hover re-renders the chart constantly; the cards only
  // change with the inputs below, so they skip those renders.
  function renderTiles(idx) {
    const rec = DATA.data[cur], side = state.metric.side;
    const key = [cur, idx[0], idx[idx.length - 1], idx.length, state.metric.key, side,
                 DATA.series.map(s => +!!state.on[s.key]).join(""), newestStamp(DATA)].join("|");
    if (key === tilesKey) return;
    tilesKey = key;
    tilesEl.innerHTML = DATA.series.map(s => {
      const c = COLORS[s.key], arms = rec && rec[s.key];
      if (!arms) {
        return `<div class="tile na" style="--c:${c}"><div class="th"><span class="dot"></span>${s.label}</div>
          <div class="na-msg">Not offered for ${cur}</div></div>`;
      }
      const [buy, sell] = arms, track = arms[side];   // value, change, high, low follow the chosen side
      const pts = idx.filter(i => track[i] != null);
      const last = pts[pts.length - 1], first = pts[0];
      const vals = pts.map(i => track[i]);
      const hi = vals.length ? Math.max(...vals) : null, lo = vals.length ? Math.min(...vals) : null;
      const b = last == null ? null : buy[last], sv = last == null ? null : sell[last];
      const sp = (b != null && sv != null) ? sv - b : null;
      let chg = "—", cls = "flat";
      if (last != null && first != null && first !== last && track[first]) {
        const d = track[last] - track[first];
        chg = arrow(d) + " " + signed(d) + " (" + (d > 0 ? "+" : "") + ((d / track[first]) * 100).toFixed(2) + "%)";
        cls = d > 0 ? "up" : d < 0 ? "down" : "flat";
      }
      // ~100 points is plenty at card width; striding keeps a year of data cheap.
      let spark = "";
      if (vals.length > 1) {
        const stride = Math.ceil(vals.length / 100), sv2 = vals.filter((_, k) => k % stride === 0 || k === vals.length - 1);
        const span = (hi - lo) || 1, n = sv2.length - 1;
        const xy = sv2.map((v, k) => `${(k / n * 100).toFixed(2)},${(28 - (v - lo) / span * 26).toFixed(2)}`);
        spark = `<svg class="spark" viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true">
          <path d="M0,30L${xy.join("L")}L100,30Z" fill="${c}" fill-opacity=".1"/>
          <path d="M${xy.join("L")}" fill="none" stroke="${c}" stroke-width="1.6" stroke-linejoin="round" vector-effect="non-scaling-stroke"/></svg>`;
      }
      const head = s.key === state.metric.key;
      return `<button class="tile${head ? " on" : ""}${state.on[s.key] ? "" : " off"}" data-k="${s.key}" style="--c:${c}"
                aria-pressed="${head}" title="Headline ${s.label} ${SIDES[side].toLowerCase()}">
        <div class="th"><span class="dot"></span>${s.label}<span class="tag">Headline</span></div>
        <div class="tv">${fmt(last == null ? null : track[last])}<small>${SIDES[side].toLowerCase()}</small></div>
        <div class="tc ${cls}">${chg}</div>
        ${spark}
        <dl>
          <div><dt>Buy</dt><dd>${fmt(b)}</dd></div><div><dt>Sell</dt><dd>${fmt(sv)}</dd></div>
          <div><dt>Spread</dt><dd>${fmt(sp)}</dd></div>
          <div><dt>Spread %</dt><dd>${sp == null || !b ? "—" : ((sp / b) * 100).toFixed(3) + "%"}</dd></div>
          <div><dt>High</dt><dd>${fmt(hi)}</dd></div><div><dt>Low</dt><dd>${fmt(lo)}</dd></div>
        </dl>
      </button>`;
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

  /* Touch gestures share the chart, and the job is telling them apart without
   * stealing the page's own vertical scroll:
   *
   *   drag sideways   -> pan the time window back and forth through history
   *   pinch           -> zoom: fingers apart shows less time, together more
   *   press and hold  -> scrub, showing the crosshair and detail tooltip
   *   drag upwards    -> left to the browser, so the page still scrolls
   *
   * `mode` latches to the first gesture to qualify and nothing else fires
   * until the fingers lift, so a pan can never trip the hold-to-scrub or vice
   * versa. A second finger always wins: whatever the first one started turns
   * into a pinch. With a mouse, hover scrubs, drag pans, and ctrl+wheel zooms
   * (a trackpad pinch arrives as exactly that).
   */
  const HOLD_MS = 260, SLOP = 10;
  let mode = null;              // null | "pan" | "scrub" | "pinch" | "done"
  let holdTimer = null, pid = null, sx = 0, sy = 0;
  let grab = null;              // the window as it was when a pan or pinch began
  const touches = new Map();    // pointerId -> clientX of every finger down

  // 0..1 across the plot area, for keeping a moment under the fingers.
  function plotFrac(clientX) {
    if (!view) return 0.5;
    return (clientX - svg.getBoundingClientRect().left - view.l) / view.iw;
  }
  // Pointer events can outrun the screen; draw at most once a frame.
  let frame = 0;
  function redraw() {
    if (!frame) frame = requestAnimationFrame(() => { frame = 0; renderAll(); });
  }
  function grabWindow() {
    const [t0, t1] = viewWindow();
    grab = { t0, t1, span: t1 - t0 };
  }

  // Drag right -> older data slides in from the left, the way pulling the
  // paper would move it. Continuous: time is a real number, so there is no
  // snapping to whole points.
  function panTo(clientX) {
    if (!grab || !view) return;
    setView(grab.span, grab.t1 - (clientX - sx) / view.iw * grab.span);
    redraw();
  }

  // Show `span` seconds, with the moment `at` sitting `f` of the way across.
  function zoomAround(span, at, f) {
    const s = clampSpan(span);
    state.range = null; markRanges();      // a pinched span matches no button
    setView(s, at - f * s + s);
    redraw();
  }

  function fingers() {
    const [a, b] = [...touches.values()];
    return { d: Math.max(40, Math.abs(a - b)), mid: (a + b) / 2 };
  }
  function beginPinch() {
    clearTimeout(holdTimer);
    if (mode === "scrub") { state.hoverT = null; state.tipOwner = null; panels.forEach(p => p.hideTip()); }
    hideTip();
    mode = "pinch";
    svg.classList.add("scrubbing");
    grabWindow();
    const { d, mid } = fingers();
    grab.d = d;
    grab.at = grab.t0 + plotFrac(mid) * grab.span;   // the moment between the fingers
  }
  // Spread the fingers to see less time, closer for more; the moment that
  // started between them stays between them, wherever they move.
  function pinchTo() {
    const { d, mid } = fingers();
    zoomAround(grab.span * grab.d / d, grab.at, plotFrac(mid));
  }

  function beginPan() {
    clearTimeout(holdTimer);
    mode = "pan"; grabWindow();
    state.hoverT = null; hideTip();
    // Capture so the finger can wander off the chart and the pan still tracks.
    try { svg.setPointerCapture(pid); } catch (_) {}
  }

  function beginScrub(clientX) {
    clearTimeout(holdTimer);
    mode = "scrub";
    // Capture, so the finger can leave the chart — off the top, onto the rate
    // cards — and the scrub still tracks instead of dying mid-gesture.
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
    svg.classList.remove("scrubbing");
    mode = null; pid = null; grab = null;
    if (wasScrub) clearHover();          // panning and zooming leave the view put
  }

  // A finger lifting. After a pinch, the one left behind does nothing until it
  // lifts too: letting it pan would jolt the view the moment the pinch ends.
  function lift(e) {
    touches.delete(e.pointerId);
    try { svg.releasePointerCapture(e.pointerId); } catch (_) {}
    if (touches.size) { if (mode === "pinch") mode = "done"; return; }
    endGesture();
  }

  svg.addEventListener("pointerdown", e => {
    if (fromMouse(e)) { pid = e.pointerId; sx = e.clientX; mode = null; return; }
    touches.set(e.pointerId, e.clientX);
    if (touches.size === 2) {
      try { svg.setPointerCapture(e.pointerId); } catch (_) {}
      beginPinch();
      return;
    }
    if (touches.size > 2 || mode) return;
    pid = e.pointerId; sx = e.clientX; sy = e.clientY;
    clearTimeout(holdTimer);
    holdTimer = setTimeout(() => { if (pid === e.pointerId && mode === null) beginScrub(sx); }, HOLD_MS);
  });

  svg.addEventListener("pointermove", e => {
    if (fromMouse(e)) {
      if (e.buttons & 1) {                 // button held: drag to pan
        if (mode !== "pan") {
          mode = "pan"; sx = e.clientX; grabWindow(); state.hoverT = null; hideTip();
          try { svg.setPointerCapture(e.pointerId); } catch (_) {}
        }
        panTo(e.clientX);
      } else {
        if (mode === "pan") mode = null;
        moveTo(e.clientX);                 // no button: plain hover scrub
      }
      return;
    }
    if (touches.has(e.pointerId)) touches.set(e.pointerId, e.clientX);
    if (mode === "pinch") { e.preventDefault(); pinchTo(); return; }
    if (e.pointerId !== pid || mode === "done") return;

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

  svg.addEventListener("pointerup", e => {
    if (fromMouse(e)) {
      if (mode) { try { svg.releasePointerCapture(e.pointerId); } catch (_) {} }
      mode = null; pid = null; grab = null;
    } else lift(e);
  });
  svg.addEventListener("pointercancel", e => {
    if (fromMouse(e)) { mode = null; pid = null; grab = null; } else lift(e);
  });
  svg.addEventListener("pointerleave", e => { if (fromMouse(e) && mode !== "pan") clearHover(); });

  // ctrl+wheel, and a trackpad pinch (which the browser sends as one), zoom
  // around the pointer. A plain wheel is left alone so the page still scrolls.
  svg.addEventListener("wheel", e => {
    if (!e.ctrlKey || !view) return;
    e.preventDefault();
    const [t0, t1] = viewWindow(), f = plotFrac(e.clientX);
    zoomAround((t1 - t0) * Math.exp(e.deltaY * 0.01), t0 + f * (t1 - t0), f);
  }, { passive: false });

  // touch-action: pan-y already keeps the browser's own pinch-zoom off the
  // chart; iOS Safari also wants these, or it zooms the whole page instead.
  svg.addEventListener("gesturestart", e => e.preventDefault());
  svg.addEventListener("touchmove", e => { if (e.touches.length > 1) e.preventDefault(); }, { passive: false });

  const api = {
    root, render, hideTip, reveal,
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

// The headline is a series x side pair: the rate cards pick the series, the
// Buy / Sell segment the side, so every one of the six is a tap away.
function setMetric(key, side) {
  const same = state.metric.key === key && state.metric.side === side;
  state.metric = { key, side };
  // headlining a series nobody can see is a dead number, so switch it on
  if (!state.on[key]) state.on[key] = true;
  el("side").querySelectorAll("button").forEach(b => b.classList.toggle("on", +b.dataset.side === side));
  slideThumb(el("side"));
  if (same) return;
  syncControls(); renderAll(); savePrefs();
}

function buildSide() {
  el("side").insertAdjacentHTML("beforeend",
    SIDES.map((label, side) => `<button data-side="${side}" class="${side === state.metric.side ? "on" : ""}">${label}</button>`).join(""));
  el("side").querySelectorAll("button").forEach(b =>
    b.addEventListener("click", () => setMetric(state.metric.key, +b.dataset.side)));
}

// Light up the button for the current span; none after a pinch.
function markRanges() {
  el("ranges").querySelectorAll("button").forEach(x => x.classList.toggle("on", x.dataset.r === state.range));
  slideThumb(el("ranges"));
}

// Built once and then only re-flagged, so the sliding thumb survives a
// change of currency or a live refresh instead of being rebuilt under it.
function buildRanges() {
  el("ranges").insertAdjacentHTML("beforeend",
    RANGES.map(r => `<button data-r="${r.k}" class="${state.range === r.k ? "on" : ""}">${r.k}</button>`).join(""));
  el("ranges").querySelectorAll("button").forEach(b => {
    b.addEventListener("click", () => {
      if (state.range === b.dataset.r) return;
      state.range = b.dataset.r;
      el("ranges").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
      slideThumb(el("ranges"));
      setView(rangeSpan(state.range), Infinity);
      state.hoverT = null; panels.forEach(p => { p.hideTip(); p.reveal(); });
      renderAll(); savePrefs();
    });
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
    return `<label class="tg ${on ? "on" : ""} ${has ? "" : "off-data"}" data-k="${s.key}" style="--c:${COLORS[s.key]}"
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
  el("ranges").querySelectorAll("button").forEach(b => {
    const r = RANGES.find(x => x.k === b.dataset.r);
    b.disabled = !(r.s === Infinity || span >= r.s * 0.15 || r.s <= 86400);
  });
  slideThumb(el("ranges"));

  el("cov").textContent = `${t.length} scrape${t.length === 1 ? "" : "s"} stored` +
    (t.length ? ` · ${fmtFull(t[0])} → ${fmtFull(t[t.length - 1])}` : "");
}

function pick(preferred, avoid) {
  if (DATA.currencies.includes(preferred) && preferred !== avoid) return preferred;
  return DATA.currencies.find(c => c !== avoid) || DATA.currencies[0];
}

// Market hours on or off. The view goes back to the chosen range (ALL after a
// pinch): a zoomed window in one axis means nothing in the other.
function setLive(on, quiet) {
  state.live = on;
  el("live").classList.toggle("on", on);
  el("live").setAttribute("aria-pressed", on);
  if (!state.range) { state.range = "ALL"; markRanges(); }
  setView(rangeSpan(state.range), Infinity);
  if (quiet) return;
  state.hoverT = null; panels.forEach(p => { p.hideTip(); p.reveal(); });
  renderAll(); savePrefs();
}

function setSplit(on) {
  state.split = on;
  const host = el("panels");
  host.classList.toggle("split", on);
  el("split").classList.toggle("on", on);
  el("split").setAttribute("aria-pressed", on);
  panels.forEach(p => p.reveal());

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

// Rows are stored per currency only when that currency changes, so the newest
// one can be in any of them, not necessarily the first.
function newestStamp(d) {
  let hi = 0;
  for (const c of d.currencies) { const t = d.data[c].t; if (t.length) hi = Math.max(hi, t[t.length - 1]); }
  return hi;
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
  state.fresh = true;
  try { renderAll(); } finally { state.fresh = false; }
  return true;
}

// pullFresh with its loading state: the header ring spins while it runs.
async function sync() {
  const s = el("sync");
  s.classList.add("busy"); el("ago").textContent = "Syncing…";
  try { return await pullFresh(); }
  finally { s.classList.remove("busy"); updateAgo(); }
}

function flashUpdated() {
  const cov = el("cov");
  cov.style.color = "var(--up)";
  setTimeout(() => { cov.style.color = ""; }, 2500);
}

// How old the newest scrape is, so a page left open says whether it is live.
function updateAgo() {
  if (el("sync").classList.contains("busy")) return;
  const mins = Math.floor((Date.now() / 1000 - newestStamp(DATA)) / 60);
  el("ago").textContent = "Updated " + (mins < 1 ? "just now" : mins < 60 ? mins + " min ago"
    : mins < 48 * 60 ? Math.floor(mins / 60) + " h ago" : Math.floor(mins / 1440) + " days ago");
}

function scheduleRefresh() {
  setTimeout(async () => {
    // Never yank the ground out from under a finger mid-scrub.
    if (state.hoverT != null) { setTimeout(scheduleRefresh, 15000); return; }
    try {
      if (await sync()) flashUpdated();
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
  if (day >= 1 && day <= 5 && mins < 9 * 60 + 15) return "09:00 WIB";
  let add = 1, wd = (day + 1) % 7;
  while (wd === 0 || wd === 6) { add++; wd = (wd + 1) % 7; }
  return DAY_ABBR[(day + add) % 7] + " 09:00";
}

// Open = BCA is dealing: its e-Rate is the tight quote (DATA.open, measured,
// so a holiday reads closed). Trusted while the data is under 2 h old; else,
// or before there is history to judge, the clock: weekdays 09:00-15:00.
function marketOpen(nowSec) {
  if (typeof DATA.open === "boolean" && nowSec - newestStamp(DATA) < 2 * 3600) return DATA.open;
  const d = new Date((nowSec + 7 * 3600) * 1000);
  const day = d.getUTCDay(), mins = d.getUTCHours() * 60 + d.getUTCMinutes();
  return day >= 1 && day <= 5 && mins >= 9 * 60 && mins < 15 * 60;
}

function updateMarket() {
  const m = el("market");
  const closed = !marketOpen(Date.now() / 1000);
  m.hidden = false;
  m.className = "market " + (closed ? "closed" : "open");
  // The "opens …" half is dropped on a phone, where the header has no room.
  // One wrapping span, so the badge's flex gap does not open up mid-sentence.
  m.innerHTML = closed ? `<span>Closed<span class="long"> · opens ${nextOpenLabel(Date.now() / 1000)}</span></span>` : "Market open";
}

const PREFS_KEY = "bca-forex-view";

function savePrefs() {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify({
      range: state.range || loadPrefs().range, metric: state.metric, split: state.split, live: state.live,
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
  if (typeof saved.live === "boolean") state.live = saved.live;
  if (saved.metric && DATA.series.some(s => s.key === saved.metric.key) &&
      (saved.metric.side === 0 || saved.metric.side === 1)) state.metric = saved.metric;

  buildSide();
  buildRanges();

  const savedCur = saved.currencies || [];
  const c0 = DATA.currencies.includes(savedCur[0]) ? savedCur[0] : pick("USD", null);
  const first = makePanel(c0);
  panels.push(first);
  el("panels").appendChild(first.root);

  el("split").addEventListener("click", () => setSplit(!state.split));
  el("live").addEventListener("click", () => setLive(!state.live));
  setLive(state.live, true);

  if (saved.split) {
    setSplit(true);
    if (DATA.currencies.includes(savedCur[1])) { panels[1].currency = savedCur[1]; savePrefs(); }
  }

  updateMarket(); updateAgo();
  setInterval(() => { updateMarket(); updateAgo(); }, 30000);   // flip open / closed without a reload

  el("stamp").textContent = DATA.pageUpdated ? "· BCA page: " + DATA.pageUpdated : "";
  el("fails").innerHTML = DATA.failedRuns
    ? `· <span class="warn">${DATA.failedRuns} failed scrape${DATA.failedRuns === 1 ? "" : "s"}</span>` : "";

  syncControls();
  slideThumb(el("side"));
  renderAll();
  addEventListener("resize", () => {
    state.hoverT = null; panels.forEach(p => p.hideTip());
    slideThumb(el("side")); slideThumb(el("ranges"));
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
    if (!document.hidden) sync().then(ok => ok && flashUpdated()).catch(() => {});
  });
}
init();
</script>
</body>
</html>
"""
