"""Ambil kalender ekonomi ForexFactory.

ForexFactory menyediakan feed mingguan dalam bentuk JSON di:
  https://nfs.faireconomy.media/ff_calendar_thisweek.json
(feed publik yang sama dipakai banyak bot). Kalau formatnya berubah,
fallback parsing HTML tersedia tapi sengaja dibuat minimal.

Catatan: hormati robots.txt & rate limit. Pakai cache (lihat storage.py).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from dateutil import parser as dateparser

FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

_IMPACT_RANK = {"low": 1, "medium": 2, "high": 3, "holiday": 0}


def _normalize_impact(raw: str) -> str:
    raw = (raw or "").strip().lower()
    if "high" in raw:
        return "high"
    if "medium" in raw or "med" in raw:
        return "medium"
    if "low" in raw:
        return "low"
    return "holiday"


def fetch_calendar(timeout: float = 15.0) -> list[dict[str, Any]]:
    """Kembalikan list event ternormalisasi.

    Tiap event: {currency, impact, title, time_utc (ISO8601)}.
    """
    with httpx.Client(timeout=timeout, headers={"User-Agent": "forex-bot/1.0"}) as client:
        resp = client.get(FEED_URL)
        resp.raise_for_status()
        rows = resp.json()

    events: list[dict[str, Any]] = []
    for row in rows:
        # Field umum di feed: country, impact, title, date (ISO dengan offset)
        when = row.get("date")
        if not when:
            continue
        try:
            dt = dateparser.parse(when)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(timezone.utc)
        except (ValueError, TypeError):
            continue

        events.append(
            {
                "currency": (row.get("country") or "").upper(),
                "impact": _normalize_impact(row.get("impact", "")),
                "title": row.get("title", ""),
                "time_utc": dt.isoformat(),
            }
        )
    return events


def upcoming_blackout(
    events: list[dict[str, Any]],
    currencies: list[str],
    min_impact: str,
    blackout_minutes: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Tentukan apakah saat ini berada dalam jendela blackout berita.

    Return: {"blocked": bool, "event": <event terdekat penyebab>|None}
    """
    now = now or datetime.now(timezone.utc)
    min_rank = _IMPACT_RANK.get(min_impact, 3)
    wanted = {c.upper() for c in currencies}

    nearest: dict[str, Any] | None = None
    for ev in events:
        if ev["currency"] not in wanted:
            continue
        if _IMPACT_RANK.get(ev["impact"], 0) < min_rank:
            continue
        try:
            ev_dt = dateparser.parse(ev["time_utc"])
        except (ValueError, TypeError):
            continue
        delta_min = abs((ev_dt - now).total_seconds()) / 60.0
        if delta_min <= blackout_minutes:
            if nearest is None or delta_min < nearest["_delta"]:
                nearest = {**ev, "_delta": delta_min}

    if nearest is not None:
        nearest.pop("_delta", None)
        return {"blocked": True, "event": nearest}
    return {"blocked": False, "event": None}
