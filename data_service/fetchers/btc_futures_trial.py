"""BTCUSDT perpetual M15 research only; never sends orders or reads keys."""
from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta, timezone

import httpx

from . import trial_signals
from .signal_engine import atr_series, ema_series, rsi_series

KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
_utc = trial_signals._utc
resolve = trial_signals.resolve  # same conservative OHLC resolver


def fetch_klines() -> list[dict]:
    response = httpx.get(
        KLINES_URL, params={"symbol": "BTCUSDT", "interval": "15m", "limit": 1000},
        timeout=15.0,
    )
    response.raise_for_status()
    raw = response.json()
    if not isinstance(raw, list) or not raw:
        raise ValueError("BTC futures klines kosong/tidak valid")
    bars = []
    for row in raw:
        if not isinstance(row, list) or len(row) < 6:
            raise ValueError("Format BTC futures kline tidak valid")
        bars.append({
            "datetime": datetime.fromtimestamp(int(row[0]) / 1000, timezone.utc).isoformat(),
            "open": float(row[1]), "high": float(row[2]),
            "low": float(row[3]), "close": float(row[4]),
        })
    if any(_utc(b["datetime"]) + timedelta(minutes=15) != _utc(n["datetime"])
           for b, n in zip(bars, bars[1:])):
        raise ValueError("BTC futures klines tidak kontinu/urut")
    return bars


def candidates(bars: list[dict], now: datetime, after: str | None) -> tuple[list[dict], str | None]:
    """Signal uses closed bar only; next candle open is a theoretical paper fill."""
    now = now.astimezone(timezone.utc)
    if len(bars) < 100:
        return [], after
    times = [_utc(b["datetime"]) for b in bars]
    end = min(bisect_right(times, now - timedelta(minutes=15)) - 1, len(bars) - 2)
    if end < 60:
        return [], after
    start = end if after is None else max(60, bisect_right(times, _utc(after)))
    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    ema21, ema50 = ema_series(closes, 21), ema_series(closes, 50)
    rsis = rsi_series(closes)
    atrs = atr_series(highs, lows, closes)
    found = []
    for i in range(start, end + 1):
        if times[i + 1] != times[i] + timedelta(minutes=15):
            continue
        if rsis[i] is None or atrs[i] is None or atrs[i] <= 0:
            continue
        bar = bars[i]
        bullish = float(bar["close"]) > float(bar["open"])
        bearish = float(bar["close"]) < float(bar["open"])
        rsi = float(rsis[i])
        trend_up = ema21[i] > ema50[i]
        setup = side = None
        # Broad research thresholds, deliberately not promoted to live signals.
        if trend_up and bullish and 40 <= rsi <= 62:
            setup, side = "trend", "buy"
        elif not trend_up and bearish and 38 <= rsi <= 60:
            setup, side = "trend", "sell"
        elif rsi <= 35 and bullish:
            setup, side = "reversal", "buy"
        elif rsi >= 65 and bearish:
            setup, side = "reversal", "sell"
        if side is None:
            continue
        entry = float(bars[i + 1]["open"])
        spread = entry * 0.0001  # paper assumption: 1 bp; not an exchange quote
        entry += spread if side == "buy" else 0.0
        distance = max(float(atrs[i]) * 1.2, entry * 0.0015)
        sl = entry - distance if side == "buy" else entry + distance
        tp = entry + 2 * distance if side == "buy" else entry - 2 * distance
        found.append({
            "id": f"btc-futures-paper-{times[i].strftime('%Y%m%d%H%M')}",
            "exchange": "Binance USD-M", "symbol": "BTCUSDT", "market": "perpetual futures",
            "setup": setup, "side": side, "candle_utc": times[i].isoformat(),
            "entry_candle_utc": times[i + 1].isoformat(),
            "entry": round(entry, 2), "sl": round(sl, 2), "tp": round(tp, 2),
            "rr": 2.0, "rsi": round(rsi, 1), "atr": round(float(atrs[i]), 2),
            "spread_assumed": round(spread, 4), "slippage_assumed": 0.0,
            "fees_included": False, "funding_included": False,
            "status": "open", "paper": True,
            "late": (now - times[i] - timedelta(minutes=15)) > timedelta(minutes=30),
        })
    return found, bars[end]["datetime"]
