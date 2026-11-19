"""Notification layer only; never changes V2 replay fills or balances."""
import math
from bisect import bisect_right
from datetime import timedelta

from . import nanda_method as v1
from .trial_signals import _hourly, _utc

MINUTE = timedelta(minutes=1)


def watch(bars, now):
    times = [_utc(b["datetime"]) for b in bars]
    j = bisect_right(times, now - v1.BAR) - 1
    if j < v1.SUPPORT_BARS or now >= times[j] + 2*v1.BAR:
        return None
    hours = _hourly(bars[:j+1])
    level = v1.broken_support(bars, times, j, hours, [h["close_utc"] for h in hours])
    if level is None:
        return None
    return dict(id=f"watch-{times[j]:%Y%m%d%H%M}", support=level,
                time=(times[j]+v1.BAR).isoformat(), breakdown_utc=times[j].isoformat())


def guard(event, bars, now, kind):
    """Require contiguous M1 coverage INCLUDING current minute, not just a last price."""
    start = _utc(event["time"])
    if not start <= now < start + v1.BAR:
        return "stale"
    last = now.replace(second=0, microsecond=0)
    part = [b for b in bars if start <= _utc(b["datetime"]) <= last]
    if (len(part) != (last-start)//MINUTE + 1 or
            any(_utc(b["datetime"]) != start+i*MINUTE for i,b in enumerate(part))):
        return "missing_m1"
    for b in part:
        lo, hi, close = b["low"], b["high"], b["close"]
        if not all(math.isfinite(v) for v in (lo, hi, close)) or not 0 < lo <= close <= hi:
            return "invalid_price"
        if kind == "watch" and hi >= event["support"]:
            return "already_retested"
        if kind == "confirm" and (hi+v1.SPREAD >= event["sl"] or lo+v1.SPREAD <= event["tp"]):
            return "already_hit_exit"
    return "ok"
