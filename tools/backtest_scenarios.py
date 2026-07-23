"""Backtest 3 skenario SL/TP (Test A/B/C) untuk strategi teknikal XAUUSD M15.

Aturan entry DIPERTAHANKAN sama persis dgn sistem live (profil "harian"):
  tren H1 EMA21/50 + pullback RSI(14) M15 di zona [rsi_lo, rsi_hi], sesi
  London+NY, candle CLOSED (indikator pakai bar [-2], entry di close bar
  yang baru selesai). NON-OVERLAP: 1 posisi selesai dulu (maks 1 sinyal/candle).

Yang BEDA antar skenario HANYA SL & TP (dalam pip). Semua asumsi lain sama.
Sentiment gate TIDAK di-backtest di sini (butuh arsip berita historis
ber-timestamp yang tidak tersedia) -> ini grup "ALL TECHNICAL". Perbandingan
efek sentiment gate dilakukan LIVE via shadow tracking.

Config pip/point TIDAK di-hardcode -> lihat PIPCFG (bisa diubah saat data
broker riil tersedia).
"""
from __future__ import annotations

import os
import statistics
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data_service"))

from fetchers.signal_engine import ema_series, rsi_series  # noqa: E402
from fetchers.tracker import parse_utc  # noqa: E402

KEY = os.getenv("TWELVEDATA_API_KEY", "096b399350f5428d8a35f9538b7801cf")


# --- Config broker (JANGAN hardcode; ganti saat data broker riil ada) ------
@dataclass
class PipConfig:
    pip_price: float = 0.10       # 1 pip XAUUSD = gerak harga $0.10
    contract_size: float = 100.0  # 100 oz per 1.0 lot
    lot_size: float = 0.01        # lot minimum
    # USD per pip per lot_size: 0.01 lot=1oz, $1 gerak=$1 -> $0.10/pip
    usd_per_pip: float = 0.10
    spread_pips: float = 3.0      # asumsi spread emas (~$0.30)
    slippage_pips: float = 1.0    # asumsi slippage per sisi


PIPCFG = PipConfig()

# Aturan teknikal (identik sistem live profil "harian")
RSI_LO, RSI_HI = 30.0, 70.0
EMA_FAST, EMA_SLOW = 21, 50
SESSION = (5, 21)          # jam UTC boleh entry
LOOKAHEAD = 400            # maks bar M15 utk resolve (SL 30pip bisa lama)
EQUITY = 100.0


def fetch_paged(interval: str, pages: int) -> list[dict]:
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
        page = [{"dt": v["datetime"], "o": float(v["open"]), "h": float(v["high"]),
                 "l": float(v["low"]), "c": float(v["close"])} for v in vals]
        out = page + out
        end = page[0]["dt"]
    seen, ded = set(), []
    for b in out:
        if b["dt"] not in seen:
            seen.add(b["dt"])
            ded.append(b)
    ded.sort(key=lambda b: b["dt"])
    return ded


def session_of(hour: int) -> str:
    if 0 <= hour < 7:
        return "Asia"
    if 7 <= hour < 13:
        return "London"
    return "NewYork"


def map_trend(m15: list[dict], h1: list[dict]) -> list[int]:
    """Tren H1 (EMA21/50) bar TERTUTUP terakhir utk tiap bar M15 (no look-ahead)."""
    c = [b["c"] for b in h1]
    ef, es = ema_series(c, EMA_FAST), ema_series(c, EMA_SLOW)
    trend = [1 if f > s else -1 if f < s else 0 for f, s in zip(ef, es)]
    ht = [parse_utc(b["dt"]) for b in h1]
    out, j = [], 0
    for b in m15:
        t = parse_utc(b["dt"])
        while j + 1 < len(ht) and ht[j + 1] + timedelta(hours=1) <= t:
            j += 1
        out.append(trend[j] if ht[j] + timedelta(hours=1) <= t and j >= EMA_SLOW else 0)
    return out


