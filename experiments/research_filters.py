"""RISET (offline, tidak menyentuh live): kenapa sinyal gagal & filter apa
yang menguatkan. Diagnosa ciri winner vs loser, lalu uji filter di 210 hari.

Base strategy = profil "harian" live: tren H1 EMA21/50, entry M15 RSI[30,70]
pullback, sesi 05-21 UTC, SL 1.2xATR M15, RR 1:2, non-overlap.
"""
from __future__ import annotations

import os
import statistics
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data_service"))

from fetchers.signal_engine import atr_series, ema_series, rsi_series  # noqa: E402
from fetchers.tracker import parse_utc  # noqa: E402

KEY = os.getenv("TWELVEDATA_API_KEY", "096b399350f5428d8a35f9538b7801cf")
SL_MULT, RR, SPREAD = 1.2, 2.0, 0.40


def fetch_paged(interval, pages):
    import httpx
    out, end = [], None
    for _ in range(pages):
        p = {"symbol": "XAU/USD", "interval": interval, "outputsize": "5000",
             "order": "ASC", "apikey": KEY, "format": "JSON", "timezone": "UTC"}
        if end:
            p["end_date"] = end
        v = httpx.get("https://api.twelvedata.com/time_series", params=p, timeout=30).json().get("values") or []
        if not v:
            break
        page = [{"dt": x["datetime"], "o": float(x["open"]), "h": float(x["high"]),
                 "l": float(x["low"]), "c": float(x["close"])} for x in v]
        out = page + out
        end = page[0]["dt"]
    seen, ded = set(), []
    for b in out:
        if b["dt"] not in seen:
            seen.add(b["dt"])
            ded.append(b)
    ded.sort(key=lambda b: b["dt"])
    return ded


def map_to(m15, big, hours, vals):
    bt = [parse_utc(b["dt"]) for b in big]
    out, j = [], 0
    for b in m15:
        t = parse_utc(b["dt"])
        while j + 1 < len(bt) and bt[j + 1] + timedelta(hours=hours) <= t:
            j += 1
        out.append(vals[j] if bt[j] + timedelta(hours=hours) <= t else None)
    return out


def build(m15, h1, h4):
    c = [b["c"] for b in m15]
    rsi = rsi_series(c)
    atr = atr_series([b["h"] for b in m15], [b["l"] for b in m15], c)
    ema21_m = ema_series(c, 21)
    # H1 tren + slope EMA21
    h1c = [b["c"] for b in h1]
    h1_ef, h1_es = ema_series(h1c, 21), ema_series(h1c, 50)
    h1_trend = [1 if f > s else -1 if f < s else 0 for f, s in zip(h1_ef, h1_es)]
    h1_slope = [h1_ef[i] - h1_ef[i - 3] if i >= 3 else 0 for i in range(len(h1_ef))]
    # H4 tren
    h4c = [b["c"] for b in h4]
    h4_ef, h4_es = ema_series(h4c, 50), ema_series(h4c, 200)
    h4_trend = [1 if f > s else -1 if f < s else 0 for f, s in zip(h4_ef, h4_es)]
    return {
        "rsi": rsi, "atr": atr, "ema21_m": ema21_m,
        "trend": map_to(m15, h1, 1, h1_trend),
        "h1_slope": map_to(m15, h1, 1, h1_slope),
        "h4_trend": map_to(m15, h4, 4, h4_trend),
    }


