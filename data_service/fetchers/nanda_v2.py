"""Independent paper ledgers for Nanda Rejection; prices are bid proxies."""
from datetime import datetime, timedelta

from . import nanda_method as v1
from .trial_signals import _utc

BAR = timedelta(minutes=15)
SPREAD = 0.30


def initial(now):
    return {"start": now.isoformat(), "cursor": None, "signals": [], "strategy": ledger(),
            "accounts": {f"{lot:g} lot · Unlimited": ledger(lot, "Unlimited")
                         for lot in (0.1, 0.05)}}


def ledger(lot=None, leverage=None):
    return dict(lot=lot, leverage=leverage, balance=100.0 if lot else None, pos=None,
                trades=[], margin_skips=0, overlap_skips=0)


def enter(book, e):
    if book["pos"]:
        book["overlap_skips"] += 1
        return
    if book["lot"]:
        units = book["lot"] * 100
        equity = book["balance"]
        # Proxy for eligible Unlimited below $5000; higher equity tiers use finite leverage.
        lev = 500 if equity >= 100000 else 1000 if equity >= 30000 else 2000 if equity >= 5000 else None
        margin = e["entry"] * units / lev if lev else 0.0
        if equity <= units * SPREAD or equity < margin:
            book["margin_skips"] += 1
            return
    book["pos"] = e.copy()


def settle(book, bar, boundary):
    e = book["pos"]
    if not e:
        return
    units = (book["lot"] or 0) * 100
    start = _utc(e["time"])
    stop = min(e["sl"], e["entry"] + book["balance"] / units) if units else e["sl"]
    time = _utc(bar["datetime"]) if bar else None
    reason = None
    if time is not None and start <= time < start + timedelta(days=2):
        s, t = bar["high"] + SPREAD >= stop, bar["low"] + SPREAD <= e["tp"]
        if s and t:
            reason = "ambiguous"
        elif s:
            reason = "stopout" if units and book["balance"] <= e["distance"] * units else "sl"
        elif t:
            reason = "tp"
    if not reason and (time is None or time + BAR != boundary):
        reason = "gap"
    if not reason and boundary >= start + timedelta(days=2):
        reason = "timeout"
    if not reason:
        return
    trade = dict(id=e["id"], reason=reason, observed_utc=boundary.isoformat())
    if units:
        # Same stress convention as the historical replay. Keep unknowns identifiable.
        pnl = 2 * e["distance"] * units if reason == "tp" else -min(book["balance"], e["distance"] * units)
        book["balance"] = round(book["balance"] + pnl, 8)
        trade.update(pnl_usd=round(pnl, 8), balance=book["balance"])
    book["trades"].append(trade)
    book["pos"] = None


def advance(state, bars, now):
    times = [_utc(b["datetime"]) for b in bars]
    if len(times) < 85 or any(a >= b for a, b in zip(times, times[1:])):
        raise ValueError("insufficient or unordered bars")
    cutoff = datetime.fromisoformat(state["start"]) + timedelta(days=30)
    available = [i for i, t in enumerate(times) if t <= now]
    if not available:
        return []
    cursor = _utc(state["cursor"]) if state["cursor"] else None
    indices = [i for i in available if times[i] > cursor] if cursor else available[-1:]
    if not indices:
        return []
    # V1 provides the closed-bar support/breakdown and first touch only.
    # Never inherit its intrabar fill, stop, exit reason or outcome.
    found, _ = v1.candidates(bars, min(now, cutoff), (times[indices[0]] - 2 * BAR).isoformat(),
                             v1.EXNESS_PIP_PRICE)
    by_time = {_utc(e["candle_utc"]): e for e in found}
    books = [state["strategy"], *state["accounts"].values()]
    fresh = []
    for i in indices:
        t = times[i]
        prev = bars[i - 1] if i and (cursor is None or times[i - 1] == cursor) else None
        for book in books:
            settle(book, prev, t)
        touch = by_time.get(t - BAR)
        if (touch and prev and t < cutoff and _utc(prev["datetime"]) == t - BAR
                and (cursor is not None or now - t < BAR)):
            level, p = touch["entry"], bars[i]["open"]
            if prev["close"] < min(level, prev["open"]) and p < level:
                d = round(max(5.0, prev["high"] + SPREAD + 0.10 - p), 6)
                e = dict(id=f"nanda-v2-{t:%Y%m%d%H%M}", time=t.isoformat(), support=level,
                         retest_high=prev["high"], entry=p, distance=d, sl=round(p+d, 6),
                         tp=round(p-2*d, 6), strategy_taken=not state["strategy"]["pos"])
                state["signals"].append(e)
                for book in books:
                    enter(book, e)
                if e["strategy_taken"] and now - t < BAR:
                    fresh.append(e)
        cursor = t
        state["cursor"] = t.isoformat()
    return fresh
