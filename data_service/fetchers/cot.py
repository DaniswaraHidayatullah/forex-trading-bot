"""Ambil data Commitments of Traders (COT) dari CFTC sebagai proxy sentiment.

CFTC menyediakan API Socrata (publicreporting.cftc.gov). Kita ambil laporan
"Traders in Financial Futures" / legacy report dan hitung net position
(long - short) kelompok non-commercial / leveraged funds.

Output di-normalisasi jadi skor sentiment sederhana: "long" | "short" | "flat".
"""
from __future__ import annotations

from typing import Any

import httpx

# Endpoint legacy COT (futures only), format Socrata JSON.
CFTC_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"


def _net_bias(longs: float, shorts: float, threshold: float = 0.05) -> str:
    total = longs + shorts
    if total <= 0:
        return "flat"
    skew = (longs - shorts) / total
    if skew > threshold:
        return "long"
    if skew < -threshold:
        return "short"
    return "flat"


def fetch_cot(market_name: str, timeout: float = 20.0) -> dict[str, Any]:
    """Ambil laporan COT terbaru untuk satu market.

    Return: {"market": str, "bias": "long|short|flat",
             "noncomm_long": int, "noncomm_short": int, "report_date": str}
    """
    params = {
        "$where": f"upper(market_and_exchange_names) like '%{market_name.upper()}%'",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": "1",
    }
    with httpx.Client(timeout=timeout, headers={"User-Agent": "forex-bot/1.0"}) as client:
        resp = client.get(CFTC_URL, params=params)
        resp.raise_for_status()
        rows = resp.json()

    if not rows:
        return {
            "market": market_name,
            "bias": "flat",
            "noncomm_long": 0,
            "noncomm_short": 0,
            "report_date": None,
        }

    row = rows[0]
    longs = float(row.get("noncomm_positions_long_all", 0) or 0)
    shorts = float(row.get("noncomm_positions_short_all", 0) or 0)

    return {
        "market": market_name,
        "bias": _net_bias(longs, shorts),
        "noncomm_long": int(longs),
        "noncomm_short": int(shorts),
        "report_date": row.get("report_date_as_yyyy_mm_dd"),
    }
