"""Backtest varian strategi XAUUSD di data historis Twelve Data.

Tujuan: memilih konfigurasi dengan WINRATE tinggi + frekuensi harian + tetap
profit (net R & $ positif), sebelum dipakai live. Termasuk biaya spread.

Pakai: TWELVEDATA_API_KEY di env, lalu `python tools/backtest.py`.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data_service"))

from fetchers.signal_engine import atr_series, ema_series, fetch_series, rsi_series  # noqa: E402
from fetchers.tracker import parse_utc  # noqa: E402

SPREAD = 0.40  # $ biaya bolak-balik per trade (spread emas konservatif)


def bar_time(b: dict) -> datetime:
    return parse_utc(str(b["datetime"]))


def run_variant(
    entry_bars: list[dict],
    trend_bars: list[dict],
    trend_hours: float,
    name: str,
    rsi_lo: float,
    rsi_hi: float,
    sl_mult: float,
    tp_mult: float,
    ema_fast: int = 50,
    ema_slow: int = 200,
) -> dict:
    closes = [b["close"] for b in entry_bars]
    highs = [b["high"] for b in entry_bars]
    lows = [b["low"] for b in entry_bars]
    rsi = rsi_series(closes)
    atr = atr_series(highs, lows, closes)

    t_closes = [b["close"] for b in trend_bars]
    t_times = [bar_time(b) for b in trend_bars]
    ema_f = ema_series(t_closes, ema_fast)
    ema_s = ema_series(t_closes, ema_slow)

    # indeks bar tren TERTUTUP terakhir sebelum waktu t
    ti = 0

    def trend_at(t: datetime) -> int:
        nonlocal ti
        while ti + 1 < len(t_times) and t_times[ti + 1] + timedelta(hours=trend_hours) <= t:
            ti += 1
        if t_times[ti] + timedelta(hours=trend_hours) > t or ti < ema_slow:
            return 0
        return 1 if ema_f[ti] > ema_s[ti] else -1 if ema_f[ti] < ema_s[ti] else 0

    wins = losses = 0
    pnl = 0.0
    in_pos_until = -1
    days = (bar_time(entry_bars[-1]) - bar_time(entry_bars[0])).days or 1

    for i in range(30, len(entry_bars) - 1):
        if i <= in_pos_until:
            continue
        r, a = rsi[i - 1], atr[i - 1]
        if r is None or a is None or a <= 0:
            continue
        tr = trend_at(bar_time(entry_bars[i]))
        if tr == 0 or not (rsi_lo <= r <= rsi_hi):
            continue
        side = "buy" if tr == 1 else "sell"
        price = closes[i - 1]
        sl_d, tp_d = a * sl_mult, a * tp_mult
        sl = price - sl_d if side == "buy" else price + sl_d
        tp = price + tp_d if side == "buy" else price - tp_d

        outcome = None
        for j in range(i, len(entry_bars)):
            hi, lo = highs[j], lows[j]
            hit_sl = lo <= sl if side == "buy" else hi >= sl
            hit_tp = hi >= tp if side == "buy" else lo <= tp
            if hit_sl:            # konservatif: dua-duanya kena -> loss
                outcome = ("loss", j)
                break
            if hit_tp:
                outcome = ("win", j)
                break
        if outcome is None:
            break
        kind, j = outcome
        in_pos_until = j
        if kind == "win":
            wins += 1
            pnl += tp_d - SPREAD
        else:
            losses += 1
            pnl -= sl_d + SPREAD

    total = wins + losses
    wr = wins / total * 100 if total else 0.0
    rr = tp_mult / sl_mult
    exp_r = (wr / 100 * rr - (1 - wr / 100)) if total else 0.0
    return {
        "name": name, "trades": total, "per_day": round(total / days, 2),
        "winrate": round(wr, 1), "rr": round(rr, 2),
        "net_usd_001": round(pnl, 1), "exp_r": round(exp_r, 3),
    }


def main() -> None:
    key = os.getenv("TWELVEDATA_API_KEY", "")
    if not key:
        sys.exit("set TWELVEDATA_API_KEY dulu")

    m15 = fetch_series("XAU/USD", "15min", 5000, key)
    m30 = fetch_series("XAU/USD", "30min", 5000, key)
    h1 = fetch_series("XAU/USD", "1h", 5000, key)
    h4 = fetch_series("XAU/USD", "4h", 3000, key)
    print(f"data: M15={len(m15)} M30={len(m30)} H1={len(h1)} H4={len(h4)} | "
          f"{m30[0]['datetime']} s/d {m30[-1]['datetime']}\n")

    variants = [
        # (entry, trend, trend_hours, nama, rsi_lo, rsi_hi, sl_mult, tp_mult)
        # --- RR 1:3 (gaya lama) ---
        (m30, h4, 4, "M30/H4 RR1:3 (saat ini)", 40, 60, 1.5, 4.5),
        (m30, h4, 4, "M30/H4 RR1:3 longgar", 35, 65, 1.5, 4.5),
        (m15, h1, 1, "M15/H1 RR1:3 longgar", 35, 65, 1.5, 4.5),
        # --- RR 1:2 ---
        (m30, h4, 4, "M30/H4 RR1:2", 40, 60, 1.5, 3.0),
        (m30, h4, 4, "M30/H4 RR1:2 longgar", 35, 65, 1.5, 3.0),
        (m15, h1, 1, "M15/H1 RR1:2 longgar", 35, 65, 1.5, 3.0),
        (m15, h4, 4, "M15/H4 RR1:2 longgar", 35, 65, 1.5, 3.0),
        # --- RR 1:1 ---
        (m30, h4, 4, "M30/H4 RR1:1", 35, 65, 1.5, 1.5),
        (m15, h1, 1, "M15/H1 RR1:1", 35, 65, 1.5, 1.5),
        # --- TP cepat (winrate tinggi) ---
        (m30, h4, 4, "M30/H4 fastTP 1:0.5", 35, 65, 1.5, 0.75),
        (m30, h4, 4, "M30/H4 fastTP 1:0.33", 35, 65, 1.5, 0.5),
        (m15, h1, 1, "M15/H1 fastTP 1:0.5", 35, 65, 1.5, 0.75),
        (m15, h1, 1, "M15/H1 fastTP 1:0.33", 35, 65, 1.5, 0.5),
        (m15, h1, 1, "M15/H1 fastTP SL2 1:0.25", 35, 65, 2.0, 0.5),
        (m15, h4, 4, "M15/H4 fastTP 1:0.5", 35, 65, 1.5, 0.75),
        (m15, h4, 4, "M15/H4 fastTP 1:0.33", 35, 65, 1.5, 0.5),
        (m15, h4, 4, "M15/H4 fastTP SL2 1:0.25", 30, 70, 2.0, 0.5),
    ]
    print(f"{'varian':28} {'trade':>5} {'/hari':>5} {'WR%':>6} {'RR':>5} {'net$@0.01':>9} {'ExpR':>6}")
    for ebars, tbars, th, name, lo, hi, slm, tpm in variants:
        r = run_variant(ebars, tbars, th, name, lo, hi, slm, tpm)
        print(f"{r['name']:28} {r['trades']:>5} {r['per_day']:>5} {r['winrate']:>6} "
              f"{r['rr']:>5} {r['net_usd_001']:>9} {r['exp_r']:>6}")


if __name__ == "__main__":
    main()