def run_scenario(m15, trend, rsi, sl_pips, tp_pips, cfg: PipConfig):
    sl_d = sl_pips * cfg.pip_price
    tp_d = tp_pips * cfg.pip_price
    cost_d = (cfg.spread_pips + 2 * cfg.slippage_pips) * cfg.pip_price  # round trip
    risk_usd = sl_pips * cfg.usd_per_pip
    trades = []
    until = -1
    for i in range(EMA_SLOW * 4 + 5, len(m15) - 1):
        if i <= until:
            continue
        r = rsi[i - 1]                      # RSI bar TERTUTUP (no look-ahead)
        tr = trend[i]
        if r is None or tr == 0 or not (RSI_LO <= r <= RSI_HI):
            continue
        t = parse_utc(m15[i]["dt"])
        if t.weekday() >= 5 or not (SESSION[0] <= t.hour < SESSION[1]):
            continue
        side = tr                            # ikut tren H1
        entry = m15[i - 1]["c"]              # entry di CLOSE candle yang selesai
        # sisipkan biaya di entry (harga masuk lebih buruk)
        if side == 1:
            sl = entry - sl_d
            tp = entry + tp_d
        else:
            sl = entry + sl_d
            tp = entry - tp_d
        mae = mfe = 0.0
        outcome = None
        for j in range(i, min(i + LOOKAHEAD, len(m15))):
            hi, lo = m15[j]["h"], m15[j]["l"]
            adv = (entry - lo) if side == 1 else (hi - entry)   # gerak lawan
            fav = (hi - entry) if side == 1 else (entry - lo)   # gerak searah
            mae = max(mae, adv)
            mfe = max(mfe, fav)
            hit_sl = lo <= sl if side == 1 else hi >= sl
            hit_tp = hi >= tp if side == 1 else lo <= tp
            if hit_sl:                        # konservatif: dua-duanya kena=loss
                outcome = ("loss", j)
                break
            if hit_tp:
                outcome = ("win", j)
                break
        if outcome is None:
            break
        kind, j = outcome
        until = j
        # cek "SL dulu baru arah TP tercapai" (apakah SL kekecilan)
        sl_then_tp = False
        if kind == "loss":
            for k in range(j, min(j + LOOKAHEAD, len(m15))):
                if (side == 1 and m15[k]["h"] >= tp) or (side == -1 and m15[k]["l"] <= tp):
                    sl_then_tp = True
                    break
        r_mult = 2.0 if kind == "win" else -1.0
        usd = (tp_d - cost_d if kind == "win" else -(sl_d + cost_d)) * cfg.usd_per_pip / cfg.pip_price
        trades.append({
            "side": "BUY" if side == 1 else "SELL", "kind": kind,
            "entry_t": m15[i]["dt"], "exit_t": m15[j]["dt"], "bars": j - i,
            "r": r_mult, "usd": round(usd, 2), "risk_usd": risk_usd,
            "session": session_of(t.hour),
            "mae_r": round(mae / sl_d, 2), "mfe_r": round(mfe / sl_d, 2),
            "sl_then_tp": sl_then_tp,
            "near_tp_then_sl": kind == "loss" and mfe >= 0.8 * tp_d,
        })
    return trades, risk_usd


