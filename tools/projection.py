"""Proyeksi Monte Carlo 30 hari untuk TrendEA di XAUUSD.

Memakai parameter sistem nyata:
  - Modal awal $100, lot 0.01 TETAP (karena < $400 -> belum naik tingkat)
  - XAUUSD: 0.01 lot ~ $1 P/L per $1 pergerakan harga emas
  - SL = 1.5 x ATR(H1); ATR disampel $2.5..$5.0  -> R (risiko) ~ $3.75..$7.5/trade
  - RR 1:3  -> TP = 3R
  - Winrate 39%
  - Layering 1+2: peluang 1 posisi 0.70, 2 posisi 0.20, 3 posisi 0.10.
    Saat menambah layer, SL posisi dasar digeser ke breakeven -> kalau setup
    GAGAL & sudah berlapis, hanya layer-nya yang rugi (dasar = 0), kalau BERHASIL
    semua layer kena TP (+3R per layer).
  - Biaya (spread+komisi) ~ $0.10 per posisi
  - ~1 setup/hari aktif x ~22 hari aktif (emas libur weekend) ~ 22 setup/bulan
    (sampel KECIL -> variance besar)
"""
from __future__ import annotations

import random
import statistics

SEED = 7
N_SIM = 30000
START = 100.0
ACTIVE_DAYS = 22
WINRATE = 0.39
SETUPS_PER_DAY_MEAN = 1.0
COST_PER_POS = 0.10
LAYER_DIST = {1: 0.70, 2: 0.20, 3: 0.10}
RUIN_LEVEL = 30.0     # dianggap "hancur / stop" bila ekuitas <= ini


def _poisson(lam: float) -> int:
    # Knuth
    import math
    L = math.exp(-lam)
    k = 0
    p = 1.0
    while True:
        k += 1
        p *= random.random()
        if p <= L:
            return k - 1


def _layers() -> int:
    r = random.random()
    c = 0.0
    for n, prob in LAYER_DIST.items():
        c += prob
        if r <= c:
            return n
    return 1


def sim_month_ideal() -> float:
    """Asumsi 39%/1:3 berlaku BERSIH tanpa friksi (batas atas / optimistis)."""
    bal = START
    for _ in range(ACTIVE_DAYS):
        for _ in range(_poisson(SETUPS_PER_DAY_MEAN)):
            if bal <= RUIN_LEVEL:
                return bal
            R = 1.5 * random.uniform(2.5, 5.0)
            n = _layers()
            cost = COST_PER_POS * n
            if random.random() < WINRATE:
                bal += 3.0 * R * n - cost
            else:
                loss_units = 1 if n == 1 else (n - 1)
                bal -= R * loss_units + cost
    return bal


def sim_month_real() -> float:
    """Versi REALISTIS: tambah friksi pasar emas + ketidakpastian winrate.

      - winrate aktual bulan ini disampel ~N(0.36, 0.07) (39% itu estimasi, out-
        of-sample sering lebih rendah & bervariasi)
      - menang efektif 2.7R (spread + TP 1:3 yg jauh tak selalu full); layer
        tambahan 2.3R (entry lebih tinggi, ruang lebih sempit)
      - kalah 1.1R (slippage emas saat news); dasar saat berlapis kena BE-whipsaw
        rata-rata -0.4R (bukan 0 sempurna)
    """
    w = min(0.60, max(0.15, random.gauss(0.36, 0.07)))
    bal = START
    for _ in range(ACTIVE_DAYS):
        for _ in range(_poisson(SETUPS_PER_DAY_MEAN)):
            if bal <= RUIN_LEVEL:
                return bal
            R = 1.5 * random.uniform(2.5, 5.0)
            n = _layers()
            cost = COST_PER_POS * n
            if random.random() < w:
                payoff = 2.7 * R + (n - 1) * 2.3 * R   # menang (efektif)
                bal += payoff - cost
            else:
                if n == 1:
                    loss = 1.1 * R
                else:
                    loss = 0.4 * R + (n - 1) * 1.1 * R  # dasar BE-whipsaw + layer
                bal -= loss + cost
    return bal


def _report(name: str, sim) -> None:
    ends = sorted(sim() for _ in range(N_SIM))

    def pct(p: float) -> float:
        return ends[min(len(ends) - 1, int(p * len(ends)))]

    prob_profit = sum(1 for e in ends if e > START) / len(ends)
    prob_ruin = sum(1 for e in ends if e <= RUIN_LEVEL) / len(ends)
    prob_2x = sum(1 for e in ends if e >= 2 * START) / len(ends)

    print(f"\n===== {name} =====")
    print(f"Median (p50)  : ${statistics.median(ends):.0f}")
    for p, label in [(0.10, "NEGATIF  (p10)"),
                     (0.50, "REALISTIS(p50)"),
                     (0.90, "POSITIF  (p90)")]:
        v = pct(p)
        print(f"  {label:16s}: ${v:6.0f}  ({(v/START-1)*100:+.0f}%)")
    print(f"  Peluang profit (>$100)   : {prob_profit*100:.0f}%")
    print(f"  Peluang 2x   (>=$200)    : {prob_2x*100:.0f}%")
    print(f"  Peluang hancur (<=$30)   : {prob_ruin*100:.0f}%")


def main() -> None:
    print(f"Monte Carlo {N_SIM:,} bulan | modal ${START:.0f} | lot 0.01 | "
          f"RR 1:3 | winrate dasar {WINRATE:.0%} | ~{ACTIVE_DAYS} hari aktif")
    random.seed(SEED)
    _report("IDEAL (39%/1:3 bersih, batas atas)", sim_month_ideal)
    random.seed(SEED)
    _report("REALISTIS (dgn friksi + ketidakpastian winrate)", sim_month_real)


if __name__ == "__main__":
    main()
