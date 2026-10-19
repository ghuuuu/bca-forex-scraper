# Setup reference

How this is actually wired, as it stands. Written to be read cold, months from
now, having forgotten all of it.

`README.md` explains what the project is and why it makes the choices it does.
This file is the operational half: what runs where, and what to type.

---

## The short version

A scraper on the NAS reads BCA's published exchange rates on a trading-hours
schedule (every 15 min in hours, hourly on weeknights, every 3 h at weekends)
into a SQLite file, and rewrites a self-contained HTML chart after each read. A
second container serves that chart. An existing Cloudflare Tunnel publishes it at
`https://forex.<your-domain>`, which installs to a phone as an app.

Update it by running one script on the NAS.

---

## Where everything is

| | |
| --- | --- |
| Repo | `github.com/ghuuuu/bca-forex-scraper` (**private**) |
| NAS folder | `/volume1/docker/BCA-Forex-Scraper` |
| NAS LAN address | `<nas-lan-ip>` |
| Database | `<nas folder>/data/bca-forex.sqlite3` |
| Served files | `<nas folder>/web/` |
| Public URL | `https://forex.<your-domain>` |
| LAN URL | `http://<nas-lan-ip>:7000` |
| Deploy key | `/root/.ssh/id_ed25519` on the NAS |

Two containers, both from one image built on the NAS:

| Container | Does | Ports |
| --- | --- | --- |
| `bca-forex-scraper` | scrapes every 15 min, rewrites the chart | none |
| `bca-forex-web` | serves `web/` over HTTP | `7000` → `8080` |

---

## How a request reaches the page

```
phone / browser
      |
      v
https://forex.<your-domain>          Cloudflare edge — TLS terminates here
      |
      v
tunnel "nas-tunnel"              cloudflared, running in its own stack on the NAS
      |
      v
http://bca-forex-web:8080       by CONTAINER NAME, over a shared Docker network
      |
      v
python -m http.server           serving /web read-only
```

Nothing is port-forwarded. `cloudflared` makes an **outbound** connection to
Cloudflare and traffic returns down it, so the NAS accepts nothing inbound from
the internet.

`bca-forex-web` is on two Docker networks: its own project network, and
`edge`, which is where `cloudflared` already lives. That
second one is what lets the tunnel resolve `bca-forex-web` by name. It is set by
`EDGE_NETWORK` in `docker-compose.yml`, defaulting to that name.

`bca-forex-scraper` is deliberately **not** on that network, and its database is
never mounted into the web container. Nothing reachable from the tunnel can see
the history file.

---

## Cloudflare configuration

Tunnel **nas-tunnel**, under Zero Trust → Networks → Tunnels. It carries two
hostnames; this project is the second.

| Destination | Type | Service |
| --- | --- | --- |
| `otherapp.<your-domain>` | Published application | `http://other-service:8000` |
| `forex.<your-domain>` | Published application | `http://bca-forex-web:8080` |

**Port 8080, not 7000.** That is the port *inside* the container. Traffic goes
container-to-container and never touches the host, which is the whole point —
an address like `<nas-lan-ip>:7000` works too but breaks the day DHCP moves
the NAS, with nothing obvious to point at.

Access policies live under Zero Trust → Access → Applications, and are
**per-hostname** — the smlibrary policy does not cover forex. Without one, the
hostname is public: discoverable through Certificate Transparency logs, not just
by people you tell. Worth having, because the thing behind it is
`python -m http.server`, which is not hardened for hostile traffic.

If you add one, give it a **long session duration**. An installed app that
demands a login every few hours feels broken.

---

## Updating it

```bash
cd /volume1/docker/BCA-Forex-Scraper && sudo ./deploy.sh
```

That pulls, rebuilds, restarts, clears dangling layers, and prints the container
state plus the tail of the scraper log.

**The rebuild is the part that matters.** The `Dockerfile` copies `bca_forex/`
*into the image*, so new files on the NAS change nothing until the image is
rebuilt. Copying files over and restarting looks like it should work and does
not.

Afterwards, hard-refresh the browser (`Ctrl+Shift+R`) — the old page caches
otherwise, which reads exactly like a failed deploy.

---

## Day-to-day commands

Run on the NAS. All of them are safe.

```bash
# is it alive, and what did it last do
sudo docker ps --format '{{.Names}}\t{{.Status}}\t{{.Ports}}' | grep bca
sudo docker logs --tail 30 bca-forex-scraper

# follow it live
sudo docker logs -f bca-forex-scraper

# how much history, and has anything been failing
sudo docker exec bca-forex-scraper python -m bca_forex --db /data/bca-forex.sqlite3 status

# the newest stored rates
sudo docker exec bca-forex-scraper python -m bca_forex --db /data/bca-forex.sqlite3 latest

# scrape right now instead of waiting for the next slot
sudo docker exec bca-forex-scraper python -m bca_forex --db /data/bca-forex.sqlite3 once

# export to CSV (lands in the container; add --out /data/... to keep it)
sudo docker exec bca-forex-scraper python -m bca_forex --db /data/bca-forex.sqlite3 export --out /data/rates.csv

# restart just the scraper — also forces the chart to be rewritten
sudo docker restart bca-forex-scraper
```