def metrics(trades, risk_usd, sl_pips, tp_pips, days):
    n = len(trades)
    wins = [t for t in trades if t["kind"] == "win"]
    losses = [t for t in trades if t["kind"] == "loss"]
    wr = len(wins) / n * 100 if n else 0
    net_r = sum(t["r"] for t in trades)
    net_usd = sum(t["usd"] for t in trades)
    gp = sum(t["usd"] for t in wins)
    gl = -sum(t["usd"] for t in losses)
    pf = gp / gl if gl else float("inf")
    exp_r = net_r / n if n else 0
    # equity curve (R) utk drawdown & streak
    eq, peak, dd_r = 0.0, 0.0, 0.0
    cw = cl = mcw = mcl = 0
    for t in trades:
        eq += t["r"]
        peak = max(peak, eq)
        dd_r = min(dd_r, eq - peak)
        if t["kind"] == "win":
            cw, cl = cw + 1, 0
            mcw = max(mcw, cw)
        else:
            cl, cw = cl + 1, 0
            mcl = max(mcl, cl)
    bars = [t["bars"] for t in trades]

    def sub(items):
        if not items:
            return "0 (—)"
        w = sum(1 for t in items if t["kind"] == "win")
        return f"{len(items)} (WR {w/len(items)*100:.0f}%, {sum(t['r'] for t in items):+.0f}R)"

    print(f"  Total trade dieksekusi : {n}  ({n/days:.2f}/hari)")
    print(f"  Win / Loss             : {len(wins)} / {len(losses)}")
    print(f"  Win rate               : {wr:.1f}%")
    print(f"  Net R                  : {net_r:+.1f}R")
    print(f"  Net profit simulasi    : ${net_usd:+.2f}  (dari modal ${EQUITY:.0f})")
    print(f"  Gross profit / loss    : ${gp:.2f} / ${gl:.2f}")
    print(f"  Profit factor          : {pf:.2f}")
    print(f"  Expectancy / trade     : {exp_r:+.3f}R  (${net_usd/n if n else 0:+.2f})")
    print(f"  Avg win / avg loss     : ${statistics.mean([t['usd'] for t in wins]) if wins else 0:+.2f}"
          f" / ${statistics.mean([t['usd'] for t in losses]) if losses else 0:+.2f}")
    print(f"  Max drawdown           : {dd_r:.1f}R  (${dd_r*risk_usd:.2f})")
    print(f"  Max consec win / loss  : {mcw} / {mcl}")
    print(f"  Holding time avg/median: {statistics.mean(bars)*15/60:.1f}j / "
          f"{statistics.median(bars)*15/60:.1f}j" if bars else "  Holding: —")
    print(f"  MAE avg / MFE avg       : {statistics.mean([t['mae_r'] for t in trades]):.2f}R"
          f" / {statistics.mean([t['mfe_r'] for t in trades]):.2f}R" if n else "")
    print(f"  Risiko per trade        : ${risk_usd:.2f}  (~{risk_usd/EQUITY*100:.0f}% akun)")
    print(f"  BUY  : {sub([t for t in trades if t['side']=='BUY'])}")
    print(f"  SELL : {sub([t for t in trades if t['side']=='SELL'])}")
    for s in ("Asia", "London", "NewYork"):
        print(f"  Sesi {s:8s}: {sub([t for t in trades if t['session']==s])}")
    print(f"  Kena SL lalu harga capai arah TP : {sum(1 for t in trades if t['sl_then_tp'])} "
          f"(SL mungkin kekecilan)")
    print(f"  Hampir TP (MFE>=80%) lalu balik SL: {sum(1 for t in losses if t['near_tp_then_sl'])}")
    return {"wr": wr, "net_r": net_r, "net_usd": net_usd, "pf": pf,
            "exp_r": exp_r, "dd_r": dd_r, "n": n, "risk": risk_usd}


def main():
    print("Mengambil data...")
    m15 = fetch_paged("15min", 4)
    h1 = fetch_paged("1h", 2)
    days = (parse_utc(m15[-1]["dt"]) - parse_utc(m15[0]["dt"])).days or 1
    print(f"Data M15: {len(m15)} candle ({m15[0]['dt'][:10]} .. {m15[-1]['dt'][:10]}, {days} hari)")
    print(f"Asumsi: pip={PIPCFG.pip_price} price, spread {PIPCFG.spread_pips}p + "
          f"slippage {PIPCFG.slippage_pips}p/sisi, lot {PIPCFG.lot_size}\n")

    trend = map_trend(m15, h1)
    rsi = rsi_series([b["c"] for b in m15])

    scenarios = [("A · SL30/TP60 (risk ~3%)", 30, 60),
                 ("B · SL50/TP100 (risk ~5%)", 50, 100),
                 ("C · SL100/TP200 (risk ~10%)", 100, 200)]
    summary = []
    for name, slp, tpp in scenarios:
        print("=" * 60)
        print(f"TEST {name}   [RR 1:2]")
        print("=" * 60)
        trades, risk = run_scenario(m15, trend, rsi, slp, tpp, PIPCFG)
        summary.append((name, metrics(trades, risk, slp, tpp, days)))
        print()

    print("=" * 60)
    print("RINGKASAN PERBANDINGAN")
    print("=" * 60)
    print(f"{'skenario':30} {'trade':>6} {'WR%':>6} {'PF':>5} {'ExpR':>7} {'netR':>7} {'net$':>8} {'DD_R':>7}")
    for name, m in summary:
        print(f"{name:30} {m['n']:>6} {m['wr']:>6.1f} {m['pf']:>5.2f} {m['exp_r']:>+7.3f} "
              f"{m['net_r']:>+7.0f} {m['net_usd']:>+8.1f} {m['dd_r']:>7.1f}")


if __name__ == "__main__":
    main()
