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
TD_PRICE_URL = "https://api.twelvedata.com/price"


# --- Pengambilan harga --------------------------------------------------

def fetch_series(symbol: str, interval: str, outputsize: int, api_key: str,
                 timeout: float = 15.0) -> list[dict[str, Any]]:
    """Ambil OHLC (dgn datetime UTC) dari Twelve Data, urut lama->baru."""
    params = {
        "symbol": symbol, "interval": interval, "outputsize": str(outputsize),
        "order": "ASC", "apikey": api_key, "format": "JSON", "timezone": "UTC",
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
    out: list[dict[str, Any]] = []
    for v in values:
        out.append({
            "datetime": v.get("datetime", ""),
            "open": float(v["open"]), "high": float(v["high"]),
            "low": float(v["low"]), "close": float(v["close"]),
        })
    return out


def fetch_price(symbol: str, api_key: str, timeout: float = 10.0) -> float:
    """Harga real-time (1 kredit). Lempar bila gagal."""
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(TD_PRICE_URL, params={"symbol": symbol, "apikey": api_key})
        resp.raise_for_status()
        data = resp.json()
    if isinstance(data, dict) and data.get("price"):
        return float(data["price"])
    raise RuntimeError(str(data)[:120])


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


# --- Profil strategi ----------------------------------------------------
# Tiap profil = timeframe + pengali SL (+ opsional rr & zona RSI sendiri).
# "harian" = hasil backtest 3.5 bln (Mar-Jul 2026): RR 1:2, M15/H4, RSI 35-65
# -> ~4 sinyal/hari, WR ~39%, net terbaik (+$417 @0.01 lot) setelah spread.
PROFILES: dict[str, dict[str, Any]] = {
    "harian": {
        "label": "Harian", "trend": "4h", "entry": "15min",
        "atr_mult": 1.5, "rr": 2.0, "rsi_lo": 35.0, "rsi_hi": 65.0,
        "hold": "~1 jam s/d 1 hari",
    },
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

PIP = 0.10          # 1 pip emas = $0.10 gerak harga
SENT_STRONG = 0.30  # |skor sentimen| >= ini dianggap kuat


def market_open(now: datetime | None = None) -> bool:
    """Pasar emas buka? Tutup: Jumat ~21:00 UTC s/d Minggu ~22:00 UTC.
    Saat tutup, data harga jadi basi -> sinyal weekend = artefak (harus di-skip).
    """
    now = now or datetime.now(timezone.utc)
    wd, hr = now.weekday(), now.hour  # Mon=0 .. Sun=6
    if wd == 5:
        return False
    if wd == 4 and hr >= 21:
        return False
    if wd == 6 and hr < 22:
        return False
    return True

# Validitas sinyal (menit) per timeframe entry -> "kapan" entry.
_TF_MINUTES = {"5min": 5, "15min": 15, "30min": 30, "1h": 60,
               "2h": 120, "4h": 240, "1day": 1440}


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
    sentiment_score: float = 0.0,
    sentiment_available: bool = True,
    quote: float | None = None,
    max_risk_usd: float | None = None,
    now_utc: datetime | None = None,
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
    # Profil boleh membawa rr & zona RSI sendiri (hasil backtest).
    rr = float(prof.get("rr", rr))
    rsi_lo = float(prof.get("rsi_lo", rsi_lo))
    rsi_hi = float(prof.get("rsi_hi", rsi_hi))

    base: dict[str, Any] = {
        "symbol": "XAUUSD",
        "signal": "none",
        "reason": "",
        "profile": prof["label"],
        "trend_tf": trend_interval, "entry_tf": entry_interval,
        "hold": prof["hold"],
        "entry": None, "sl": None, "tp": None, "rr": rr,
        "entry_type": None, "entry_zone_low": None, "entry_zone_high": None,
        "valid_minutes": None, "timing": None,
        "sl_pips": None, "tp_pips": None,
        "risk_per_001": None, "reward_per_001": None,
        "atr": None, "trend": "flat", "rsi": None,
        "sentiment_bias": sentiment_bias, "sentiment_score": round(sentiment_score, 3),
        "sentiment_available": sentiment_available,
        "confidence": None, "confidence_level": 0, "confidence_stars": "",
        "news_blocked": news_blocked, "risk_pct": None,
        "suggested_lot": _lot_for_equity(equity),
        "price_source": "twelvedata:" + symbol,
        "time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    if not market_open(now_utc):
        base["reason"] = "Pasar emas TUTUP (weekend) -> tidak ada sinyal"
        return base
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

    # Pakai harga REAL-TIME bila tersedia; kalau menyimpang jauh dari bar
    # terakhir (pasar lari / data tidak sinkron), jangan kasih sinyal basi.
    if quote is not None and quote > 0:
        if abs(quote - price) > 1.0 * atr_val:
            base["entry"] = round(quote, 2)
            base["reason"] = "Harga bergerak cepat / data tidak sinkron -> tunggu bar berikutnya"
            return base
        price = quote

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

    sl_dist = atr_val * atr_mult
    tp_dist = sl_dist * rr

    # Batas risiko: di akun kecil, lot minimum 0.01 tidak bisa diperkecil.
    # Kalau jarak SL (=$ risiko per 0.01 lot) melebihi batas, JANGAN kirim
    # sinyal -- lebih baik tidak trading daripada risiko tak masuk akal.
    if max_risk_usd is not None and sl_dist > max_risk_usd:
        base["risk_pct"] = round(sl_dist / equity * 100, 1)
        base["reason"] = (
            f"Volatilitas tinggi: risiko ${sl_dist:.0f}/trade (~{sl_dist/equity*100:.0f}% akun) "
            f"> batas ${max_risk_usd:.0f} -> skip demi keamanan"
        )
        return base

    zone = round(0.15 * atr_val, 2)            # toleransi zona entry (~0.15 ATR)
    zlow = round(price - zone, 2)
    zhigh = round(price + zone, 2)
    valid_minutes = _TF_MINUTES.get(entry_interval, 30)
    base.update({
        "sl_pips": round(sl_dist / PIP),
        "tp_pips": round(tp_dist / PIP),
        "risk_per_001": round(sl_dist, 2),     # $ rugi per 0.01 lot bila kena SL
        "reward_per_001": round(tp_dist, 2),   # $ untung per 0.01 lot bila kena TP
        "risk_pct": round(sl_dist / equity * 100, 1),
        "entry_type": "market",
        "entry_zone_low": zlow, "entry_zone_high": zhigh,
        "valid_minutes": valid_minutes,
        "timing": (
            f"Masuk SEKARANG (market) di zona {zlow}-{zhigh}. "
            f"Sinyal fresh, valid ~{valid_minutes} mnt (sampai bar {entry_interval} berikutnya)."
        ),
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

    # Keyakinan: pakai keselarasan + kekuatan sentimen sbg booster.
    side = base["signal"]
    aligned = (
        (side == "buy" and sentiment_bias == "long")
        or (side == "sell" and sentiment_bias == "short")
    )
    if aligned and abs(sentiment_score) >= SENT_STRONG:
        level, label, stars = 3, "Kuat", "⭐⭐⭐"
    elif aligned:
        level, label, stars = 2, "Sedang", "⭐⭐"
    else:
        level, label, stars = 1, "Lemah (teknikal saja)", "⭐"
    base.update({"confidence": label, "confidence_level": level, "confidence_stars": stars})

    # Gate sentimen: sinyal MELAWAN sentimen tidak dikirim, tapi tetap
    # dikembalikan sbg "bayangan" (shadow) lengkap dgn SL/TP -- supaya bisa
    # dilacak: apakah blokiran sentimen menyelamatkan atau merugikan.
    if use_sentiment and sentiment_bias in ("long", "short") and not aligned:
        base["shadow_side"] = side
        base["signal"] = "none"
        base["reason"] = (
            f"Setup {side.upper()} tapi sentimen {sentiment_bias} -> diblokir "
            f"(dicatat sbg bayangan utk uji akurasi gate)"
        )
    return base
