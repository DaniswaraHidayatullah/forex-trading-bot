"""Cache berbasis file sederhana dengan TTL.

Tujuannya: jangan scraping ForexFactory / CFTC tiap request EA, biar nggak
kena rate-limit dan respons cepat. Cukup untuk free tier Railway.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

CACHE_DIR = Path(__file__).parent / ".cache"
CACHE_DIR.mkdir(exist_ok=True)


def _path(key: str) -> Path:
    safe = "".join(c if c.isalnum() else "_" for c in key)
    return CACHE_DIR / f"{safe}.json"


def get_or_set(
    key: str,
    ttl_seconds: int,
    producer: Callable[[], Any],
    max_stale_seconds: int | None = None,
) -> Any:
    """Kembalikan nilai dari cache bila masih segar, kalau tidak panggil producer().

    Bila producer() gagal tapi ada cache lama, kembalikan cache lama (stale)
    supaya EA tetap dapat data — lebih baik data basi daripada error.

    max_stale_seconds: batas umur cache basi yang masih boleh dipakai saat
      producer gagal. None = tanpa batas (perilaku lama). Untuk data sensitif
      waktu (kalender berita), set agar cache yang terlalu tua TIDAK dipakai ->
      error menjalar ke EA -> EA fail-safe (skip entry).
    """
    path = _path(key)
    now = time.time()

    cached: dict[str, Any] | None = None
    if path.exists():
        try:
            cached = json.loads(path.read_text())
            if now - cached["ts"] < ttl_seconds:
                return cached["value"]
        except (json.JSONDecodeError, KeyError):
            cached = None

    try:
        value = producer()
    except Exception:
        if cached is not None:
            age = now - cached.get("ts", 0)
            if max_stale_seconds is None or age <= max_stale_seconds:
                return cached["value"]  # fallback ke data basi (masih dlm batas)
        raise

    path.write_text(json.dumps({"ts": now, "value": value}))
    return value
