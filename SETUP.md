# Setup reference

How this is actually wired, as it stands. Written to be read cold, months from
now, having forgotten all of it.

`README.md` explains what the project is and why it makes the choices it does.
This file is the operational half: what runs where, and what to type.

---

## The short version

A scraper on the NAS reads BCA's published exchange rates on a trading-hours
schedule (every 15 min in hours, hourly otherwise) into
a SQLite file, and rewrites a self-contained HTML chart after each read. A second
container serves that chart. An existing Cloudflare Tunnel publishes it at
`https://forex.mulya.id`, which installs to a phone as an app.

Update it by running one script on the NAS.

---

## Where everything is

| | |
| --- | --- |
| Repo | `github.com/ghuuuu/bca-forex-scraper` (**private**) |
| NAS folder | `/volume1/docker/BCA-Forex-Scraper` |
| NAS LAN address | `192.168.18.212` |
| Database | `<nas folder>/data/bca-forex.sqlite3` |
| Served files | `<nas folder>/web/` |
| Public URL | `https://forex.mulya.id` |
| LAN URL | `http://192.168.18.212:7000` |
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
https://forex.mulya.id          Cloudflare edge — TLS terminates here
      |
      v
tunnel "nas-Mulya"              cloudflared, running in its own stack on the NAS
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
`sm-material-library_default`, which is where `cloudflared` already lives. That
second one is what lets the tunnel resolve `bca-forex-web` by name. It is set by
`EDGE_NETWORK` in `docker-compose.yml`, defaulting to that name.

`bca-forex-scraper` is deliberately **not** on that network, and its database is
never mounted into the web container. Nothing reachable from the tunnel can see
the history file.

---

## Cloudflare configuration

Tunnel **nas-Mulya**, under Zero Trust → Networks → Tunnels. It carries two
hostnames; this project is the second.

| Destination | Type | Service |
| --- | --- | --- |
| `smlibrary.mulya.id` | Published application | `http://sm-material-library:8000` |
| `forex.mulya.id` | Published application | `http://bca-forex-web:8080` |

**Port 8080, not 7000.** That is the port *inside* the container. Traffic goes
container-to-container and never touches the host, which is the whole point —
an address like `192.168.18.212:7000` works too but breaks the day DHCP moves
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

Two cadences, Indonesian time:

- **Trading hours (Mon–Fri 08:00–16:00 WIB):** every 15 minutes.
- **After 16:00, nights and weekends:** once an hour. Some currencies still
  drift when the counters are shut, so it does not go fully dark; 16:00 lands
  as the first hourly slot, capturing the close.

Times are computed from a **fixed UTC+7** inside the code, not from the system
clock's timezone. Indonesia has no daylight saving, so the offset is correct
rather than merely convenient, and the slots hold even if the NAS clock is set
to something else. `TZ=Asia/Jakarta` in the `Dockerfile` only tidies library
logs.

To change the window or cadence, edit the constants at the top of
`bca_forex/schedule.py` — `OPEN_MIN` / `CLOSE_MIN` and the two `*_SLOTS`
tuples — and everything else follows:

```python
OPEN_MIN, CLOSE_MIN = 8 * 60, 17 * 60
ACTIVE_SLOTS = tuple(range(0, 60, 15))   # trading hours
CLOSED_SLOTS = (0,)                      # nights + weekends, hourly
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

## The phone app

Open `https://forex.mulya.id` and install it:

- **iOS** — Share → Add to Home Screen
- **Android** — Chrome offers an install prompt

It caches itself, so it still opens away from home and shows the last rates it
saw, with an amber **"Offline — showing last synced data"** marker in the footer.
Load it online at least once first; the service worker installs in the
background on that first visit.

Offline needs HTTPS — service workers only run in a secure context. That is why
this goes through the tunnel and not `http://192.168.18.212:7000`.

---

## When something is wrong

**The page is stale or the change did not appear.** The image was not rebuilt.
Run `deploy.sh`, then hard-refresh.

**`forex.mulya.id` returns 502.** The tunnel cannot reach the origin. Check
`bca-forex-web` is running, and that it is still on the shared network:

```bash
sudo docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' bca-forex-web
```

Both `bcaforexscraper_default` and `sm-material-library_default` should be
listed.

**`network sm-material-library_default not found` on deploy.** The other stack
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