The full command set is `once`, `run`, `latest`, `export`, `chart`, `status`.
Every one takes `--db`.

---

## The schedule

Three cadences, Indonesian time:

- **Trading hours (Mon–Fri 08:00–16:00 WIB):** every 15 minutes.
- **Weeknights (Mon–Fri, after 16:00 and before 08:00):** once an hour. The
  live e-Rate keeps drifting with global FX overnight (~4 hours in 5 move), so
  hourly is worth it; 16:00 lands as the first hourly slot, capturing the close.
- **Weekends (Sat–Sun):** every 3 hours. Global FX is shut — the e-Rate barely
  moves (~1 hour in 4) and the counter rates freeze — so hourly would mostly
  store duplicates. Not fully dark, because a few currencies still drift.

Times are computed from a **fixed UTC+7** inside the code, not from the system
clock's timezone. Indonesia has no daylight saving, so the offset is correct
rather than merely convenient, and the slots hold even if the NAS clock is set
to something else. `TZ=Asia/Jakarta` in the `Dockerfile` only tidies library
logs.

To change the window or cadence, edit the constants at the top of
`bca_forex/schedule.py` — `OPEN_MIN` / `CLOSE_MIN`, the `*_SLOTS` tuples, and
`WEEKEND_STEP_H` — and everything else follows:

```python
OPEN_MIN, CLOSE_MIN = 8 * 60, 16 * 60
ACTIVE_SLOTS = tuple(range(0, 60, 15))   # trading hours: every 15 min
CLOSED_SLOTS = (0,)                      # weeknights: hourly
WEEKEND_STEP_H = 3                       # weekends: every 3 hours
```

---

## The data

One row per (scrape, currency) in `data/bca-forex.sqlite3`, 18 currencies, six
rates each: e-Rate, TT Counter and Bank Notes, buy and sell.

**`0.00` means "not offered", not a rate of zero.** BCA prints it in Bank Notes
for currencies it holds no physical cash in — AED, CNH, DKK, MYR, SEK. Filter it
out in your own queries; the chart already does.

The database lives on the host, not in the image. Rebuild, delete or replace
containers freely — the history survives. It is also the one thing here that
cannot be reconstructed after the fact, so it is what to back up. Roughly 1 MB
per year.

```bash
sudo cp /volume1/docker/BCA-Forex-Scraper/data/bca-forex.sqlite3 /volume1/backup/
```

---

## The JSON API

For other apps — downstream services's quotation FX rate first. Static files the scraper
rewrites after every scrape (`run --api /web/api/v1`), served by the same
`bca-forex-web` container, so there is nothing extra to run.

| URL | Returns |
| --- | --- |
| `https://forex.<your-domain>/api/v1/latest.json` | newest scrape, all 18 currencies |
| `https://forex.<your-domain>/api/v1/history/USD.json` | every stored scrape for one currency, oldest first |

From a container on the NAS that shares a network with `bca-forex-web`, skip
the tunnel: `http://bca-forex-web:8080/api/v1/latest.json`.

```json
{
  "api_version": 1,
  "scraped_at": "2026-10-19T08:15:00+00:00",
  "page_updated": "19 Oktober 2026 15.14 WIB",
  "trading_hours": true,
  "rates": {
    "USD": { "e_rate_buy": 18034, "e_rate_sell": 18060, "tt_buy": 17820,
             "tt_sell": 18100, "bank_notes_buy": 17900, "bank_notes_sell": 18200,
             "e_rate_tight": true, "tt_tight": true, "bank_notes_tight": true }
  }
}
```

**`<rate>_tight` is the one to check before using a rate.** `true` means the
buy/sell spread is BCA's normal tight quote, safe to price or lock from;
`false` means it is wide; `null` means the rate is not offered, or there are
under 50 readings of history in and out of hours, so it cannot judge yet. It is
measured from the quote itself, not the clock, so it is right on public
holidays (wide all day) and on the odd tight 09:00 or 15:15 reading.

One line per currency and rate, from all stored history (`bca_forex/api.py`,
`tight_threshold`): tight when the spread % is at or under

    max( (m + p10) / 2,  m × 1.2 )

where `m` is the median spread in trading hours and `p10` the 10th percentile
out of hours. A tight USD e-Rate is a 0.11% spread, a tight THB one 1.15%, so
no single number works. The same line is applied to every history row too, so
past readings carry the flags. The line moves a little as history grows, so a
past borderline reading can flip.

- **e-Rate** is the one that matters: tight weekdays ~09:15–15:00, wide
  otherwise. Over Jul–Oct 2026, every normal weekday-hours reading was tight
  for 17 of 18 currencies, both public holidays were wide, and 6 of 1,724
  off-hours readings were tight (real tight quotes at 09:00 or 15:15).
  **KRW is the exception:** BCA barely narrows it (6.4% vs 6.8%), so it is
  always tight. That flag means nothing for KRW.
