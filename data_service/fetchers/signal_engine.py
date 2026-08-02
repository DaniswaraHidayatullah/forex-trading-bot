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
    # Hasil backtest ronde-2 (Mei-Jul 2026): tren H1 EMA21/50 (lebih responsif),
    # sesi London/NY 06-20 UTC, SL 1.2 ATR + RR 1:2 -> ~4 sinyal/hari,
    # WR ~40%, ekspektasi/trade terbaik (0.216R) & risiko lolos batas $12.
    "harian": {
        "label": "Harian", "trend": "1h", "entry": "15min",
        "ema_fast": 21, "ema_slow": 50,
        "atr_mult": 1.2, "rr": 2.0, "rsi_lo": 30.0, "rsi_hi": 70.0,
        "session": (5, 21),  # jam UTC boleh entry (diperlebar)
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

# --- Profil BTCUSD (domain terpisah: 24/7, volatil, teknikal-only) -------
# BTC bergerak ribuan $; SL/TP dalam $ langsung. RSI band lebih lebar (tren
# kuat). spread_pad besar (spread crypto Exness bisa puluhan $). market="crypto"
# -> tidak kena weekend guard emas. Tanpa sesi (24/7). Sentimen belum dipakai.
BTC_PROFILES: dict[str, dict[str, Any]] = {
    "btc": {
        "label": "BTC", "trend": "4h", "entry": "1h",
        "ema_fast": 21, "ema_slow": 50,
        # RR 1:1.5 (bukan 1:2 spt emas): BTC choppy/mean-revert, sering balik
        # arah di zona 1.5-2R (MFE 2 trade pertama: 1.58R & 1.96R lalu balik ke SL).
        "atr_mult": 1.5, "rr": 1.5, "rsi_lo": 35.0, "rsi_hi": 65.0,
        "spread_pad": 20.0, "market": "crypto",
        "hold": "jam s/d beberapa hari",
    },
}

PIP = 0.10          # 1 pip emas = $0.10 gerak harga
SENT_STRONG = 0.30  # |skor sentimen| >= ini dianggap kuat
SPREAD_PAD = 0.3    # spread XAUUSD Exness Standard (~0.3); profil bisa override


def market_open(now: datetime | None = None, market_type: str = "gold") -> bool:
    """Pasar buka? Crypto (BTC) = 24/7 selalu True. Emas/forex tutup:
    Jumat ~21:00 UTC s/d Minggu ~22:00 UTC (saat tutup data basi -> skip).
    """
    if market_type == "crypto":
        return True
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
    pip_price: float = 0.10,
    usd_per_pip: float = 0.10,
    lot: float | None = None,
    version: str = "v1",
    display_symbol: str | None = None,
    market_type: str = "gold",
    profiles: dict[str, dict[str, Any]] | None = None,
    fetch_fn: Callable[[str, int], list[dict[str, float]]] | None = None,
) -> dict[str, Any]:
    """Bangun sinyal untuk satu profil. Default domain EMAS; untuk BTC oper
    profiles=BTC_PROFILES, market_type="crypto", display_symbol="BTCUSD".

    Selalu kembalikan dict (tidak pernah lempar). fetch_fn(interval, size)
    bisa di-inject untuk caching harga (hemat kuota API).
    """
    registry = profiles or PROFILES
    prof = registry.get(profile) or next(iter(registry.values()))
    disp = display_symbol or symbol.replace("/", "")
    trend_interval = prof["trend"]
    entry_interval = prof["entry"]
    atr_mult = prof["atr_mult"]
    # Profil boleh membawa rr, zona RSI, EMA tren & sesi sendiri (hasil backtest).
    rr = float(prof.get("rr", rr))
    rsi_lo = float(prof.get("rsi_lo", rsi_lo))
    rsi_hi = float(prof.get("rsi_hi", rsi_hi))
    ema_fast = int(prof.get("ema_fast", ema_fast))
    ema_slow = int(prof.get("ema_slow", ema_slow))
    session = prof.get("session")

    base: dict[str, Any] = {
        "symbol": disp,
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
        "sentiment_available": sentiment_available, "version": version,
        "confidence": None, "confidence_level": 0, "confidence_stars": "",
        "momentum": "flat",
        "news_blocked": news_blocked, "risk_pct": None,
        "suggested_lot": lot if lot is not None else _lot_for_equity(equity),
        "price_source": "twelvedata:" + symbol,
        "time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    if not market_open(now_utc, market_type):
        base["reason"] = "Pasar TUTUP (weekend) -> tidak ada sinyal"
        return base
    if session:
        hr = (now_utc or datetime.now(timezone.utc)).hour
        if not (session[0] <= hr < session[1]):
            base["reason"] = (
                f"Di luar sesi trading profil ({session[0]:02d}-{session[1]:02d} UTC, "
                f"London+NY) -> tunggu"
            )
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

    # ANTI-SPIKE: SL minimal di LUAR wick 12 bar terakhir + buffer 0.5 ATR
    # + bantalan spread broker (SELL kena SL di harga ask; feed broker bisa
    # wick lebih jauh dari feed data). Kasus 14 Jul: SL 4061.7 disapu wick
    # broker 4062.11 sebelum harga jalan 200+ pips ke arah TP.
    spread_pad = float(prof.get("spread_pad", SPREAD_PAD))  # emas 0.6; BTC ~20
    sl_dist = atr_val * atr_mult
    recent_hi = max(m_high[-13:-1])
    recent_lo = min(m_low[-13:-1])
    if trend == 1:   # BUY: SL di bawah low terakhir
        wick_dist = (price - recent_lo) + 0.5 * atr_val + spread_pad
    else:            # SELL: SL di atas high terakhir
        wick_dist = (recent_hi - price) + 0.5 * atr_val + spread_pad
    if wick_dist > sl_dist:
        sl_dist = round(wick_dist, 2)
    # RR tetap dihormati: TP selalu rr x SL (ikut melebar bersama SL
    # anti-spike) -> hasil per trade selalu jelas (-1R / +rr R).
    tp_dist = sl_dist * rr

    # Batas risiko: di akun kecil, lot minimum 0.01 tidak bisa diperkecil.
    # Bandingkan $ risiko TER-SKALA broker (jarak SL x $/unit), bukan jarak
    # harga mentah -- penting utk BTC (jarak $ ratusan tapi $ risiko kecil).
    # Emas: skala 1.0 -> risk_usd == sl_dist (perilaku lama tak berubah).
    risk_usd = sl_dist / pip_price * usd_per_pip
    if max_risk_usd is not None and risk_usd > max_risk_usd:
        base["risk_pct"] = round(risk_usd / equity * 100, 1)
        base["reason"] = (
            f"Volatilitas tinggi: risiko ${risk_usd:.0f}/trade (~{risk_usd/equity*100:.0f}% akun) "
            f"> batas ${max_risk_usd:.0f} -> skip demi keamanan"
        )
        return base

    zone = round(0.15 * atr_val, 2)            # toleransi zona entry (~0.15 ATR)
    zlow = round(price - zone, 2)
    zhigh = round(price + zone, 2)
    valid_minutes = _TF_MINUTES.get(entry_interval, 30)
    sl_pips = sl_dist / pip_price
    tp_pips = tp_dist / pip_price
    base.update({
        "sl_pips": round(sl_pips),
        "tp_pips": round(tp_pips),
        "risk_per_001": round(sl_pips * usd_per_pip, 2),     # $ rugi (spesifik broker)
        "reward_per_001": round(tp_pips * usd_per_pip, 2),   # $ untung (spesifik broker)
        "risk_pct": round(sl_pips * usd_per_pip / equity * 100, 1),
        "entry_type": "market",
        "entry_zone_low": zlow, "entry_zone_high": zhigh,
        "valid_minutes": valid_minutes,
        "timing": (
            f"Masuk SEKARANG (market) di zona {zlow}-{zhigh}. "
            f"Sinyal fresh, valid ~{valid_minutes} mnt (sampai bar {entry_interval} berikutnya)."
        ),
    })
    if sentiment_bias == "flat":
        sent_txt = "berita netral saat ini"
    else:
        arah = "MENDUKUNG" if ((want_buy and sentiment_bias == "long")
                               or (want_sell and sentiment_bias == "short")) else "melawan"
        sent_txt = f"berita {arah} ({sentiment_bias} {sentiment_score:+.2f})"
    if want_buy:
        base.update({
            "signal": "buy",
            "sl": round(price - sl_dist, 2),
            "tp": round(price + tp_dist, 2),
            "reason": f"Uptrend {trend_interval} + RSI pullback {rsi_val:.0f} · {sent_txt}",
        })
    else:
        base.update({
            "signal": "sell",
            "sl": round(price + sl_dist, 2),
            "tp": round(price - tp_dist, 2),
            "reason": f"Downtrend {trend_interval} + RSI pullback {rsi_val:.0f} · {sent_txt}",
        })

    side = base["signal"]
    side_dir = 1 if side == "buy" else -1

    # v2 CONFIDENCE 3-LAPIS: teknikal (base) + sentimen + MOMENTUM harga.
    # Momentum = arah bar entry ~1 jam terakhir (bar tertutup) -> mencegah
    # kasus v1 "sentimen bullish tapi harga turun" naik jadi ⭐⭐⭐.
    mom_ref = m_close[-6] if len(m_close) >= 6 else m_close[0]
    mom_raw = m_close[-2] - mom_ref
    mom_dir = 1 if mom_raw > 0 else -1 if mom_raw < 0 else 0
    base["momentum"] = "up" if mom_dir == 1 else "down" if mom_dir == -1 else "flat"

    sent_agree = ((side == "buy" and sentiment_bias == "long")
                  or (side == "sell" and sentiment_bias == "short"))
    sent_confirm = sent_agree and abs(sentiment_score) >= SENT_STRONG
    mom_confirm = mom_dir == side_dir

    if version == "v1":
        # perilaku lama (sentimen sbg satu-satunya booster) -> untuk arsip
        if sent_confirm:
            level = 3
        elif sent_agree:
            level = 2
        else:
            level = 1
    else:
        # v2: butuh KONFIRMASI GANDA (sentimen kuat-searah + momentum searah)
        level = 1 + (1 if sent_confirm else 0) + (1 if mom_confirm else 0)
        level = min(3, level)

    conf_map = {
        3: ("⭐⭐⭐", "Kuat — teknikal + sentimen + momentum searah"),
        2: ("⭐⭐", "Sedang — sebagian konfirmasi"),
        1: ("⭐", "Standar — teknikal saja"),
    }
    stars, label = conf_map[level]
    base.update({"confidence": label, "confidence_level": level,
                 "confidence_stars": stars})
    # v2 TIDAK memblokir arah berdasar sentimen (terbukti tak andal di v1);
    # sentimen kini murni komponen keyakinan, bukan veto. Semua setup dikirim.
    return base
