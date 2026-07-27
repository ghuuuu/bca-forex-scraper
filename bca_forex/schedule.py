"""When to scrape: every quarter hour — :00, :15, :30, :45 — WIB.

Pinned to the CLOCK, not to process start. A plain "sleep 15 minutes" loop lands
on whatever minute the machine happened to boot on, and moves every restart, so
the history ends up sampled at times nobody can predict or line up between runs.
On the quarter hour exactly: slots that read the same on every machine and every
restart, so two runs of this scraper can be compared row for row.

Everything is computed in WIB (UTC+7) so the mini PC's own timezone — and any
daylight-saving nonsense elsewhere — cannot move the slots. Indonesia does not
observe DST, so a fixed offset is correct rather than merely convenient.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7), "WIB")

#: Minutes past each hour to run at.
SLOTS: tuple[int, ...] = tuple(range(0, 60, 15))


def now_wib() -> datetime:
    return datetime.now(WIB)


def next_slot(after: datetime | None = None) -> datetime:
    """The next scheduled moment strictly after `after` (default: now), in WIB."""
    ref = (after or now_wib()).astimezone(WIB)
    base = ref.replace(second=0, microsecond=0)
    for m in SLOTS:
        candidate = base.replace(minute=m)
        if candidate > ref:
            return candidate
    # past the last slot this hour -> the first slot of the next one
    return (base + timedelta(hours=1)).replace(minute=SLOTS[0])


def seconds_until_next(after: datetime | None = None) -> float:
    ref = after or now_wib()
    return max(0.0, (next_slot(ref) - ref).total_seconds())