- **TT and Bank Notes** keep one fixed margin round the clock (USD TT 1.57% in
  and out of hours), so they are tight unless BCA posts an abnormal spread. One
  example: EUR and THB TT on 15–16 Oct 2026. Tight says nothing about
  freshness: their mid moves only a few times a day.

History rows are arrays in the order of its `columns` field, to keep the file
small. Since 0.4.0 they end with the three `_tight` flags.

- **One call, every currency.** Pick `rates.USD.e_rate_sell` (or any field).
- **`null` means BCA does not offer it**, never 0. It is the Bank Notes `0.00`
  translated, so nothing downstream can multiply by zero.
- **No auth.** The chart page already embeds the same numbers publicly, so this
  exposes nothing new. If a Cloudflare Access policy is ever added to
  `forex.<your-domain>`, server-to-server callers need a service token, or a bypass
  rule on `/api/*`.
- **No rate on a weekend or holiday is not a thing here.** BCA's e-Rate is live
  every day; `latest.json` is always the newest scrape. Check `scraped_at`:
  older than ~4 hours means the scraper is down, not that the market is shut.
- **`trading_hours` matters for the e-Rate.** It is `true` for a scrape taken
  on a weekday **09:15–15:00 WIB**, inclusive — when BCA quotes the e-Rate tight
  (a 20–26 IDR spread). BCA tightens between 09:00 and 09:15 and widens between
  15:00 and 15:15, so on quarter-hour scrapes those are the first and last tight
  readings. Outside it the spread is 90–120 and the sell side sits ~50 IDR
  higher. A daily job that locks a rate should run after 09:15, not at 09:00.
  This flag is the clock only: **public holidays are not known** — on one
  (17 Aug, 25 Aug 2026) it still says `true` while BCA quotes wide all day.
  Price from `e_rate_tight`, above.
- **No CORS headers** — `python -m http.server` sends none, so a browser page
  on another origin cannot fetch it. Server-side callers are fine.
- **`api_version` changes only on a breaking change.** New fields may appear.

---

## The phone app

Open `https://forex.<your-domain>` and install it:

- **iOS** — Share → Add to Home Screen
- **Android** — Chrome offers an install prompt

It caches itself, so it still opens away from home and shows the last rates it
saw, with an amber **"Offline — showing last synced data"** marker in the footer.
Load it online at least once first; the service worker installs in the
background on that first visit.

Offline needs HTTPS — service workers only run in a secure context. That is why
this goes through the tunnel and not `http://<nas-lan-ip>:7000`.

---

## When something is wrong

**The page is stale or the change did not appear.** The image was not rebuilt.
Run `deploy.sh`, then hard-refresh.

**`forex.<your-domain>` returns 502.** The tunnel cannot reach the origin. Check
`bca-forex-web` is running, and that it is still on the shared network:

```bash
sudo docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' bca-forex-web
```

Both `bcaforexscraper_default` and `edge` should be
listed.

**`network edge not found` on deploy.** The other stack
that owns that network was removed or renamed. Point `EDGE_NETWORK` at the right
one in a `.env` file beside `docker-compose.yml`.

**`error: Your local changes would be overwritten`, but you changed nothing.**
Synology ACLs report every file as `0777`; git reads that as a permission change
on every tracked file. `deploy.sh` sets `core.fileMode false` to prevent it, but
if it ever recurs:

```bash
sudo git config core.fileMode false
```

Do **not** use `reset --hard` for this. It appears to work and does not — there
is no content to discard, and the tree dirties itself again immediately.

**`fatal: bad object refs/heads/@eaDir/...`.** DSM's indexer left metadata
folders inside `.git/`. Only happens if something copies into the folder over
SMB:

```bash
sudo find /volume1/docker/BCA-Forex-Scraper -name '@eaDir' -prune -exec rm -rf {} +
```

**The scraper is up but nothing is being stored.** BCA changed their page
layout. The parser reads their `rate-type` attributes rather than column
positions, so it fails loudly rather than storing wrong numbers — check the log
for `no rates parsed`, and `bca_forex/parser.py` for the attribute names.

---

## Things worth not forgetting

- **Do not copy files into the NAS folder over SMB any more.** It pulls from git
  now. Copying is what put `@eaDir` folders inside `.git/`.
- **Do not port-forward 7000.** It is `python -m http.server` — no auth, not
  written to face the internet. The tunnel is what makes exposure reasonable.
- **The deploy key is read-only** and lives at `/root/.ssh/id_ed25519` on the
  NAS. It must be under `/root` because `deploy.sh` runs under `sudo`.
- **The database is not in git**, by design. Back it up separately.
- **Changing the port** means editing only the left side of `"7000:8080"` in
  `docker-compose.yml`. The right side is internal and must stay 8080.
