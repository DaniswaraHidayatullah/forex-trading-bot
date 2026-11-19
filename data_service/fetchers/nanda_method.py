"""XAUUSD M15 breakdown/retest study. OHLC paper fills, never orders."""
from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta

from .trial_signals import _hourly, _utc, resolve

BAR = timedelta(minutes=15)
SUPPORT_BARS = 16
RETEST_BARS = 12
SPREAD = 0.30
EXNESS_PIP_PRICE = 0.01  # Exness XAUUSD instrument specification, not legacy bot convention


def candidates(bars: list[dict], now: datetime, after: str | None,
               pip_price: float) -> tuple[list[dict], str | None]:
    """Arm a theoretical sell limit after a closed breakdown, not after the retest."""
    if pip_price <= 0:
        raise ValueError("pip_price must be positive")
    times = [_utc(b["datetime"]) for b in bars]
    end = bisect_right(times, now - BAR) - 1
    if end < SUPPORT_BARS:
        return [], after
    start = end if after is None else bisect_right(times, _utc(after))
    if start > end:
        return [], after
    hours = _hourly(bars[:end + 1])
    hour_ends = [h["close_utc"] for h in hours]
    found: dict[int, dict] = {}
    # Scan enough prior breakdowns so a restart can still find the first retest.
    for j in range(max(SUPPORT_BARS, start - RETEST_BARS), end):
        if times[j] - times[j - SUPPORT_BARS] != SUPPORT_BARS * BAR:
            continue
        h = bisect_right(hour_ends, times[j] + BAR) - 1
        if h < 20:
            continue
        window = [v["close"] for v in hours[h - 20:h + 1]]
        if not (window[-1] < sum(window[1:]) / 20 and window[-1] < window[0]):
            continue  # H1 close below SMA20, with falling SMA20
        level = min(b["low"] for b in bars[j - SUPPORT_BARS:j])
        if not (bars[j - 1]["close"] >= level and bars[j]["close"] < level - SPREAD):
            continue
        for i in range(j + 1, min(end, j + RETEST_BARS) + 1):
            if times[i] != times[j] + (i - j) * BAR:
                break
            bar = bars[i]
            if bar["high"] < level:
                continue
            if i >= start and bar["open"] <= level:
                sl, tp = round(level + 50 * pip_price, 6), round(level - 100 * pip_price, 6)
                hit_sl = bar["high"] + SPREAD >= sl
                hit_tp_at_close = bar["close"] + SPREAD <= tp
                status = ("loss" if hit_sl else "win" if hit_tp_at_close else
                          "unknown" if bar["low"] + SPREAD <= tp else "open")
                entry = {
                    "id": f"nanda-v1-{times[i]:%Y%m%d%H%M}",
                    "setup": "nanda_v1", "symbol": "XAUUSD", "side": "sell", "paper": True,
                    "breakdown_utc": times[j].isoformat(), "breakdown_close": bars[j]["close"],
                    "candle_utc": times[i].isoformat(), "entry_candle_utc": times[i].isoformat(),
                    "monitor_from_utc": (times[i] + BAR).isoformat(), "entry": level,
                    "sl": sl, "tp": tp, "sl_pips": 50, "tp_pips": 100, "rr": 2,
                    "spread_assumed": SPREAD, "pip_price": pip_price, "status": status,
                    "late": now - (times[i] + BAR) >= BAR,
                }
                if status != "open":
                    entry["exit_candle_utc"] = times[i].isoformat()
                    if status == "unknown":
                        entry["exit_reason"] = "tp_order_unclear_on_retest"
                    else:
                        entry["result_r"] = -1 if status == "loss" else 2
                found[i] = entry  # newest breakdown wins if two retests coincide
            break  # first touch only; gap above the level is not a fill
    return list(found.values()), bars[end]["datetime"]


def resolve_entries(entries: list[dict], bars: list[dict], now: datetime) -> None:
    resolve(entries, bars, now)
