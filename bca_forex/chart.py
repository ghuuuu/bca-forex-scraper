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


def build(
    conn: sqlite3.Connection,
    out: Path,
    limit: int = 20000,
    currency: str | None = None,
    logo: Path | None = None,
) -> tuple[Path, int, int]:
    """Write the chart. Returns (path, currencies, total points)."""
    payload = collect(conn, limit, currency)
    html = (
        TEMPLATE
        .replace("/*__DATA__*/null", json.dumps(payload, separators=(",", ":")))
        .replace("<!--__LOGO__-->", logo_markup(logo))
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    points = sum(len(v["t"]) for v in payload["data"].values())
    return out, len(payload["currencies"]), points


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>BCA Forex — rate history</title>
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
  .wrap { max-width: 1180px; margin: 0 auto; padding: 14px; display: flex; flex-direction: column; gap: 12px; }

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
  header.brand .s { color: var(--muted); font-size: 11.5px; }
  header.brand a.src {
    flex: none; color: var(--muted); font-size: 11.5px; text-decoration: none;
    border: 1px solid var(--line); border-radius: 999px; padding: 6px 12px; white-space: nowrap;
  }
  header.brand a.src:hover { color: var(--text); border-color: #38455e; }

  .top { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 14px 20px; }
  select.cur {
    background: var(--panel-2); color: var(--text); border: 1px solid var(--line);
    border-radius: 8px; padding: 9px 12px; font: 600 15px/1 inherit; letter-spacing: .04em;
    min-width: 108px; cursor: pointer;
  }
  select.cur:focus { outline: 2px solid #2f6fed; outline-offset: 1px; }
  .quote { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
  .price { font-size: 30px; font-weight: 650; letter-spacing: -.5px; line-height: 1; }
  .chg { font-size: 14px; font-weight: 600; }
  .up { color: var(--up); } .down { color: var(--down); } .flat { color: var(--muted); }
  .sub { color: var(--muted); font-size: 12px; }
  .sub b { color: #b9c4d6; font-weight: 600; }

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

  .chartbox { position: relative; background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 4px; }
  #chart { display: block; width: 100%; height: 62vh; min-height: 340px; max-height: 620px; touch-action: pan-y; }
  #chart text { fill: var(--muted); font-size: 11px; }
  .notice {
    position: absolute; inset: auto 0 0 0; margin: 0 auto 14px; width: max-content; max-width: 92%;
    background: rgba(30,38,54,.94); border: 1px solid var(--line); color: var(--muted);
    padding: 7px 13px; border-radius: 8px; font-size: 12px; text-align: center;
  }
  .tip {
    position: absolute; pointer-events: none; opacity: 0; transition: opacity .09s;
    background: rgba(13,17,26,.97); border: 1px solid #2c3648; border-radius: 9px;
    padding: 9px 11px; min-width: 190px; box-shadow: 0 10px 28px rgba(0,0,0,.55); z-index: 5;
  }
  .tip.show { opacity: 1; }
  .tip .when { color: var(--muted); font-size: 11px; margin-bottom: 7px; }
  .tip .grp { margin-top: 7px; }
  .tip .grp:first-of-type { margin-top: 0; }
  .tip .nm { display: flex; align-items: center; gap: 6px; font-weight: 700; font-size: 11.5px; margin-bottom: 3px; }
  .tip .dot { width: 9px; height: 3px; border-radius: 2px; }
  .tip .kv { display: flex; justify-content: space-between; gap: 18px; font-size: 12px; }
  .tip .kv span:first-child { color: var(--muted); }
  .tip .kv.sp span:last-child { color: #b9c4d6; }

  .stats { width: 100%; border-collapse: collapse; background: var(--panel); border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
  .stats th, .stats td { padding: 9px 12px; text-align: right; white-space: nowrap; }
  .stats th { color: var(--muted); font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: .05em; border-bottom: 1px solid var(--line); }
  .stats th:first-child, .stats td:first-child { text-align: left; }
  .stats tbody tr + tr td { border-top: 1px solid #1b2231; }
  .stats .nm { display: flex; align-items: center; gap: 8px; font-weight: 650; }
  .stats .dot { width: 11px; height: 3px; border-radius: 2px; }
  .scroller { overflow-x: auto; }

  footer { color: var(--muted); font-size: 11.5px; display: flex; flex-wrap: wrap; gap: 6px 16px; }
  .warn { color: #fbbf24; }
  /* Phones. The controls are the part that breaks first: three pills and six
     range buttons cannot sit on one 360px line, so each row gets the full width
     and the ranges spread across it instead of wrapping raggedly. */
  @media (max-width: 620px) {
    .wrap { padding: 11px; gap: 11px; }
    header.brand { gap: 10px; padding-bottom: 10px; }
    header.brand .logo .plate { padding: 5px 8px; border-radius: 7px; }
    header.brand .logo svg, header.brand .logo img { height: 26px; max-width: 92px; }
    header.brand .logo > svg { height: 34px; }
    header.brand .t { font-size: 14.5px; }
    header.brand a.src { display: none; }

    .top { align-items: center; gap: 10px 14px; }
    .price { font-size: 24px; }
    .chg { font-size: 12.5px; }
    select.cur { padding: 8px 10px; font-size: 14px; min-width: 92px; }

    .controls { flex-direction: column; align-items: stretch; gap: 9px; }
    .toggles { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }
    .tg { justify-content: center; padding: 9px 6px; font-size: 11.5px; gap: 5px; }
    .tg .sw { width: 14px; }
    .ranges { justify-content: space-between; }
    .ranges button { flex: 1; padding: 9px 4px; }

    #chart { height: 46vh; min-height: 280px; }
    .stats th, .stats td { padding: 8px 9px; font-size: 12px; }
    .tip { min-width: 168px; padding: 8px 10px; }
  }

  /* Very narrow phones: the series names are what has to give, not the numbers. */
  @media (max-width: 380px) {
    .tg span:last-child { font-size: 11px; }
    .price { font-size: 21px; }
  }

  /* Landscape phone — almost no vertical room, so stop the chart eating it. */
  @media (max-height: 480px) and (orientation: landscape) {
    #chart { height: 62vh; min-height: 200px; }
  }
</style>
</head>
<body>
<div class="wrap">

  <header class="brand">
    <span class="logo"><!--__LOGO__--></span>
    <div class="who">
      <div class="t">BCA Forex Rates</div>
      <div class="s">Kurs history &middot; Bank Central Asia</div>
    </div>
    <a class="src" href="https://www.bca.co.id/id/informasi/kurs" target="_blank" rel="noopener noreferrer">Source &#8599;</a>
  </header>

  <div class="top">
    <div>
      <div class="sub" style="margin-bottom:5px">CURRENCY / IDR</div>
      <select class="cur" id="cur"></select>
    </div>
    <div style="flex:1">
      <div class="quote">
        <span class="price" id="price">—</span>
        <span class="chg" id="chg"></span>
      </div>
      <div class="sub" id="quoteSub"></div>
    </div>
  </div>

  <div class="controls">
    <div class="toggles" id="toggles"></div>
    <div class="ranges" id="ranges"></div>
  </div>

  <div class="chartbox">
    <svg id="chart"></svg>
    <div class="tip" id="tip"></div>
    <div class="notice" id="notice" hidden></div>
  </div>

  <div class="scroller">
    <table class="stats">
      <thead><tr>
        <th>Series</th><th>Buy</th><th>Sell</th><th>Spread</th><th>Spread %</th>
        <th>High</th><th>Low</th><th>Change</th>
      </tr></thead>
      <tbody id="statsBody"></tbody>
    </table>
  </div>

  <footer>
    <span id="cov"></span>
    <span id="stamp"></span>
    <span id="fails"></span>
    <span>Times are WIB (UTC+7). Buy = solid, sell = dashed, shaded band = spread.</span>
  </footer>
</div>

<script>
"use strict";
const DATA = /*__DATA__*/null;

const COLORS = { e_rate: "#38bdf8", tt: "#fbbf24", bank_notes: "#c084fc" };
const RANGES = [
  { k: "1D", s: 86400 }, { k: "1W", s: 604800 }, { k: "1M", s: 2592000 },
  { k: "3M", s: 7776000 }, { k: "1Y", s: 31536000 }, { k: "ALL", s: Infinity },
];
const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

const nf = new Intl.NumberFormat("id-ID", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmt = v => (v == null || !isFinite(v)) ? "—" : nf.format(v);

// WIB is a fixed +7 with no DST, so shifting the epoch and reading UTC parts is
// exact — and, unlike toLocaleString, it cannot be moved by the viewer's own
// timezone. A chart of Indonesian bank rates should read the same everywhere.
function wibParts(ts) {
  const d = new Date((ts + 7 * 3600) * 1000);
  return { y: d.getUTCFullYear(), mo: d.getUTCMonth(), d: d.getUTCDate(),
           h: d.getUTCHours(), mi: d.getUTCMinutes(), s: d.getUTCSeconds() };
}
const p2 = n => String(n).padStart(2, "0");
const fmtTime = ts => { const p = wibParts(ts); return p2(p.h) + ":" + p2(p.mi); };
const fmtDay = ts => { const p = wibParts(ts); return p.d + " " + MONTHS[p.mo]; };
const fmtFull = ts => { const p = wibParts(ts);
  return p.d + " " + MONTHS[p.mo] + " " + p.y + ", " + p2(p.h) + ":" + p2(p.mi) + " WIB"; };

const el = id => document.getElementById(id);
const svg = el("chart"), tip = el("tip"), notice = el("notice");

const state = {
  cur: null,
  range: "ALL",
  on: Object.fromEntries(DATA.series.map(s => [s.key, true])),
  hover: -1,
  view: null,
};

/* ---------- data selection ------------------------------------------------ */

// Which series this currency actually carries. Bank Notes is missing entirely
// for the currencies BCA does not deal in cash, so the control is disabled
// rather than silently drawing nothing.
function availableSeries(cur) {
  const rec = DATA.data[cur] || {};
  return DATA.series.filter(s => Array.isArray(rec[s.key]));
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

function activeSeries(cur) {
  return availableSeries(cur).filter(s => state.on[s.key]);
}

/* ---------- scales -------------------------------------------------------- */

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

/* ---------- rendering ----------------------------------------------------- */

function render() {
  const cur = state.cur, rec = DATA.data[cur];
  const idx = visibleIndices(cur, state.range);
  const series = activeSeries(cur);
  const W = svg.clientWidth || 900, H = svg.clientHeight || 400;
  const M = { t: 14, r: 66, b: 26, l: 10 };
  const iw = Math.max(10, W - M.l - M.r), ih = Math.max(10, H - M.t - M.b);

  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("width", W); svg.setAttribute("height", H);

  if (!rec || !idx.length || !series.length) {
    svg.innerHTML = `<text x="${W / 2}" y="${H / 2}" text-anchor="middle">${
      !series.length ? "No series selected" : "No data for this currency"}</text>`;
    state.view = null;
    notice.hidden = true;
    renderStats([], []);
    return;
  }

  // y domain across every enabled series in view
  let lo = Infinity, hi = -Infinity;
  for (const s of series) for (const arm of rec[s.key]) for (const i of idx) {
    const v = arm[i];
    if (v != null) { if (v < lo) lo = v; if (v > hi) hi = v; }
  }
  if (!isFinite(lo)) { lo = 0; hi = 1; }
  if (hi - lo < Math.max(hi * 1e-6, 1e-9)) { const p = Math.abs(hi) * 0.002 || 1; lo -= p; hi += p; }
  const pad = (hi - lo) * 0.10; lo -= pad; hi += pad;

  const t = rec.t;
  const t0 = t[idx[0]], t1 = t[idx[idx.length - 1]];
  const xOf = i => idx.length === 1 ? M.l + iw / 2
    : M.l + (t1 === t0 ? (idx.indexOf(i) / (idx.length - 1)) : (t[i] - t0) / (t1 - t0)) * iw;
  const yOf = v => M.t + (1 - (v - lo) / (hi - lo)) * ih;

  const parts = [];

  // grid + price axis (right, the way a trading terminal puts it)
  for (const v of niceTicks(lo, hi, Math.max(3, Math.min(8, Math.round(ih / 54))))) {
    const y = yOf(v);
    if (y < M.t - 1 || y > M.t + ih + 1) continue;
    parts.push(`<line x1="${M.l}" y1="${y.toFixed(1)}" x2="${M.l + iw}" y2="${y.toFixed(1)}" stroke="#1c2433"/>`);
    parts.push(`<text x="${M.l + iw + 8}" y="${(y + 3.5).toFixed(1)}">${fmt(v)}</text>`);
  }

  // time axis — label density follows width, format follows span
  const spanSec = t1 - t0;
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

  // A missing scrape leaves no row at all, so an outage shows up as a jump in
  // time rather than a null. Anything well past the normal cadence gets treated
  // as a break: drawing through it would invent a smooth move across hours the
  // scraper never actually observed.
  const deltas = [];
  for (let k = 1; k < idx.length; k++) deltas.push(t[idx[k]] - t[idx[k - 1]]);
  deltas.sort((a, b) => a - b);
  const cadence = deltas.length ? deltas[Math.floor(deltas.length / 2)] : 0;
  const gapLimit = cadence > 0 ? cadence * 3 : Infinity;

  // one band per series: buy above/below sell, the gap between them shaded
  const dots = idx.length <= 60;
  for (const s of series) {
    const [buy, sell] = rec[s.key], c = COLORS[s.key];
    // contiguous runs only — a gap in the history must break the line, not
    // interpolate across an outage as though rates had been observed
    let run = [];
    const flush = () => {
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
      run = [];
    };
    for (const i of idx) {
      if (buy[i] == null || sell[i] == null) { flush(); continue; }
      if (run.length && t[i] - t[run[run.length - 1]] > gapLimit) flush();
      run.push(i);
    }
    flush();

    // last-value flag on the price axis
    const last = [...idx].reverse().find(i => buy[i] != null);
    if (last != null) {
      const y = yOf(buy[last]);
      parts.push(`<rect x="${M.l + iw + 2}" y="${(y - 8).toFixed(1)}" width="${M.r - 6}" height="16" rx="3" fill="${c}" fill-opacity=".16"/>`);
      parts.push(`<text x="${M.l + iw + 8}" y="${(y + 3.5).toFixed(1)}" fill="${c}" style="font-weight:700">${fmt(buy[last])}</text>`);
    }
  }

  // crosshair
  if (state.hover >= 0 && idx.includes(state.hover)) {
    const x = xOf(state.hover);
    parts.push(`<line x1="${x.toFixed(1)}" y1="${M.t}" x2="${x.toFixed(1)}" y2="${M.t + ih}" stroke="#5f6f8c" stroke-dasharray="3 3"/>`);
    for (const s of series) {
      const [buy, sell] = rec[s.key];
      for (const v of [buy[state.hover], sell[state.hover]]) {
        if (v != null) parts.push(`<circle cx="${x.toFixed(1)}" cy="${yOf(v).toFixed(1)}" r="4" fill="${COLORS[s.key]}" stroke="#0b0e14" stroke-width="1.5"/>`);
      }
    }
  }

  svg.innerHTML = parts.join("");
  state.view = { idx, xOf, M, iw, ih };

  // an honest word about thin history, rather than a chart that looks broken
  const n = idx.length;
  notice.hidden = n > 3;
  if (!notice.hidden) {
    notice.textContent = n === 1
      ? "Only one scrape stored — there is no line to draw yet. Leave `run` going and reopen this file."
      : `Only ${n} scrapes stored so far — the shape will fill in as history builds.`;
  }

  renderQuote(idx);
  renderStats(series, idx);
}

function renderQuote(idx) {
  const rec = DATA.data[state.cur];
  const primary = activeSeries(state.cur)[0] || availableSeries(state.cur)[0];
  if (!primary) { el("price").textContent = "—"; el("chg").textContent = ""; el("quoteSub").textContent = ""; return; }
  const [buy, sell] = rec[primary.key];
  const last = [...idx].reverse().find(i => buy[i] != null);
  const first = idx.find(i => buy[i] != null);
  el("price").textContent = last == null ? "—" : fmt(buy[last]);

  const chg = el("chg");
  if (last != null && first != null && first !== last) {
    const d = buy[last] - buy[first], pct = (d / buy[first]) * 100;
    chg.textContent = (d > 0 ? "+" : "") + fmt(d) + "  (" + (d > 0 ? "+" : "") + pct.toFixed(2) + "%)";
    chg.className = "chg " + (d > 0 ? "up" : d < 0 ? "down" : "flat");
  } else {
    chg.textContent = "unchanged over this range";
    chg.className = "chg flat";
  }
  const sp = (last != null && sell[last] != null) ? sell[last] - buy[last] : null;
  el("quoteSub").innerHTML =
    `<b>${primary.label}</b> buy &middot; sell ${fmt(last == null ? null : sell[last])}` +
    (sp == null ? "" : ` &middot; spread ${fmt(sp)}`) +
    (last == null ? "" : ` &middot; ${fmtFull(rec.t[last])}`);
}

function renderStats(series, idx) {
  const rec = DATA.data[state.cur], body = el("statsBody");
  if (!series.length) { body.innerHTML = `<tr><td colspan="8" style="color:var(--muted)">Nothing selected.</td></tr>`; return; }
  body.innerHTML = series.map(s => {
    const [buy, sell] = rec[s.key];
    const vals = idx.map(i => buy[i]).filter(v => v != null);
    const last = [...idx].reverse().find(i => buy[i] != null);
    const first = idx.find(i => buy[i] != null);
    const hi = vals.length ? Math.max(...vals) : null, lowv = vals.length ? Math.min(...vals) : null;
    const b = last == null ? null : buy[last], sv = last == null ? null : sell[last];
    const sp = (b != null && sv != null) ? sv - b : null;
    let chg = "—", cls = "flat";
    if (last != null && first != null && buy[first]) {
      const d = buy[last] - buy[first];
      chg = (d > 0 ? "+" : "") + fmt(d) + " (" + (d > 0 ? "+" : "") + ((d / buy[first]) * 100).toFixed(2) + "%)";
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

/* ---------- crosshair: mouse hover, and press-and-hold on touch ----------- */

function nearest(clientX) {
  const v = state.view; if (!v) return -1;
  const box = svg.getBoundingClientRect();
  const x = clientX - box.left;
  let best = -1, bd = Infinity;
  for (const i of v.idx) { const d = Math.abs(v.xOf(i) - x); if (d < bd) { bd = d; best = i; } }
  return best;
}

function showTip(i, clientX) {
  const rec = DATA.data[state.cur], series = activeSeries(state.cur);
  if (i < 0 || !series.length) return hideTip();
  let html = `<div class="when">${fmtFull(rec.t[i])}</div>`;
  for (const s of series) {
    const [buy, sell] = rec[s.key];
    if (buy[i] == null && sell[i] == null) continue;
    const sp = (buy[i] != null && sell[i] != null) ? sell[i] - buy[i] : null;
    html += `<div class="grp">
      <div class="nm"><span class="dot" style="background:${COLORS[s.key]}"></span>${s.label}</div>
      <div class="kv"><span>Buy</span><span>${fmt(buy[i])}</span></div>
      <div class="kv"><span>Sell</span><span>${fmt(sell[i])}</span></div>
      <div class="kv sp"><span>Spread</span><span>${fmt(sp)}</span></div>
    </div>`;
  }
  tip.innerHTML = html;
  tip.classList.add("show");
  const box = svg.parentElement.getBoundingClientRect();
  const w = tip.offsetWidth, x = clientX - box.left;
  tip.style.left = Math.max(6, Math.min(box.width - w - 6, x + 16)) + "px";
  tip.style.top = "14px";
}
function hideTip() { tip.classList.remove("show"); }

svg.addEventListener("mousemove", e => {
  const i = nearest(e.clientX);
  if (i !== state.hover) { state.hover = i; render(); }
  showTip(i, e.clientX);
});
svg.addEventListener("mouseleave", () => { state.hover = -1; hideTip(); render(); });

// Touch: a tap should still scroll the page, so the crosshair only takes over
// after a deliberate hold. Once it has, moves are ours and scrolling stops.
let holdTimer = null, holding = false, startY = 0, startX = 0;
svg.addEventListener("touchstart", e => {
  const tch = e.touches[0]; startX = tch.clientX; startY = tch.clientY;
  holdTimer = setTimeout(() => {
    holding = true;
    svg.style.touchAction = "none";
    state.hover = nearest(startX); render(); showTip(state.hover, startX);
    if (navigator.vibrate) navigator.vibrate(8);
  }, 260);
}, { passive: true });

svg.addEventListener("touchmove", e => {
  const tch = e.touches[0];
  if (!holding) {
    if (Math.abs(tch.clientY - startY) > 9 || Math.abs(tch.clientX - startX) > 9) {
      clearTimeout(holdTimer);            // they are scrolling, not inspecting
    }
    return;
  }
  e.preventDefault();
  const i = nearest(tch.clientX);
  if (i !== state.hover) { state.hover = i; render(); }
  showTip(i, tch.clientX);
}, { passive: false });

function endTouch() {
  clearTimeout(holdTimer);
  if (holding) { holding = false; svg.style.touchAction = "pan-y"; state.hover = -1; hideTip(); render(); }
}
svg.addEventListener("touchend", endTouch);
svg.addEventListener("touchcancel", endTouch);

/* ---------- controls ------------------------------------------------------ */

function buildToggles() {
  const avail = new Set(availableSeries(state.cur).map(s => s.key));
  el("toggles").innerHTML = DATA.series.map(s => {
    const has = avail.has(s.key), on = has && state.on[s.key];
    return `<label class="tg ${on ? "on" : ""} ${has ? "" : "off-data"}" data-k="${s.key}"
              title="${has ? "Show buy and sell for " + s.label : s.label + " is not offered for this currency"}">
      <input type="checkbox" ${on ? "checked" : ""} ${has ? "" : "disabled"} data-k="${s.key}">
      <span class="sw"></span><span>${s.label}</span>
    </label>`;
  }).join("");
  el("toggles").querySelectorAll("input").forEach(inp => {
    inp.addEventListener("change", () => {
      state.on[inp.dataset.k] = inp.checked;
      inp.closest(".tg").classList.toggle("on", inp.checked);
      render();
    });
  });
}

function buildRanges() {
  const t = (DATA.data[state.cur] || { t: [] }).t;
  const span = t.length > 1 ? t[t.length - 1] - t[0] : 0;
  el("ranges").innerHTML = RANGES.map(r => {
    // a range shorter than the history is always useful; longer ones are only
    // offered once there is enough history for them to mean anything
    const usable = r.s === Infinity || span >= r.s * 0.15 || r.s <= 86400;
    return `<button data-r="${r.k}" class="${state.range === r.k ? "on" : ""}" ${usable ? "" : "disabled"}>${r.k}</button>`;
  }).join("");
  el("ranges").querySelectorAll("button").forEach(b => {
    b.addEventListener("click", () => {
      state.range = b.dataset.r;
      el("ranges").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
      state.hover = -1; hideTip(); render();
    });
  });
}

function setCurrency(code) {
  state.cur = code;
  state.hover = -1; hideTip();
  buildToggles(); buildRanges();
  const t = DATA.data[code].t;
  el("cov").textContent = `${t.length} scrape${t.length === 1 ? "" : "s"} stored` +
    (t.length ? ` · ${fmtFull(t[0])} → ${fmtFull(t[t.length - 1])}` : "");
  render();
}

function init() {
  if (!DATA.currencies.length) {
    document.querySelector(".wrap").innerHTML =
      "<p style='color:#8b97ab'>The database has no rates yet. Run <code>python -m bca_forex once</code> and rebuild this chart.</p>";
    return;
  }
  el("cur").innerHTML = DATA.currencies.map(c => `<option>${c}</option>`).join("");
  el("cur").value = DATA.currencies.includes("USD") ? "USD" : DATA.currencies[0];
  el("cur").addEventListener("change", e => setCurrency(e.target.value));

  el("stamp").textContent = DATA.pageUpdated ? "· BCA page: " + DATA.pageUpdated : "";
  el("fails").innerHTML = DATA.failedRuns
    ? `· <span class="warn">${DATA.failedRuns} failed scrape${DATA.failedRuns === 1 ? "" : "s"}</span>` : "";

  setCurrency(el("cur").value);
  addEventListener("resize", () => { hideTip(); state.hover = -1; render(); });
}
init();
</script>
</body>
</html>
"""
