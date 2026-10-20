# Blueprint

The whole system on one page: what it does, how the parts fit, the rules it
keeps, and what was learned about BCA's rates along the way. Current as of
**0.6.0 (20 Oct 2026)**.

- [`README.md`](README.md): what the project is, commands, chart features, NAS install.
- [`SETUP.md`](SETUP.md): the deployed system, what to type, what to do when it breaks.
- This file: the design. Read it before changing anything structural.

---

## 1. Purpose

BCA publishes its exchange rates on a web page
(<https://www.bca.co.id/id/informasi/kurs>) and keeps no history. This project
reads that page on a schedule, stores every reading, and republishes the
history three ways:

| Output | For | Where |
| --- | --- | --- |
| Chart page (installable PWA) | people | `https://forex.<your-domain>` |
| JSON API | other apps (downstream services first) | `https://forex.<your-domain>/api/v1/` |
| SQLite file, CSV export | analysis, backup | `data/bca-forex.sqlite3` on the NAS |

18 currencies, 6 rates each: **e-Rate**, **TT Counter** and **Bank Notes**,
each buy and sell, all quoted in IDR.

---

## 2. Architecture

```
            BCA kurs page (HTML)
                    |
                    |  every slot (1 min / 1 h / 3 h, WIB)
                    v
  +---------------- bca-forex-scraper (container) ----------------+
  |  fetcher.py --> parser.py --> store.py --> data/*.sqlite3     |
  |                                    |       (host bind mount,  |
  |                                    |        never served)     |
  |                     after every scrape                        |
  |                   +----------------+----------------+         |
  |                   v                                 v         |
  |              chart.py                            api.py       |
  |     web/index.html + PWA files         web/api/v1/*.json      |
  +---------------------------------------------------------------+
                    |  ./web, mounted read-only
                    v
  bca-forex-web (container)   python -m http.server 8080
                    |
        +-----------+-------------------------------+
        |                                           |
  LAN  http://<nas-lan-ip>:7000      Cloudflare Tunnel "nas-tunnel"
                                       https://forex.<your-domain>
                                                    |
                                    phones (PWA), downstream services, other apps
```

Two containers, one image. The split is deliberate: if the web server dies,
nothing stops being recorded. The history is the one thing that cannot be
rebuilt later.

---

## 3. Components

All Python standard library, 3.11+. Nothing to `pip install`.

| File | Job |
| --- | --- |
| `bca_forex/fetcher.py` | GET the page with a browser User-Agent; 3 tries with back-off; gzip/deflate. |
| `bca_forex/parser.py` | HTML to rates. Keys off BCA's `rate-type="eRate-buy"` cell attributes, never column position. Reads Indonesian numbers (`17.980,00` = 17980). |
| `bca_forex/store.py` | SQLite (WAL mode): `rates` and `runs` tables. Inserts are idempotent. |
| `bca_forex/schedule.py` | Next scrape slot, in fixed WIB (UTC+7). |
| `bca_forex/chart.py` | Self-contained HTML chart, plus manifest, service worker and icons with `--pwa`. |
| `bca_forex/api.py` | Static JSON API, trading-hours check, and the tight-spread formula. |
| `bca_forex/__main__.py` | CLI: `once`, `run`, `latest`, `export`, `chart`, `status`. |
| `Dockerfile`, `docker-compose.yml` | Image and the two containers (plus an opt-in tunnel container). |
| `deploy.sh` | Pull, rebuild, restart, prune, report. Run on the NAS. |
| `tools/make_icons.py` | Renders the PWA icon PNGs from `assets/BCA-Icon.svg` via headless Chrome (dev only). |
| `tests/` | Unit tests against a saved real BCA page. |

---

## 4. One scrape cycle

`run` loops forever:

1. Sleep until the next slot (`schedule.next_slot`).
2. `fetch()` the page, stopping at the end of the rate table (~118 KB of
   ~370 KB). Network failure: up to 3 tries, then log a failed run.
3. `parse()` it. No rates matched: `ParseError`, logged as a failed run. This
   is a layout change at BCA, and it fails loudly instead of storing wrong numbers.
4. `Store.save()` a row for each currency whose rates differ from its last
   stored row — or for every currency on the hour (the heartbeat), so a gap
   in the history always means an outage. Stamped with the UTC time and BCA's
   own "Terakhir diperbarui" stamp. `INSERT OR IGNORE`: re-running is harmless.
   Every read, stored or not, is logged in `runs`.
5. If anything was stored: `write_api()` rewrites `latest.json` and
   `history/<CCY>.json`, and
6. `build_chart()` rewrites `index.html` (and the PWA files).

Steps 5 and 6 each catch their own errors. A broken chart or API never stops
scraping. Any unexpected error in a cycle is logged and the loop continues.

---

## 5. Schedule

Computed in fixed UTC+7, so the host clock's timezone cannot move it.
Indonesia has no daylight saving. Slots are pinned to the clock (whole
minutes and hours), not to process start, so every run lines up row for row.

| When (WIB) | Cadence | Why |
| --- | --- | --- |
| Mon–Fri 08:30–15:30 | every minute | BCA moves rates in this window, and switches the e-Rate tight/wide at a minute we want to pin down. |
| Mon–Fri other hours | hourly | e-Rate still drifts with global FX (~4 hours in 5 change). |
| Sat–Sun | every 3 h | e-Rate near-static (~1 hour in 4), counter rates frozen. |

Tune with `OPEN_MIN`, `CLOSE_MIN`, `ACTIVE_SLOTS`, `CLOSED_SLOTS` and
`WEEKEND_STEP_H` in `schedule.py`.

The minute window (08:30–15:30) runs half an hour either side of BCA's
switches (09:00–09:15 and 15:00–15:15, section 8), to catch the exact minute.
`python -m bca_forex timing` lists, per weekday, the first read that saw the
e-Rate tight and wide, and when TT / Bank Notes moved, with the median over
days read every minute. Once that settles, the window can shrink to the
minutes that matter.

---

## 6. Data model

`data/bca-forex.sqlite3`:

```sql
rates (
  scraped_at   TEXT,  -- UTC ISO-8601 with offset
  currency     TEXT,  -- USD, SGD, ...
  e_rate_buy, e_rate_sell, tt_buy, tt_sell, bank_notes_buy, bank_notes_sell  REAL,
  page_updated TEXT,  -- BCA's own stamp, e.g. "19 Oktober 2026 15.14 WIB"
  PRIMARY KEY (scraped_at, currency)
)
runs (started_at TEXT, ok INTEGER, currencies INTEGER, message TEXT)  -- every read
```

- **A row means something changed** (or it is the hourly heartbeat), so
  currencies do not share timestamps: "latest" is per currency
  (`store.latest_rows`), never one `MAX(scraped_at)`.

- **`0` means "not offered".** BCA prints `0,00` for Bank Notes in AED, CNH,
  DKK, MYR and SEK. The chart drops it; the API turns it into `null`.
- **UTC with offset in storage, WIB only for display and scheduling.**
- About 1 MB a year at this cadence. History starts 27 Jul 2026.
- The database is the only thing that cannot be rebuilt. Back it up.

---

## 7. Outputs

### 7.1 Chart and PWA

`chart.py` writes one HTML file with the history embedded as JSON and the
SVG drawn in plain JavaScript. No libraries, no network needed. It works from
a USB stick.

- Three rate types drawn as bands: buy solid, sell dashed, spread shaded. The
  headline series is drawn on top; the others are dimmed.
- Headline (default e-Rate sell) chosen by tapping a rate card and the
  Buy / Sell segment. One rate card per series with sparkline and stats.
  Compare mode adds a second panel with a synced crosshair. Ranges 1D–ALL.
- Motion: line wipe on change, count-up numbers, pulsing live dot, price
  flash on refresh, "Updated N min ago" with a sync spinner. All disabled
  under `prefers-reduced-motion`.
- X-axis to scale (real time). Pinch on touch, ctrl+wheel or trackpad pinch
  on desktop, zooms around the fingers or pointer (down to 30 min); drag pans.
  Zoomed to three days or less, closed hours are shaded "Closed".
- Real data gaps (scraper down) show as a grey dotted bridge.
- Refetches itself after each slot; remembers currency, range and layout.
- `--pwa` adds manifest, service worker and icons. The service worker is
  network-first with cache fallback, shows an "Offline" marker, refuses to
  cache redirects (Cloudflare Access login pages), and **skips `/api/`**
  so JSON never overwrites the cached page.

Full feature list: README, "The chart".

### 7.2 JSON API

Static files, rewritten after every scrape that stored a row by `run --api /web/api/v1`,
served by the same web container. No server code, no new port, and the
database stays unreachable from the tunnel.

| URL | Returns |
| --- | --- |
| `/api/v1/latest.json` | each currency's newest rates, plus `checked_at` |
| `/api/v1/history/<CCY>.json` | every stored scrape for one currency, oldest first |

`latest.json`:

```json
{
  "api_version": 1, "app_version": "0.6.0",
  "source": "https://www.bca.co.id/id/informasi/kurs",
  "generated": "2026-10-19T15:52:40+00:00",
  "checked_at": "2026-10-19T15:52:39+00:00",
  "scraped_at": "2026-10-19T15:00:02+00:00",
  "page_updated": "19 Oktober 2026 22.52 WIB",
  "trading_hours": false,
  "fields": ["e_rate_buy", "e_rate_sell", "tt_buy", "tt_sell", "bank_notes_buy", "bank_notes_sell"],
  "rates": {
    "USD": { "e_rate_buy": 17990, "e_rate_sell": 18080, "tt_buy": 17850, "tt_sell": 18130,
             "bank_notes_buy": 17820, "bank_notes_sell": 18100,
             "e_rate_tight": false, "tt_tight": true, "bank_notes_tight": true,
             "scraped_at": "2026-10-19T15:00:02+00:00" }
  }
}
```

`checked_at` is the last time BCA's page was read; each currency's
`scraped_at` is when its rates last changed (or the last heartbeat), and the
top-level `scraped_at` the newest of those. Use `checked_at` for staleness.

`history/<CCY>.json` has the same metadata plus `currency`, `columns` and
`rows`. Rows are arrays (half the bytes of objects):
`[scraped_at, 6 rates…, e_rate_tight, tt_tight, bank_notes_tight]`.

Contract rules:

- **Atomic writes.** Temp file, then rename. A reader never sees half a file.
- **`null`, never `0`.** Nothing downstream can multiply by zero.
- **`trading_hours`** is a clock check only: weekday 09:15–15:00 WIB inclusive.
  It does not know public holidays.
- **`<rate>_tight`** is the one to trust before using a rate (section 8).
- **`api_version`** changes only on a breaking change. New fields can appear.
- **No auth, no CORS.** The chart already publishes the same numbers.
  Server-side callers only. If Cloudflare Access is ever put on
  `forex.<your-domain>`, API callers need a service token or a bypass on `/api/*`.
- **Staleness:** `checked_at` older than ~4 hours means the scraper is down.

---

## 8. BCA rate behaviour, and the tight flag

Measured from the stored history (Jul–Oct 2026), not assumed.

### 8.1 What the rates do

- **e-Rate is a live dealing quote only in hours.** Weekdays from 09:15 to
  15:00 WIB, the USD spread is 20–26 IDR (0.11%). BCA tightens between 09:00
  and 09:15 and widens between 15:00 and 15:15. Outside that, the spread is
  90–120 IDR (~0.5%) and the rate barely moves.
- **Public holidays quote wide all day** (17 Aug and 25 Aug 2026).
- **TT Counter and Bank Notes keep one fixed margin round the clock** (USD TT
  1.57%, in and out of hours). Their mid changes only a few times a day.
- **KRW e-Rate barely narrows** (6.4% in hours vs 6.8% out). It cannot be told
  apart.

**Against Google Finance USD/IDR (week to 19 Oct 2026):** in hours, BCA's
e-Rate mid is within ~±10 IDR of Google, and e-Rate sell is Google +2 to +17.
Out of hours the gap swings from −95 to +171. TT sell runs ~+50 to +80 over
Google. So a "Google ± offset" estimate only holds in hours; reading BCA
directly is the reliable source. Google has no public FX API (shut in 2012).

### 8.2 The tight formula

`api.tight_threshold`, one line per **currency × rate type**, from all stored
history:

```
tight  <=>  spread% <= max( (m + p10) / 2 ,  m × 1.2 )

spread% = (sell − buy) / ((sell + buy) / 2) × 100
m       = median spread% of readings in trading hours (weekday 09:15–15:00 WIB)
p10     = 10th percentile spread% of readings outside them
```

- **First term**: halfway between the tight quote and the low end of the
  wide one. It uses p10, not the median, because weekends quote wider than
  weeknights. A line pulled up by weekends would let the weeknight quote pass.
- **Second term**: for rates with one margin round the clock (TT, Bank
  Notes). There is nothing to split, so the first term would cut a single
  quote in half by noise. With it, they read tight unless the spread is abnormal.
- **No single % works across currencies**: tight USD e-Rate is 0.11%, tight
  THB is 1.15%.
- **`null`** when the rate is not offered, or there are fewer than 50
  readings in hours or out of hours (`CALIBRATION_MIN_ROWS`).
- **Applied to every row**, so history carries the flags. Because the line is
  recomputed from all history after each scrape, a past borderline reading
  can flip as history grows.

Validation over Jul–Oct 2026:

| | Normal weekday hours | Holidays | Off-hours |
| --- | --- | --- | --- |
| e-Rate | all tight, 17 of 18 currencies | all wide | 6 of 1,724 tight, real 09:00 / 15:15 quotes |
| TT | all tight | all tight | all tight except EUR and THB, 15–16 Oct |
| Bank Notes | all tight | all tight | all tight |

Exceptions: THB e-Rate had 47 genuinely wide in-hours readings in August
(spread up to 2.6%), correctly flagged. KRW e-Rate always reads tight: ignore
that flag for KRW.

Knobs: `CALIBRATION_MIN_ROWS = 50`, `TIGHT_TOLERANCE = 1.2`,
`E_RATE_OPEN_MIN` / `E_RATE_CLOSE_MIN` (09:15 / 15:00), all in `api.py`.

---

## 9. Consumers

### downstream services (quotation FX rate)

Not wired yet. downstream services (`C:\Projects\downstream-app`, Next.js + Prisma) gets its
rate from `fetchMarketRate()` in `app/api/cron/update-exchange-rates/route.ts`,
currently `open.er-api.com`, on a daily cron at 09:00 WIB. It adds a buffer:
`bufferedRate = market ÷ (1 − buffer)`. Its `docs/decisions/OPEN-QUESTIONS.md`
item 1 is this API's contract.

Recommended integration, for when it is approved there:

1. Read `https://forex.<your-domain>/api/v1/latest.json` in `fetchMarketRate()`.
2. Use `rates.USD.e_rate_sell` **only when `rates.USD.e_rate_tight` is `true`**.
   Otherwise keep the previous locked rate.
3. Move the cron from 09:00 to **09:30 WIB** (`30 2 * * *` UTC). At 09:00
   BCA is still quoting wide.
4. Check `scraped_at` for staleness.

downstream services's own rules apply: ask before changing pricing logic, update its
`docs/design/*.md` and `DECISIONS.md`.

### Others

Any server-side app can read the same two URLs. From a container on the NAS
that shares a network with `bca-forex-web`, skip the tunnel:
`http://bca-forex-web:8080/api/v1/latest.json`.

---

## 10. Deployment

| | |
| --- | --- |
| Host | Synology NAS (x86_64) |
| Folder | `/volume1/docker/BCA-Forex-Scraper` (git checkout of `ghuuuu/bca-forex-scraper`, private) |
| Containers | `bca-forex-scraper` (no ports), `bca-forex-web` (`7000` → `8080`) |
| Public | Cloudflare Tunnel `nas-tunnel` → `http://bca-forex-web:8080`, hostname `forex.<your-domain>` |
| Tunnel network | `bca-forex-web` joins `edge` (`EDGE_NETWORK`), where cloudflared lives |
| LAN | `http://<nas-lan-ip>:7000` |

**Deploy flow:** commit and push to `main`, then on the NAS run
`sudo ./deploy.sh`. It sets `core.fileMode false` (Synology ACLs), runs
`git pull --ff-only`, `compose up -d --build`, prunes dangling images, and
prints container state and the log tail. The rebuild matters: the image copies
`bca_forex/` in, so new files do nothing until rebuilt.

**Remote deploy** (from a workstation with the key in the Windows ssh-agent),
over Tailscale:

```powershell
ssh ghuuuu@<nas-tailscale-ip> 'cd /volume1/docker/BCA-Forex-Scraper && sudo -n ./deploy.sh'
```

- Use Windows OpenSSH (`C:\Windows\System32\OpenSSH\ssh.exe`). Git Bash's
  `ssh` cannot reach the Windows agent, and the key has a passphrase.
- A sudoers rule lets `ghuuuu` run only `deploy.sh` without a password.
  **Security note:** `deploy.sh` runs whatever is on GitHub `main` as root on
  the NAS. Whoever can push to `main` can run code as root there.
- `deploy.sh` adds `/usr/local/bin` to `PATH`, because a non-interactive SSH
  session on DSM leaves docker off it.
- Key login needed `/volume1/homes` at `755` (sshd StrictModes rejects a
  world-writable parent).

Synology pitfalls (LF line endings, `@eaDir` in `.git`, missing bind-mount
folders, the mode-only diff) are in README and SETUP.

---

## 11. Rules the code keeps

| Rule | Why |
| --- | --- |
| Standard library only | Runs anywhere with Python 3.11+, tiny image, no patch surface. |
| Parse by attribute, not position | A reordered table produces nothing, loudly, never wrong numbers. |
| `0` is "not offered" | Becomes `null` in the API and a gap in the chart. |
| UTC in storage, fixed WIB for logic | Clock or timezone changes cannot corrupt history or move slots. |
| Slots pinned to the clock | Comparable rows across restarts and machines. |
| Outputs never stop scraping | A stale chart is a nuisance; a missed scrape is a permanent hole. |
| Database never mounted in the web container | A static file server would hand it out. |
| Atomic file replacement | Readers never see half-written JSON. |
| Static files, not a server, for the API | No new process, port or attack surface. |
| Tight measured from the quote, not the clock | Right on holidays and odd boundary quotes. |

---

## 12. Testing

```bat
python -m unittest discover -s tests -v
```

57 tests. They cover the parser against a saved real page (Indonesian number
format, sell ≥ buy, plausible USD), schedule slot maths, chart payload and
zero handling, and the API: newest-only `latest.json`, `null` for zero,
history order and columns, trading-hours edges (09:00 no, 09:15 yes, 15:00
yes, 15:15 no, Saturday no), the tight threshold for both the split
(e-Rate) and fixed-margin (TT) cases, and no leftover temp files.

---

## 13. History

| Version | Date | Change |
| --- | --- | --- |
| — | 27 Jul 2026 | Scraper, SQLite, HTML chart, NAS compose, deploy.sh, PWA, Cloudflare Tunnel, SETUP.md |
| 0.1.0 | 29 Jul 2026 | Trading-hours cadence, session x-axis, market badge, hourly after 16:00 |
| 0.2.0 | 6 Aug 2026 | Weekend cadence every 3 h |
| 0.3.0 | 19 Oct 2026 | JSON API (`latest.json`, `history/<CCY>.json`); `trading_hours` 09:15–15:00; remote deploy over SSH |
| 0.4.0 | 19 Oct 2026 | `e_rate_tight`, `tt_tight`, `bank_notes_tight` from one formula, on every history row |
| 0.5.0 | 20 Oct 2026 | Dashboard redesign: rate cards, Buy/Sell segment, Compare switch, motion and loading states; new BCA wordmark, app icons and favicon |
| 0.6.0 | 20 Oct 2026 | Every minute 08:30–15:30 on weekdays, stored only on change plus an hourly heartbeat; read stops after the rate table; `timing` report; chart x-axis to scale, pinch / ctrl+wheel zoom; `checked_at` and per-currency `scraped_at` in `latest.json` |

---

## 14. Known limits and open items

- **Public holidays are not known** to `trading_hours`. The tight flags cover
  this from the data.
- **KRW e-Rate tight flag is meaningless.**
- **Tight is not fresh.** TT and Bank Notes can read tight while hours old.
  Check `scraped_at` and the mid's movement if freshness matters.
- **History files grow** (~3,200 rows per currency after 3 months, rewritten
  every scrape). Fine for years at this size. Add a date-range endpoint or
  paging if they get large.
- **No point-in-time endpoint.** For "rate at time T", read the history file
  and pick the last row at or before T.
- **No CORS.** Browser apps on other origins cannot call the API. Add
  headers (a small proxy or a different server) if one ever needs to.
- **No Cloudflare Access policy** on `forex.<your-domain>` yet. Adding one needs
  the API carve-out in 7.2.
- **downstream services integration** is planned, not built (section 9).
