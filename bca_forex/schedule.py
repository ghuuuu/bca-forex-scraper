"""When to scrape, in WIB.

Three cadences, coarsening as the page goes quiet:

- **Trading hours (Mon-Fri 08:00-16:00 WIB): every 15 minutes**, and
  **every minute in two bands, 08:45-09:15 and 14:45-15:15.** BCA switches its
  e-Rate to the tight dealing quote just after 09:00 and back to the wide one
  just after 15:00; the bands pin down the exact minute. Most of these reads
  store nothing: a row is only written when a rate changed (see
  is_heartbeat).
- **Weeknights (Mon-Fri, after 16:00 and before 08:00): hourly.** The live
  e-Rate tracks global FX and keeps drifting through the night — ~4 in 5 hours
  still show movement — so hourly earns its keep; 15-minute sampling of it does
  not.
- **Weekends (Sat-Sun): every few hours.** Global FX is shut, so the e-Rate
  goes near-static (only ~1 hour in 4 shows any change) and the counter rates
  freeze solid. A handful of currencies still drift, so it does not go fully
  dark — but hourly here is almost all duplicate rows. Measured over the live
  history, not assumed; see WEEKEND_STEP_H.

Pinned to the CLOCK, not to process start. A "sleep 15 minutes" loop lands on
whatever minute the machine booted on and moves every restart; on the quarter
hour exactly, two runs of this scraper line up row for row.

Everything is computed in WIB (UTC+7) so the mini PC's own timezone — and any
daylight-saving nonsense elsewhere — cannot move the slots. Indonesia does not
observe DST, so a fixed offset is correct rather than merely convenient.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7), "WIB")

#: Trading window, in minutes since WIB midnight, both ends included.
OPEN_MIN = 8 * 60    # 08:00
CLOSE_MIN = 16 * 60  # 16:00

#: Minute-of-hour cadences: quarter-hourly when trading, on the hour when not.
ACTIVE_SLOTS: tuple[int, ...] = (0, 15, 30, 45)
CLOSED_SLOTS: tuple[int, ...] = (0,)

#: Weekday bands read every minute, (start, end) in minutes since WIB
#: midnight, both ends included: centred on the open and close switches.
#:
#: TEMPORARY (from 20 Oct 2026): a measurement, not the steady state. Once
#: `python -m bca_forex timing` shows whether the switches land on a
#: consistent minute, narrow these bands or drop them for reads at those
#: exact minutes.
MINUTE_BANDS: tuple[tuple[int, int], ...] = (
    (8 * 60 + 45, 9 * 60 + 15),    # 08:45-09:15
    (14 * 60 + 45, 15 * 60 + 15),  # 14:45-15:15
)

#: Weekend cadence: scrape once every this many hours (at :00), rather than
#: hourly. 3 keeps the occasional weekend drift (CNH and friends) while dropping
#: two rows in three. The hours it fires on: 00, 03, 06 … 21 WIB.
WEEKEND_STEP_H = 3
WEEKEND_HOURS: tuple[int, ...] = tuple(range(0, 24, WEEKEND_STEP_H))


def now_wib() -> datetime:
    return datetime.now(WIB)


def is_active(dt: datetime) -> bool:
    """True during trading hours: a weekday, 08:00 <= time <= 16:00 WIB."""
    ref = dt.astimezone(WIB)
    if ref.weekday() >= 5:  # Saturday, Sunday
        return False
    minutes = ref.hour * 60 + ref.minute
    return OPEN_MIN <= minutes <= CLOSE_MIN


def is_heartbeat(dt: datetime) -> bool:
    """Whether a slot stores every currency, changed or not.

    Rows are written only when a rate changes, which leaves no way to tell a
    rate that held still from a scraper that was down. So on the hour — every
    closed-hours slot, and once an hour while trading — everything is stored
    regardless. A gap longer than that in the history is then a real outage.
    """
    return dt.astimezone(WIB).minute == 0


def is_weekend(dt: datetime) -> bool:
    """True on Saturday or Sunday, WIB."""
    return dt.astimezone(WIB).weekday() >= 5


def _is_slot(cand: datetime) -> bool:
    """Whether `cand` (a whole minute, WIB) lands on a scheduled scrape.

    The cadences, in priority order: the minute bands every minute, trading
    hours every 15 min, weekends every WEEKEND_STEP_H hours, and everything left
    (weeknights) hourly.
    """
    mins = cand.hour * 60 + cand.minute
    if cand.weekday() < 5 and any(a <= mins <= b for a, b in MINUTE_BANDS):
        return True
    if is_active(cand):
        return cand.minute in ACTIVE_SLOTS
    if is_weekend(cand):
        return cand.minute == 0 and cand.hour in WEEKEND_HOURS
    return cand.minute in CLOSED_SLOTS


def next_slot(after: datetime | None = None) -> datetime:
    """The next scheduled moment strictly after `after` (default: now), in WIB.

    Steps minute by minute to the next valid slot. Bounded: the sparsest cadence
    is the weekend one, so a valid slot is never more than WEEKEND_STEP_H hours
    away and this loops at most a few hundred times.
    """
    ref = (after or now_wib()).astimezone(WIB)
    cand = ref.replace(second=0, microsecond=0)
    while True:
        cand += timedelta(minutes=1)
        if _is_slot(cand):
            return cand


def seconds_until_next(after: datetime | None = None) -> float:
    ref = after or now_wib()
    return max(0.0, (next_slot(ref) - ref).total_seconds())