def gen_trades(m15, F, extra_filter=None):
    """Hasilkan trade + fitur di titik entry. extra_filter(feat)->bool."""
    rsi, atr, ema21_m = F["rsi"], F["atr"], F["ema21_m"]
    trend, h1_slope, h4_trend = F["trend"], F["h1_slope"], F["h4_trend"]
    trades, until = [], -1
    for i in range(210, len(m15) - 1):
        if i <= until:
            continue
        r, a = rsi[i - 1], atr[i - 1]
        tr = trend[i]
        if r is None or a is None or a <= 0 or tr in (None, 0):
            continue
        t = parse_utc(m15[i]["dt"])
        if t.weekday() >= 5 or not (5 <= t.hour < 21) or not (30 <= r <= 70):
            continue
        side = tr
        price = m15[i - 1]["c"]
        # FITUR di titik entry
        feat = {
            "side": side,
            "dist_ema": (price - ema21_m[i - 1]) / a * side,   # + = harga di atas EMA (searah)
            "rsi_slope": (rsi[i - 1] - rsi[i - 2]) * side if rsi[i - 2] else 0,
            "h4_agree": 1 if h4_trend[i] == side else 0,
            "h1_slope": (h1_slope[i] or 0) * side,
            "rsi": r,
        }
        if extra_filter and not extra_filter(feat):
            continue
        sl_d, tp_d = a * SL_MULT, a * RR * SL_MULT
        sl = price - side * sl_d
        tp = price + side * tp_d
        outcome = None
        for j in range(i, min(i + 400, len(m15))):
            hit_sl = m15[j]["l"] <= sl if side == 1 else m15[j]["h"] >= sl
            hit_tp = m15[j]["h"] >= tp if side == 1 else m15[j]["l"] <= tp
            if hit_sl:
                outcome = ("loss", j)
                break
            if hit_tp:
                outcome = ("win", j)
                break
        if outcome is None:
            break
        kind, j = outcome
        until = j
        feat["kind"] = kind
        trades.append(feat)
    return trades


def stats(trades):
    n = len(trades)
    if not n:
        return None
    w = sum(1 for t in trades if t["kind"] == "win")
    wr = w / n * 100
    net_r = w * RR - (n - w)
    gp, gl = w * RR, (n - w) * 1.0
    pf = gp / gl if gl else 0
    return {"n": n, "wr": wr, "pf": pf, "net_r": net_r, "exp": net_r / n}


def main():
    m15 = fetch_paged("15min", 4)
    h1 = fetch_paged("1h", 2)
    h4 = fetch_paged("4h", 1)
    days = (parse_utc(m15[-1]["dt"]) - parse_utc(m15[0]["dt"])).days or 1
    print(f"data {len(m15)} M15 ({days} hari)\n")
    F = build(m15, h1, h4)

    base = gen_trades(m15, F)
    win = [t for t in base if t["kind"] == "win"]
    los = [t for t in base if t["kind"] == "loss"]
    print("=== DIAGNOSA: ciri WINNER vs LOSER (rata-rata) ===")
    print(f"{'fitur':16} {'winner':>9} {'loser':>9}  interpretasi")
    for key, desc in [("dist_ema", "jarak dari EMA (ATR); + = extended"),
                      ("rsi_slope", "momentum RSI searah (+=mendukung)"),
                      ("h4_agree", "H4 setuju arah (1=ya)"),
                      ("h1_slope", "slope EMA21 H1 searah")]:
        mw = statistics.mean(t[key] for t in win)
        ml = statistics.mean(t[key] for t in los)
        print(f"{key:16} {mw:>9.3f} {ml:>9.3f}  {desc}")
    print()

    print("=== UJI FILTER (di seluruh 210 hari) ===")
    filters = {
        "BASELINE (tanpa filter)": None,
        "Momentum: RSI searah": lambda f: f["rsi_slope"] > 0,
        "Anti-extended <1.0 ATR": lambda f: f["dist_ema"] < 1.0,
        "Anti-extended <0.5 ATR": lambda f: f["dist_ema"] < 0.5,
        "EMA21 H1 slope searah": lambda f: f["h1_slope"] > 0,
        "H4 confluence": lambda f: f["h4_agree"] == 1,
        "Momentum + H4": lambda f: f["rsi_slope"] > 0 and f["h4_agree"] == 1,
        "Momentum + anti-ext<1": lambda f: f["rsi_slope"] > 0 and f["dist_ema"] < 1.0,
        "Mom + H4 + slope H1": lambda f: (f["rsi_slope"] > 0 and f["h4_agree"] == 1
                                          and f["h1_slope"] > 0),
    }
    print(f"{'filter':28} {'trade':>6} {'/hari':>5} {'WR%':>6} {'PF':>5} {'ExpR':>7} {'netR':>6}")
    for name, fn in filters.items():
        s = stats(gen_trades(m15, F, fn))
        if not s:
            print(f"{name:28} (0 trade)")
            continue
        print(f"{name:28} {s['n']:>6} {s['n']/days:>5.1f} {s['wr']:>6.1f} "
              f"{s['pf']:>5.2f} {s['exp']:>+7.3f} {s['net_r']:>+6.0f}")


if __name__ == "__main__":
    main()
