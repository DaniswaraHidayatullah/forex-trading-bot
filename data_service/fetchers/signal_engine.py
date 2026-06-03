"""Signal engine: hitung sinyal XAUUSD di sisi server (cloud) supaya bisa
jalan 24/7 tanpa MT5/laptop. Output dipakai untuk eksekusi MANUAL.

Logika sama seperti EA:
  - Tren dari EMA50 vs EMA200 di timeframe tren (default H4 / "4h")
  - Entry dari RSI pullback di timeframe entry (default M30 / "30min")
  - SL = ATR * mult ; TP = SL * RR (1:3)
  - Digabung dgn sentimen berita + news blackout (dari /context)

Sumber harga: Twelve Data (https://twelvedata.com) — GRATIS (butuh API key
gratis, tanpa kartu). Andal dari server/cloud lintas region (beda dgn Yahoo
yang sering blokir IP datacenter). Simbol "XAU/USD".

Indikator dihitung manual (pure Python) -> tidak perlu pandas/numpy, ringan
untuk Railway free tier.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

TD_URL = "https://api.twelvedata.com/time_series"


# --- Pengambilan harga --------------------------------------------------

def fetch_series(symbol: str, interval: str, outputsize: int, api_key: str,
                 timeout: float = 15.0) -> list[dict[str, float]]:
    """Ambil OHLC dari Twelve Data, urut lama->baru. Lempar bila gagal."""
    params = {
        "symbol": symbol, "interval": interval, "outputsize": str(outputsize),
        "order": "ASC", "apikey": api_key, "format": "JSON",
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(TD_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
    if isinstance(data, dict) and data.get("status") == "error":
        raise RuntimeError(data.get("message", "twelvedata error"))
    values = data.get("values") if isinstance(data, dict) else None
    if not values:
        raise RuntimeError("data harga kosong")
    out: list[dict[str, float]] = []
    for v in values:
        out.append({
            "open": float(v["open"]), "high": float(v["high"]),
            "low": float(v["low"]), "close": float(v["close"]),
        })
    return out


# --- Indikator (pure Python) -------------------------------------------

def ema_series(values: list[float], n: int) -> list[float]:
    k = 2.0 / (n + 1)
    e = values[0]
    out = [e]
    for v in values[1:]:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def rsi_series(closes: list[float], n: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) < n + 1:
        return out
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n

    def _val(ag: float, al: float) -> float:
        if al == 0:
            return 100.0
        rs = ag / al
        return 100.0 - 100.0 / (1.0 + rs)

    out[n] = _val(avg_g, avg_l)
    for i in range(n + 1, len(closes)):
        avg_g = (avg_g * (n - 1) + gains[i - 1]) / n
        avg_l = (avg_l * (n - 1) + losses[i - 1]) / n
        out[i] = _val(avg_g, avg_l)
    return out


def atr_series(highs: list[float], lows: list[float], closes: list[float],
               n: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) < n + 1:
        return out
    trs = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    atr = sum(trs[1:n + 1]) / n
    out[n] = atr
    for i in range(n + 1, len(trs)):
        atr = (atr * (n - 1) + trs[i]) / n
        out[i] = atr
    return out


def _lot_for_equity(equity: float) -> float:
    if equity < 400:
        return 0.01
    lot = 0.02 + int((equity - 400) // 200) * 0.01
    return min(round(lot, 2), 0.05)


# --- Pembentuk sinyal ---------------------------------------------------

def build_signal(
    sentiment_bias: str,
    news_blocked: bool,
    api_key: str,
    symbol: str = "XAU/USD",
    equity: float = 100.0,
    trend_interval: str = "4h",
    entry_interval: str = "30min",
    rr: float = 3.0,
    atr_mult: float = 1.5,
    ema_fast: int = 50,
    ema_slow: int = 200,
    rsi_lo: float = 40.0,
    rsi_hi: float = 60.0,
    use_sentiment: bool = True,
) -> dict[str, Any]:
    """Bangun sinyal XAUUSD. Selalu kembalikan dict (tidak pernah lempar)."""
    base: dict[str, Any] = {
        "symbol": "XAUUSD",
        "signal": "none",
        "reason": "",
        "entry": None, "sl": None, "tp": None, "rr": rr,
        "atr": None, "trend": "flat", "rsi": None,
        "sentiment_bias": sentiment_bias, "news_blocked": news_blocked,
        "suggested_lot": _lot_for_equity(equity),
        "price_source": "twelvedata:" + symbol,
        "time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    if news_blocked:
        base["reason"] = "Blackout berita high-impact -> tunggu"
        return base
    if not api_key:
        base["reason"] = "TWELVEDATA_API_KEY belum diset di server"
        return base

    try:
        h4 = fetch_series(symbol, trend_interval, ema_slow + 30, api_key)
        m30 = fetch_series(symbol, entry_interval, 60, api_key)
    except Exception as e:  # noqa: BLE001 - tetap balas, jangan 500
        base["reason"] = f"Data harga tidak tersedia: {e}"
        return base

    if len(h4) < ema_slow + 2 or len(m30) < 20:
        base["reason"] = "Data harga belum cukup untuk indikator"
        return base

    h4_close = [b["close"] for b in h4]
    ema_f = ema_series(h4_close, ema_fast)[-2]
    ema_s = ema_series(h4_close, ema_slow)[-2]
    trend = 1 if ema_f > ema_s else -1 if ema_f < ema_s else 0

    m_close = [b["close"] for b in m30]
    m_high = [b["high"] for b in m30]
    m_low = [b["low"] for b in m30]
    rsi_val = rsi_series(m_close)[-2]
    atr_val = atr_series(m_high, m_low, m_close)[-2]
    price = m_close[-1]

    if rsi_val is None or atr_val is None:
        base["reason"] = "Indikator belum siap (data kurang)"
        return base

    base["trend"] = "up" if trend == 1 else "down" if trend == -1 else "flat"
    base["rsi"] = round(rsi_val, 1)
    base["atr"] = round(atr_val, 2)
    base["entry"] = round(price, 2)

    if trend == 0:
        base["reason"] = "Tren H4 flat (EMA50 ~ EMA200) -> tunggu"
        return base

    want_buy = trend == 1 and rsi_lo <= rsi_val <= rsi_hi
    want_sell = trend == -1 and rsi_lo <= rsi_val <= rsi_hi
    if not want_buy and not want_sell:
        base["reason"] = f"RSI {rsi_val:.0f} di luar zona pullback ({rsi_lo:.0f}-{rsi_hi:.0f})"
        return base

    if use_sentiment and sentiment_bias != "flat":
        if want_buy and sentiment_bias != "long":
            base["reason"] = "Setup BUY tapi sentimen bukan long -> skip"
            return base
        if want_sell and sentiment_bias != "short":
            base["reason"] = "Setup SELL tapi sentimen bukan short -> skip"
            return base

    sl_dist = atr_val * atr_mult
    tp_dist = sl_dist * rr
    if want_buy:
        base.update({
            "signal": "buy",
            "sl": round(price - sl_dist, 2),
            "tp": round(price + tp_dist, 2),
            "reason": f"Uptrend H4 + RSI pullback {rsi_val:.0f} + sentimen {sentiment_bias}",
        })
    else:
        base.update({
            "signal": "sell",
            "sl": round(price + sl_dist, 2),
            "tp": round(price - tp_dist, 2),
            "reason": f"Downtrend H4 + RSI pullback {rsi_val:.0f} + sentimen {sentiment_bias}",
        })
    return base
