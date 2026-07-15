"""EKSPERIMEN (terpisah dari sistem live): XGBoost meta-filter sinyal XAUUSD.

Ide: model memprediksi peluang "TP tersentuh sebelum SL" untuk setup
trend-following M15 (aturan mirip profil harian). Sinyal hanya "layak"
bila probabilitas model >= ambang. Validasi WALK-FORWARD (split waktu,
tanpa kebocoran masa depan).

Output: tabel per ambang -> winrate, jumlah trade/hari, ekspektasi R.
Model TIDAK diintegrasikan ke sistem live (sesuai keputusan user).
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import numpy as np
from xgboost import XGBClassifier

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data_service"))

from fetchers.signal_engine import atr_series, ema_series, rsi_series  # noqa: E402
from fetchers.tracker import parse_utc  # noqa: E402

KEY = os.getenv("TWELVEDATA_API_KEY", "096b399350f5428d8a35f9538b7801cf")
SL_MULT, RR = 1.2, 2.0
LOOKAHEAD = 192          # maks 2 hari (bar M15) utk resolve TP/SL
SPREAD_R = 0.05          # biaya ~5% dari 1R


def fetch_paged(interval: str, pages: int) -> list[dict]:
    """Ambil data historis lebih panjang dgn paging end_date."""
    import httpx
    out: list[dict] = []
    end = None
    for _ in range(pages):
        params = {"symbol": "XAU/USD", "interval": interval, "outputsize": "5000",
                  "order": "ASC", "apikey": KEY, "format": "JSON", "timezone": "UTC"}
        if end:
            params["end_date"] = end
        r = httpx.get("https://api.twelvedata.com/time_series", params=params, timeout=30)
        vals = r.json().get("values") or []
        if not vals:
            break
        page = [{"datetime": v["datetime"], "open": float(v["open"]),
                 "high": float(v["high"]), "low": float(v["low"]),
                 "close": float(v["close"])} for v in vals]
        out = page + out
        end = page[0]["datetime"]
    # dedup + urut
    seen, ded = set(), []
    for b in out:
        if b["datetime"] not in seen:
            seen.add(b["datetime"])
            ded.append(b)
    ded.sort(key=lambda b: b["datetime"])
    return ded


def trend_series(bars: list[dict], fast: int, slow: int) -> list[int]:
    c = [b["close"] for b in bars]
    ef, es = ema_series(c, fast), ema_series(c, slow)
    return [1 if f > s else -1 if f < s else 0 for f, s in zip(ef, es)]


def map_to(entry_bars: list[dict], big_bars: list[dict], big_hours: float,
           values: list) -> list:
    """Nilai bar besar TERTUTUP terakhir utk tiap bar entry."""
    from datetime import timedelta
    bt = [parse_utc(b["datetime"]) for b in big_bars]
    et = [parse_utc(b["datetime"]) for b in entry_bars]
    out, j = [], 0
    for t in et:
        while j + 1 < len(bt) and bt[j + 1] + timedelta(hours=big_hours) <= t:
            j += 1
        out.append(values[j] if bt[j] + timedelta(hours=big_hours) <= t else None)
    return out


def main() -> None:
    m15 = fetch_paged("15min", 4)
    h1 = fetch_paged("1h", 2)
    h4 = fetch_paged("4h", 1)
    print(f"data: M15={len(m15)} ({m15[0]['datetime'][:10]}..{m15[-1]['datetime'][:10]}) "
          f"H1={len(h1)} H4={len(h4)}")

    c = [b["close"] for b in m15]
    hi = [b["high"] for b in m15]
    lo = [b["low"] for b in m15]
    rsi = rsi_series(c)
    atr = atr_series(hi, lo, c)
    tr_h1 = map_to(m15, h1, 1, trend_series(h1, 21, 50))
    tr_h4 = map_to(m15, h4, 4, trend_series(h4, 50, 200))
    times = [parse_utc(b["datetime"]) for b in m15]

    X, y, t_idx = [], [], []
    in_pos_until = -1
    for i in range(220, len(m15) - LOOKAHEAD):
        if i <= in_pos_until:                 # NON-OVERLAP: 1 posisi selesai dulu
            continue
        r, a = rsi[i - 1], atr[i - 1]
        th1, th4 = tr_h1[i], tr_h4[i]
        if r is None or a is None or a <= 0 or th1 in (None, 0):
            continue
        t = times[i]
        if not (6 <= t.hour < 20) or t.weekday() >= 5:
            continue
        if not (35 <= r <= 65):          # setup mirip profil harian
            continue
        side = 1 if th1 == 1 else -1      # ikut tren H1
        price = c[i - 1]
        sl_d = a * SL_MULT
        tp_d = sl_d * RR
        sl = price - side * sl_d
        tp = price + side * tp_d
        lab = None
        for j in range(i, min(i + LOOKAHEAD, len(m15))):
            hit_sl = lo[j] <= sl if side == 1 else hi[j] >= sl
            hit_tp = hi[j] >= tp if side == 1 else lo[j] <= tp
            if hit_sl:
                lab, in_pos_until = 0, j
                break
            if hit_tp:
                lab, in_pos_until = 1, j
                break
        if lab is None:
            continue
        ret1 = (c[i - 1] - c[i - 2]) / a
        ret4 = (c[i - 1] - c[i - 5]) / a
        ret16 = (c[i - 1] - c[i - 17]) / a
        rng16 = max(hi[i - 17:i - 1]) - min(lo[i - 17:i - 1])
        pos16 = (c[i - 1] - min(lo[i - 17:i - 1])) / rng16 if rng16 > 0 else 0.5
        atr_mean = float(np.mean([x for x in atr[i - 33:i - 1] if x])) or a
        atr_hist = [x for x in atr[i - 201:i - 1] if x]
        atr_pct = sum(1 for x in atr_hist if x < a) / len(atr_hist) if atr_hist else 0.5
        streak = 0
        for k in range(i - 1, max(i - 9, 1), -1):   # bar searah beruntun
            if (c[k] - c[k - 1]) * side > 0:
                streak += 1
            else:
                break
        body = abs(c[i - 1] - m15[i - 1]["open"])
        rng_b = hi[i - 1] - lo[i - 1]
        X.append([
            r, (r - 50) * side, a / price * 1000, a / atr_mean, atr_pct,
            th1, th4 or 0, int(th1 == (th4 or 0)),
            ret1 * side, ret4 * side, ret16 * side, pos16,
            streak, body / rng_b if rng_b > 0 else 0.5,
            math.sin(2 * math.pi * t.hour / 24), math.cos(2 * math.pi * t.hour / 24),
            t.weekday(),
        ])
        y.append(lab)
        t_idx.append(i)

    X = np.array(X)
    y = np.array(y)
    n = len(y)
    days = (times[-1] - times[0]).days or 1
    print(f"dataset: {n} setup ({n/days:.1f}/hari) | baseline WR={y.mean()*100:.1f}% "
          f"| ExpR baseline={(y.mean()*RR-(1-y.mean())-SPREAD_R):.3f}\n")

    # Walk-forward: 4 fold (train membesar, test blok berikutnya)
    folds = 4
    edges = [int(n * k / (folds + 1)) for k in range(1, folds + 2)]
    thresholds = [0.5, 0.6, 0.7, 0.8]
    agg = {th: [0, 0] for th in thresholds}   # th -> [win, total]
    for f in range(folds):
        tr_end, te_end = edges[f], edges[f + 1]
        model = XGBClassifier(n_estimators=300, max_depth=4, learning_rate=0.05,
                              subsample=0.8, colsample_bytree=0.8,
                              eval_metric="logloss", verbosity=0)
        model.fit(X[:tr_end], y[:tr_end])
        proba = model.predict_proba(X[tr_end:te_end])[:, 1]
        yt = y[tr_end:te_end]
        for th in thresholds:
            m = proba >= th
            agg[th][0] += int(yt[m].sum())
            agg[th][1] += int(m.sum())

    total_test = edges[-1] - edges[0]
    print(f"{'ambang':>7} {'WR%':>6} {'trade':>6} {'%dipakai':>8} {'/hari':>6} {'ExpR':>6}")
    base_wr = y[edges[0]:].mean()
    print(f"{'tanpa':>7} {base_wr*100:>6.1f} {total_test:>6} {'100%':>8} "
          f"{total_test/(days*4/5):>6.1f} {base_wr*RR-(1-base_wr)-SPREAD_R:>6.3f}")
    for th in thresholds:
        w, tot = agg[th]
        if tot == 0:
            print(f"{th:>7} {'-':>6} {0:>6}")
            continue
        wr = w / tot
        expr = wr * RR - (1 - wr) - SPREAD_R
        print(f"{th:>7} {wr*100:>6.1f} {tot:>6} {tot/total_test*100:>7.0f}% "
              f"{tot/(days*4/5):>6.2f} {expr:>6.3f}")


if __name__ == "__main__":
    main()
