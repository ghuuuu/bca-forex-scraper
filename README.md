# BCA Forex Scraper

Collects Bank Central Asia's published exchange rates
(<https://www.bca.co.id/id/informasi/kurs>) into a local SQLite file, on a
schedule, unattended.

**Standalone.** Nothing here imports from the downstream tools, and there is
nothing to `pip install` — Python's standard library only. Move this folder
anywhere with Python 3.11+ and it runs.

## Quick start

```bat
run-once.bat
```

Scrapes now, stores, and prints what it got. To leave it collecting:

```bat
run-service.bat
```

Or from a terminal:

```bat
python -m bca_forex once
python -m bca_forex run
```

## What it collects

All 18 currencies BCA publishes, six rates each — **e-Rate**, **TT Counter** and
**Bank Notes**, buy and sell. Everything lands in `data/bca-forex.sqlite3`.

> Some currencies (AED, CNH, DKK…) genuinely publish `0` for bank notes: BCA
> does not trade physical notes in them. That is real data, not a parse failure.

**Already deployed and just need to remember how it fits together?**
[`SETUP.md`](SETUP.md) is the operational reference: what runs where, the
Cloudflare wiring, and what to type when something looks wrong.

## Commands

| Command | What it does |
| --- | --- |
| `once` | Scrape now and exit. Use with Task Scheduler if you prefer per-run tasks over a long-running service. |
| `run` | Stay running and scrape **every 15 minutes, WIB**. |
| `latest` | Print the newest stored scrape (`--json` for machine output). |
| `export --out rates.csv` | Dump history to CSV. `--currency USD` to narrow it. |
| `chart --open` | Build `chart.html` from the history and open it. |
| `status` | Row counts, date range, and how many runs have failed. |

Add `--db <path>` to any of them to use a different database file.

## Schedule

Runs **every 15 minutes on the quarter hour**, Indonesian time: 08:00, 08:15,
08:30, 08:45… — 4 scrapes an hour, 96 a day.

- **Pinned to the clock, not to start-up.** A plain "sleep 15 minutes" loop
  lands on whatever minute the machine booted on and moves on every restart, so
  the history gets sampled at times you cannot predict or line up between runs.
- **On the quarter hour exactly**, so the slots read the same on every machine
  and after every restart.
- **Computed in WIB (UTC+7)**, so the mini PC's own timezone setting cannot
  shift the slots. Indonesia has no daylight saving, so a fixed offset is
  correct and not just convenient.

## Running it on the mini PC

**As a scheduled task (recommended)** — survives reboots and logouts:

1. Task Scheduler → *Create Task*
2. General → **Run whether user is logged on or not**
3. Triggers → *At startup*
4. Actions → *Start a program* → `run-service.bat`, "Start in" = this folder
5. Settings → **If the task fails, restart every 5 minutes**

The scraper already keeps itself alive through network and parse failures; the
restart rule is only for the machine-level cases (a crash, a power cut).

**Prefer per-run tasks?** Trigger `python -m bca_forex once` on a 15-minute
repeat and let Task Scheduler own the timing. The database is the same either way — writes
are idempotent, so an overlapping run cannot duplicate a scrape.

## The data

One row per (scrape, currency), in `data/bca-forex.sqlite3`:

| column | |
| --- | --- |
| `scraped_at` | when we read it, **UTC ISO-8601** |
| `currency` | `USD`, `SGD`, … |
| `e_rate_buy` / `e_rate_sell` | e-Rate |
| `tt_buy` / `tt_sell` | TT Counter |
| `bank_notes_buy` / `bank_notes_sell` | Bank Notes |
| `page_updated` | the "Terakhir diperbarui" stamp BCA printed |

A `runs` table logs every attempt with its outcome, so a quiet failure is still
visible afterwards — `status` reads it.

**`0.00` means "not offered", not a rate of zero.** BCA prints it in Bank Notes
for currencies it holds no physical cash in — AED, CNH, DKK, MYR and SEK at the
time of writing. Filter it out in your own queries; the chart already does.

Timestamps are UTC with an explicit offset. Local time without one is how a
history becomes unreadable after a clock or timezone change.

Query it with anything that speaks SQLite:

```sql
SELECT scraped_at, e_rate_sell FROM rates
WHERE currency = 'USD' ORDER BY scraped_at DESC LIMIT 20;
```

## The chart

```bat
python -m bca_forex chart --open
```

Writes `chart.html` — **one file, no dependencies, no server**. The SVG is drawn
in plain JavaScript and the history is embedded in the page, so it opens from a
USB stick or an email attachment with no network at all. `run-chart.bat`
double-clicks it.

- **Three controls, one per rate type.** e-Rate, TT Counter and Bank Notes. Each
  one shows *both* sides: buy solid, sell dashed, and the band shaded between
  them — that band is the spread, which is the thing worth looking at.
- **Headline picker.** The big number defaults to **e-Rate sell** — what you pay
  to buy foreign currency — and the dropdown swaps it to any of the six
  series/side combinations. High, low and change follow whichever side you pick.
- **Two charts.** A second panel for another currency, defaulting to USD and
  CNH. Side by side once the window is wide enough for a half-width panel to
  hold the whole stats table, stacked below that — two cramped charts read
  worse than two full-width ones. Metric, series and range stay shared across
  both panels, so the two are read the same way, and the crosshair is synced by
  timestamp: hovering one marks the same moment on the other.
- **It keeps itself current.** The page refetches its own payload shortly after
  each scrape slot and re-renders in place, so a screen left open does not go
  stale. Selections survive — it swaps the data, it does not reload.
- **Every currency in one file.** Switch with the dropdown; no rebuild needed.
  Scales differ by a factor of 2000 (KRW ≈ 12 IDR, CHF ≈ 22 000), so they get
  one chart each rather than one unreadable chart together.
- **Hover for detail**, or **press and hold** on a touchscreen — a quick tap
  still scrolls the page, so the chart does not hijack the phone.
- **Ranges** 1D / 1W / 1M / 3M / 1Y / ALL, greyed out until there is history
  behind them.
- **Gaps stay gaps.** If the scraper was down, the span is shaded grey and the
  line becomes a grey dotted bridge across it — so the outage reads as an
  outage, not as a real move the scraper never observed.
- **Bank Notes disables itself** for currencies BCA quotes as `0.00`.

Options: `--currency USD` for one code, `--limit N` to cap points per currency,
`--out <path>` to write elsewhere, `--logo <file>` to use a different image in
the header (svg/png/jpg/gif/webp).

The header carries BCA's own logo, `bca_forex/assets/logo-bca.svg` — the same
file their kurs page links. It lives inside the package because the Docker image
copies only `bca_forex/`, and it is **embedded, not linked**, so the chart still
shows it after being copied to a phone or a USB stick.

SVG is inlined as markup rather than base64'd: the asset is 70 KB of path
geometry and base64 would add a third again for nothing. It sits on a white
plate because the mark is a single flat blue drawn for white paper — recolouring
it would make it the wrong logo. That asset is most of `chart.html`'s ~100 KB.

`run --chart <path>` keeps a chart rebuilt after every scrape, which is how the
NAS setup below serves a page that is never more than 15 minutes stale.

## Running on a Synology NAS

The DS916+ is x86_64, so the stock `python:3.12-slim` image runs natively — no
ARM variant, no emulation. Two containers come out of one image: `scraper`
writes `index.html` after each pull, `web` serves it. If the web half dies
nothing stops being recorded.

**DSM 7.2+ (Container Manager)**

1. Copy this folder to the NAS, e.g. `/volume1/docker/bca-forex`
   (File Station, or `\\<nas>\docker\bca-forex` over SMB). **Make sure the empty
   `data/` and `web/` folders come with it** — see the note below.
2. **Container Manager** → **Project** → **Create**
   - Project name: `bca-forex`
   - Path: the folder from step 1
   - Source: **Use existing docker-compose.yml**
3. **Build** and start it. The first build pulls the Python base image.
4. Open `http://<nas-ip>:7000/`

**DSM 6.2 / 7.0–7.1 (Docker package)** has no Compose UI. SSH in
(*Control Panel → Terminal & SNMP → Enable SSH*) and run it by hand:

```bash
cd /volume1/docker/bca-forex && sudo docker compose up -d --build
```

If `docker compose` is not found on the older package, use `docker-compose`.

**Updating it**

```bash
sudo ./deploy.sh
```

Pulls, rebuilds, restarts, clears dangling layers and prints the last of the
log. Safe to re-run — the database is on the host and a rebuild never touches
it. Without a git checkout (the copy-over-SMB workflow) it skips the pull and
rebuilds what is on disk, which is the step that actually gets forgotten.

The rebuild is the part that matters: `Dockerfile` copies `bca_forex/` **into
the image**, so new files on the NAS change nothing until the image is rebuilt.
That is why a plain `up -d`, or copying files and restarting, appears to do
nothing.

**`error: Your local changes to the following files would be overwritten by merge`**

— when you have not changed anything. Check the diff:

```bash
sudo git diff --stat
```

Every file listed with **0 insertions and 0 deletions**, binaries unchanged in
size, means the difference is file *modes*, not content. Synology shared folders
apply ACLs that report everything as `0777`, so git sees the executable bit set
on files the index records as `100644`. (`deploy.sh` is committed `100755`, so it
matches and is conspicuously absent from that list — the giveaway.)

```bash
sudo git config core.fileMode false
```

`deploy.sh` now sets this itself, so it should only ever bite once. `reset
--hard` also appears to fix it and does not: nothing was modified to begin with,
and the tree dirties itself again on the next checkout.

**`fatal: bad object refs/heads/@eaDir/main@SynoEAStream`**

DSM's indexing service scatters `@eaDir` metadata folders through anything
copied over SMB. One landing inside `.git/refs/heads/` becomes a ref git cannot
parse, and every fetch fails. Clear them and retry:

```bash
sudo find /volume1/docker/bca-forex -name '@eaDir' -prune -exec rm -rf {} +
sudo git fetch origin && sudo git reset --hard origin/main
```

They stop coming back once you pull instead of copying over SMB.

Three things that bite on Synology, all already handled:

- **`.sh` files must keep LF endings.** Checked out with CRLF on Windows and
  copied over, the script fails as `bad interpreter: No such file or directory`
  — which reads like a missing shell, not a line-ending problem. `.gitattributes`
  pins `eol=lf`; if you edit `deploy.sh` in Notepad, save it as LF.
- **The executable bit** is committed as `100755`. If you copy over SMB rather
  than clone, Windows drops it — `sudo chmod +x deploy.sh` once on the NAS.

**Checking on it**

```bash
sudo docker logs -f bca-forex-scraper
```

You should see a `next scrape HH:MM WIB` line after each pull. For a one-off
scrape or a rebuild without waiting for a slot:

```bash
sudo docker exec bca-forex-scraper python -m bca_forex --db /data/bca-forex.sqlite3 status
```

**`Bind mount failed: ... does not exists`**

Docker on Synology will not create a missing bind-mount source — unlike Docker
on Linux, it refuses to start the container instead. Some copy methods drop
empty folders. Create them and bring it up again:

```bash
sudo mkdir -p /volume1/docker/bca-forex/data /volume1/docker/bca-forex/web
cd /volume1/docker/bca-forex && sudo docker-compose up -d
```

No rebuild needed — the image is already built by this point.

**Things worth knowing**

- **The database lives in `./data` on the NAS**, not in the image. Rebuild or
  replace the container freely; the history stays.
- **`./web` is what gets served, and it holds only the chart.** The database is
  deliberately not mounted into the web container — a static file server would
  hand `bca-forex.sqlite3` to anyone on the LAN who guessed the name.
- **Port 7000** is the host side; change only the left number of `"7000:8080"`
  in `docker-compose.yml` if it clashes. The right number is internal to the
  container and must stay 8080. DSM itself uses 5000/5001.
- **Do not expose this to the internet.** It is `http.server` — no HTTPS, no
  auth, not written to face the open web. Reach it over LAN or your VPN. If you
  want it remote, put it behind DSM's reverse proxy with HTTPS.
- **Timezone does not matter.** `TZ=Asia/Jakarta` is set for tidy library logs,
  but the scraper computes WIB from a fixed +7 itself, so the slots hold even if
  the NAS clock is set to something else.
- **Disk use is trivial** — roughly 1 MB of SQLite per year at 15-minute slots.

## Installing it on a phone

`--pwa` writes a manifest, a service worker and an icon set beside the page, so
it installs to a home screen and keeps working offline. The NAS setup passes it
already; add it to a manual build with:

```bat
python -m bca_forex chart --out web/index.html --pwa
```

Plain `chart --out chart.html` stays exactly as it was — one self-contained
file, nothing referenced, nothing written beside it. A manifest link in a file
you opened from a USB stick would only 404.

**Offline behaviour is deliberate.** The page *is* the data, rewritten after
every scrape, so the service worker goes to the network first and falls back to
cache. On the LAN you get current rates; away from home you get the last ones it
saw, with an amber "Offline — showing last synced data" marker in the footer so
a stale page never passes for a live one.

**Service workers need HTTPS.** That is a browser rule — a secure context is
required, and `http://<nas-ip>:7000/` is not one. Over plain HTTP, iOS will
still add it to the home screen and run it fullscreen, but there is no offline
cache and Android will not offer a real install. For the full thing, put it
behind a hostname with a certificate:

### Cloudflare Tunnel

A tunnel publishes the page at a real HTTPS hostname **without opening a single
inbound port** — `cloudflared` dials out to Cloudflare and traffic returns down
that connection, so the NAS stays unreachable from the internet. That is what
makes this reasonable in a way that port-forwarding `http.server` would not be.

**If you already run a tunnel on this NAS** — for some other subdomain — reuse
it. One tunnel serves any number of hostnames, and a second one buys nothing.

The web container joins the `edge` network so an existing cloudflared can reach
it **by container name**, which is how the other services on such a tunnel are
usually wired. Point `EDGE_NETWORK` in `.env` at whatever network your
cloudflared is on:

```bash
docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' cloudflared
```

Then in the Cloudflare dashboard, on the existing tunnel:

**Public Hostnames → Add a public hostname**

| | |
| --- | --- |
| Subdomain | `forex` |
| Domain | your domain |
| Type | `HTTP` |
| URL | `bca-forex-web:8080` |

Port **8080**, the port inside the container — not the 7000 published to the
LAN. Container-to-container traffic never goes via the host.

`<nas-lan-ip>:7000` works too and needs no network changes, but it hard-codes an
address that DHCP can move, and when it does the hostname breaks with nothing
obvious to point at. A container name cannot drift.

The trade-off of the network approach is coupling: this stack now depends on a
network another project created. Delete or rename that project and `up` fails
with `network ... not found` until `EDGE_NETWORK` is repointed. The tidier
long-term shape is one dedicated network — `docker network create edge` — that
everything you expose joins, owned by nothing in particular.

Nothing to deploy on the Cloudflare side: the hostname is live in seconds. Then
add a policy under **Zero Trust → Access → Applications** for it.

**If you do not have a tunnel yet**, this repo ships one behind a profile, so it
never starts by accident:

1. **Zero Trust → Networks → Tunnels → Create a tunnel**, copy the token.
2. **Public hostname:** `forex.<your-domain>` → type `HTTP`, URL `web:8080`.
   The service name is correct *here* — this container shares the compose
   network with the web container.
3. Put the token in `.env` beside `docker-compose.yml` (see `.env.example`;
   `.env` is gitignored, and the token is a credential — it authorises a
   connection into your machine):

   ```
   TUNNEL_TOKEN=eyJhIjoi...
   ```
4. **Zero Trust → Access → Applications** → add the hostname and a policy.
5. Start it explicitly:

   ```bash
   sudo docker compose --profile tunnel up -d
   ```

Then open `https://forex.<your-domain>` on the phone and install it.

**One wrinkle with Access:** an expired session answers requests with a login
page instead of the file. The service worker refuses to cache redirected or
non-OK responses for exactly that reason — otherwise the app would cache the
login screen and pin itself to it. Set a long session duration in the Access
policy so the phone is not re-authenticating constantly.

To stay LAN-only, comment out the `tunnel` service; everything else works
unchanged.

## Which rate should I use?

For **buying** imported goods, the relevant one is **e-Rate sell** — what it
costs you to acquire the currency. That is the one downstream costing tools re-base their
prices from.

## When BCA changes the page

The parser keys off the `rate-type` attribute BCA puts on each cell
(`rate-type="eRate-buy"`), not on column position. That is deliberate: a
positional parser silently returns *the wrong numbers* when a table is
reordered, which is the worst possible failure for a price feed. Keying off the
attribute means a layout change produces nothing at all, loudly — the run is
logged as failed and `once` exits non-zero.

If that happens, save the page and check the attribute names against
`RATE_KEYS` in `bca_forex/parser.py`.

## Tests

```bat
python -m unittest discover -s tests -v
```

39 tests, run against a real page saved in `tests/fixtures/`. Beyond parsing,
they check the things that would be expensive to get wrong and easy to miss:
that `17.980,00` reads as 17 980 and not 17.98 (Indonesian decimal separators
are the reverse of English), that sell is never below buy (which would mean the
columns were read the wrong way round), that USD lands in a plausible range, and
that the slot maths produces exactly 08:00 → 08:15 → 08:30 → 08:45.

To refresh the fixture from the live page:

```bat
python -c "import urllib.request,pathlib; from bca_forex.fetcher import fetch; pathlib.Path('tests/fixtures/bca-kurs.html').write_text(fetch(), encoding='utf-8')"
```

## Layout

```
BCA Forex Scraper/
├── bca_forex/
│   ├── __main__.py     CLI: once | run | latest | export | status
│   ├── parser.py       HTML -> rates
│   ├── fetcher.py      HTTP, with retries
│   ├── store.py        SQLite
│   ├── schedule.py     every-15-minute WIB slot maths
│   ├── chart.py        the HTML chart builder
│   └── assets/         BCA's logo, plus the PWA icon set
├── tools/make_icons.py regenerates the icons (stdlib PNG writer)
├── tests/              unit tests + a saved page
├── data/               the database (created on first run)
├── run-once.bat
├── run-chart.bat
├── run-service.bat
├── Dockerfile          for the NAS
└── docker-compose.yml  scraper + web server
```
