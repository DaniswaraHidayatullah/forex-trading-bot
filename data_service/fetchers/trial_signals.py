"""Pure, closed-candle XAUUSD M15 candidates for a paper-only trial.

This module does not place orders, read Discord credentials, or alter the live
signal engine. OHLC results are estimates, not broker fills.
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from typing import Any

from .signal_engine import atr_series, ema_series, rsi_series


def _utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def closed_index(bars: list[dict[str, Any]], now: datetime) -> int:
    """Last fully closed M15 bar that has a subsequent bar for entry pricing."""
    cutoff = now.astimezone(timezone.utc) - timedelta(minutes=15)
    return min(
        bisect_right([_utc(b["datetime"]) for b in bars], cutoff) - 1,
        len(bars) - 2,
    )


def _hourly(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[datetime, list[dict[str, Any]]] = {}
    for bar in bars:
        dt = _utc(bar["datetime"])
        groups.setdefault(dt.replace(minute=0, second=0, microsecond=0), []).append(bar)
    out = []
    for hour, group in sorted(groups.items()):
        if len(group) != 4 or sorted(_utc(b["datetime"]).minute for b in group) != [0, 15, 30, 45]:
            continue
        out.append({"close_utc": hour + timedelta(hours=1), "close": float(group[-1]["close"])})
    return out


def candidates(bars: list[dict[str, Any]], now: datetime, after: str | None) -> tuple[list[dict[str, Any]], str | None]:
    """Return all new candidates, each priced at the following M15 open.

    On first invocation only the latest eligible candle is considered; old
    history is not mislabeled as a forward trial. Later invocations catch up
    after delayed GitHub Actions runs, marking stale alerts as late.
    """
    end = closed_index(bars, now)
    if end < 220:
        return [], after
    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    rsis = rsi_series(closes)
    atrs = atr_series(highs, lows, closes)
    hours = _hourly(bars)
    if len(hours) < 51:
        return [], after
    hour_closes = [h["close"] for h in hours]
    fast = ema_series(hour_closes, 21)
    slow = ema_series(hour_closes, 50)
    hour_ends = [h["close_utc"] for h in hours]
    start = end if after is None else max(220, bisect_right([_utc(b["datetime"]) for b in bars], _utc(after)))
    found = []
    for i in range(start, end + 1):
        bar = bars[i]
        candle_time = _utc(bar["datetime"])
        close_time = candle_time + timedelta(minutes=15)
        if _utc(bars[i + 1]["datetime"]) != close_time:
            continue  # never use a delayed next bar as an entry price
        if not 5 <= close_time.hour < 21 or close_time.weekday() >= 5:
            continue
        hi = bisect_right(hour_ends, close_time) - 1
        if hi < 50 or rsis[i] is None or atrs[i] is None or atrs[i] <= 0:
            continue
        rsi = float(rsis[i])
        atr = float(atrs[i])
        trend_up = fast[hi] > slow[hi]
        strong_trend = abs(fast[hi] - slow[hi]) > 1.5 * atr
        bullish = float(bar["close"]) > float(bar["open"])
        bearish = float(bar["close"]) < float(bar["open"])
        setup = side = None
        if strong_trend:
            if trend_up and bullish and 40 <= rsi <= 60:
                setup, side = "trend", "buy"
            elif not trend_up and bearish and 40 <= rsi <= 60:
                setup, side = "trend", "sell"
        elif rsi <= 35 and bullish:
            setup, side = "range", "buy"
        elif rsi >= 65 and bearish:
            setup, side = "range", "sell"
        if side is None:
            continue
        entry = float(bars[i + 1]["open"]) + (0.30 if side == "buy" else 0.0)
        recent = bars[max(0, i - 11):i + 1]
        wick = (entry - min(float(b["low"]) for b in recent) if side == "buy"
                else max(float(b["high"]) for b in recent) + 0.30 - entry)
        distance = max(1.2 * atr, wick + 0.5 * atr + 0.30)
        sl = entry - distance if side == "buy" else entry + distance
        tp = entry + 2 * distance if side == "buy" else entry - 2 * distance
        found.append({
            "id": f"paper-{setup}-{candle_time.strftime('%Y%m%d%H%M')}",
            "symbol": "XAUUSD", "setup": setup, "side": side,
            "candle_utc": candle_time.isoformat(),
            "entry_candle_utc": _utc(bars[i + 1]["datetime"]).isoformat(),
            "entry": round(entry, 3), "sl": round(sl, 3), "tp": round(tp, 3),
            "rr": 2.0, "rsi": round(rsi, 1), "atr": round(atr, 3),
            "spread_assumed": 0.30, "slippage_assumed": 0.0,
            "risk_usd_at_02_cent_lot_assumed": round(distance * 0.2, 2),
            "status": "open", "paper": True,
            "late": (now - close_time) > timedelta(minutes=30),
        })
    return found, str(bars[end]["datetime"])


def resolve(entries: list[dict[str, Any]], bars: list[dict[str, Any]], now: datetime) -> None:
    """Conservative OHLC resolution; sell exits are evaluated at ask."""
    cutoff = now.astimezone(timezone.utc) - timedelta(minutes=15)
    for entry in entries:
        if entry.get("status") != "open":
            continue
        start = _utc(entry["entry_candle_utc"])
        expiry = start + timedelta(days=2)
        expected = start
        saw_bar = False
        for bar in bars:
            dt = _utc(bar["datetime"])
            if dt < start or dt > cutoff or dt > expiry:
                continue
            if dt != expected:
                entry["status"] = "unknown"  # missing prices: never guess win/loss
                entry["exit_candle_utc"] = dt.isoformat()
                break
            saw_bar = True
            expected = dt + timedelta(minutes=15)
            high, low = float(bar["high"]), float(bar["low"])
            if entry["side"] == "sell":
                high += float(entry["spread_assumed"])
                low += float(entry["spread_assumed"])
            hit_sl = low <= entry["sl"] if entry["side"] == "buy" else high >= entry["sl"]
            hit_tp = high >= entry["tp"] if entry["side"] == "buy" else low <= entry["tp"]
            if hit_sl or hit_tp:
                entry["status"] = "loss" if hit_sl else "win"
                entry["exit_candle_utc"] = dt.isoformat()
                entry["result_r"] = -1.0 if hit_sl else 2.0
                break
        if entry["status"] == "open" and not saw_bar and now - start > timedelta(minutes=30):
            entry["status"] = "unknown"
            entry["exit_candle_utc"] = now.isoformat()
        if entry["status"] == "open" and now > expiry:
            entry["status"] = "expired"
            entry["exit_candle_utc"] = now.isoformat()
