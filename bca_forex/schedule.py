"""When to scrape, in WIB.

Trading hours (Mon-Fri 08:00-17:00 WIB) are sampled every 15 minutes, when BCA
actually moves its rates. Outside that — nights and weekends — it drops to once
an hour: some currencies (CNH, say) still drift when the counters are shut, so
going dark would lose real movement, but 15-minute sampling of a near-static
page is wasted rows.

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

#: Trading window, in minutes since WIB midnight. 17:00 itself is closed, so it
#: is caught as the first hourly slot — the day's closing rate still lands.
OPEN_MIN = 8 * 60    # 08:00
CLOSE_MIN = 17 * 60  # 17:00

#: Minute-of-hour cadences: every quarter hour when trading, on the hour when not.
ACTIVE_SLOTS: tuple[int, ...] = tuple(range(0, 60, 15))
CLOSED_SLOTS: tuple[int, ...] = (0,)


def now_wib() -> datetime:
    return datetime.now(WIB)


def is_active(dt: datetime) -> bool:
    """True during trading hours: a weekday, 08:00 <= time < 17:00 WIB."""
    ref = dt.astimezone(WIB)
    if ref.weekday() >= 5:  # Saturday, Sunday
        return False
    minutes = ref.hour * 60 + ref.minute
    return OPEN_MIN <= minutes < CLOSE_MIN


def next_slot(after: datetime | None = None) -> datetime:
    """The next scheduled moment strictly after `after` (default: now), in WIB.

    Steps minute by minute to the next valid slot. Bounded: a valid slot is
    never more than an hour away (the closed cadence fires every :00), so this
    loops at most ~60 times.
    """
    ref = (after or now_wib()).astimezone(WIB)
    cand = ref.replace(second=0, microsecond=0)
    while True:
        cand += timedelta(minutes=1)
        slots = ACTIVE_SLOTS if is_active(cand) else CLOSED_SLOTS
        if cand.minute in slots:
            return cand


def seconds_until_next(after: datetime | None = None) -> float:
    ref = after or now_wib()
    return max(0.0, (next_slot(ref) - ref).total_seconds())
