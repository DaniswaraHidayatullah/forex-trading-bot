"""Pelacak hasil sinyal: tentukan tiap sinyal kena TP (win) atau SL (loss),
lalu hitung statistik akurasi (win-rate, net R).

Log sinyal disimpan sebagai JSON di repo (signals/log.json) dan di-commit oleh
workflow -> tahan lama, bisa diaudit user, dan jadi sumber dedupe (1 sinyal
terbuka per profil).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def parse_utc(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def check_outcome(
    bars: list[dict[str, Any]],
    side: str,
    sl: float,
    tp: float,
    after_utc: str | None = None,
) -> str | None:
    """Telusuri bar (urut lama->baru) sesudah waktu entry; kembalikan
    "win" bila TP tersentuh duluan, "loss" bila SL duluan, None bila belum.

    Bila SL & TP tersentuh di bar yang sama, dianggap "loss" (konservatif,
    karena urutan intrabar tidak diketahui).
    """
    t0 = parse_utc(after_utc) if after_utc else None
    for b in bars:
        if t0 is not None and "datetime" in b:
            try:
                bt = parse_utc(str(b["datetime"]))
            except ValueError:
                continue
            if bt <= t0:
                continue
        hi, lo = float(b["high"]), float(b["low"])
        if side == "buy":
            hit_sl, hit_tp = lo <= sl, hi >= tp
        else:
            hit_sl, hit_tp = hi >= sl, lo <= tp
        if hit_sl:
            return "loss"          # termasuk kasus dua-duanya kena (konservatif)
        if hit_tp:
            return "win"
    return None


def summarize(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Statistik dari log: win-rate & net R (win=+RR, loss=-1)."""
    wins = [e for e in entries if e.get("status") == "win"]
    losses = [e for e in entries if e.get("status") == "loss"]
    open_ = [e for e in entries if e.get("status") == "open"]
    expired = [e for e in entries if e.get("status") == "expired"]
    closed = len(wins) + len(losses)
    winrate = round(len(wins) / closed * 100, 1) if closed else 0.0
    net_r = round(sum(float(e.get("rr", 3)) for e in wins) - float(len(losses)), 1)
    return {
        "wins": len(wins), "losses": len(losses), "open": len(open_),
        "expired": len(expired), "closed": closed,
        "winrate_pct": winrate, "net_r": net_r,
    }


def stats_line(stats: dict[str, Any]) -> str:
    """Satu baris ringkas untuk footer/laporan Discord."""
    return (
        f"Akurasi: {stats['winrate_pct']}% ({stats['wins']}W/{stats['losses']}L) "
        f"· Net {stats['net_r']:+g}R · {stats['open']} posisi terbuka"
    )
