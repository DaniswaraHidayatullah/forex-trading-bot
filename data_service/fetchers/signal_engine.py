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

from collections.abc import Callable
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


# --- Profil strategi (RR tetap 1:3) ------------------------------------
# Tiap profil = timeframe + pengali SL berbeda. RR sama (1:3).
PROFILES: dict[str, dict[str, Any]] = {
    "scalp": {
        "label": "Scalping", "trend": "30min", "entry": "5min",
        "atr_mult": 1.2, "hold": "menit s/d ~1 jam",
    },
    "intraday": {
        "label": "Intraday", "trend": "4h", "entry": "30min",
        "atr_mult": 1.5, "hold": "jam s/d ~1-2 hari",
    },
    "swing": {
        "label": "Swing", "trend": "1day", "entry": "4h",
        "atr_mult": 2.0, "hold": "hari s/d minggu",
    },
}

PIP = 0.10  # 1 pip emas = $0.10 gerak harga


# --- Pembentuk sinyal ---------------------------------------------------

def build_signal(
    sentiment_bias: str,
    news_blocked: bool,
    api_key: str,
    symbol: str = "XAU/USD",
    equity: float = 100.0,
    profile: str = "intraday",
    rr: float = 3.0,
    ema_fast: int = 50,
    ema_slow: int = 200,
    rsi_lo: float = 40.0,
    rsi_hi: float = 60.0,
    use_sentiment: bool = True,
    fetch_fn: Callable[[str, int], list[dict[str, float]]] | None = None,
) -> dict[str, Any]:
    """Bangun sinyal XAUUSD untuk satu profil (scalp/intraday/swing).

    Selalu kembalikan dict (tidak pernah lempar). fetch_fn(interval, size)
    bisa di-inject untuk caching harga (hemat kuota API).
    """
    prof = PROFILES.get(profile, PROFILES["intraday"])
    trend_interval = prof["trend"]
    entry_interval = prof["entry"]
    atr_mult = prof["atr_mult"]

    base: dict[str, Any] = {
        "symbol": "XAUUSD",
        "signal": "none",
        "reason": "",
        "profile": prof["label"],
        "trend_tf": trend_interval, "entry_tf": entry_interval,
        "hold": prof["hold"],
        "entry": None, "sl": None, "tp": None, "rr": rr,
        "sl_pips": None, "tp_pips": None,
        "risk_per_001": None, "reward_per_001": None,
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

    if fetch_fn is None:
        def fetch_fn(interval: str, size: int) -> list[dict[str, float]]:
            return fetch_series(symbol, interval, size, api_key)

    try:
        h4 = fetch_fn(trend_interval, ema_slow + 30)
        m30 = fetch_fn(entry_interval, 60)
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
        base["reason"] = f"Tren {trend_interval} flat (EMA50 ~ EMA200) -> tunggu"
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
    base.update({
        "sl_pips": round(sl_dist / PIP),
        "tp_pips": round(tp_dist / PIP),
        "risk_per_001": round(sl_dist, 2),     # $ rugi per 0.01 lot bila kena SL
        "reward_per_001": round(tp_dist, 2),   # $ untung per 0.01 lot bila kena TP
    })
    if want_buy:
        base.update({
            "signal": "buy",
            "sl": round(price - sl_dist, 2),
            "tp": round(price + tp_dist, 2),
            "reason": f"Uptrend {trend_interval} + RSI pullback {rsi_val:.0f} + sentimen {sentiment_bias}",
        })
    else:
        base.update({
            "signal": "sell",
            "sl": round(price + sl_dist, 2),
            "tp": round(price - tp_dist, 2),
            "reason": f"Downtrend {trend_interval} + RSI pullback {rsi_val:.0f} + sentimen {sentiment_bias}",
        })
    return base
