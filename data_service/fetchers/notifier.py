"""Kirim sinyal ke Discord lewat WEBHOOK (tanpa bot token / tanpa hosting bot).

Cara dapat webhook URL:
  Server Discord -> Server Settings -> Integrations -> Webhooks -> New Webhook
  -> pilih channel -> Copy Webhook URL. Tempel ke env DISCORD_WEBHOOK_URL.
"""
from __future__ import annotations

from typing import Any

import httpx

_COLORS = {"buy": 3066993, "sell": 15158332, "none": 9807270}  # hijau/merah/abu


def format_embed(sig: dict[str, Any]) -> dict[str, Any]:
    """Bentuk payload embed Discord dari dict sinyal."""
    side = sig.get("signal", "none")
    if side == "buy":
        title = "🟢 SINYAL BUY XAUUSD"
    elif side == "sell":
        title = "🔴 SINYAL SELL XAUUSD"
    else:
        title = "⚪ Tidak ada sinyal XAUUSD"

    def fld(name: str, val: Any, inline: bool = True) -> dict[str, Any]:
        return {"name": name, "value": str(val), "inline": inline}

    fields: list[dict[str, Any]] = []
    if side in ("buy", "sell"):
        fields += [
            fld("Entry", sig.get("entry")),
            fld("SL", sig.get("sl")),
            fld("TP", sig.get("tp")),
            fld("Lot", sig.get("suggested_lot")),
            fld("RR", f"1:{int(sig.get('rr', 3))}"),
            fld("RSI / Tren", f"{sig.get('rsi')} / {sig.get('trend')}"),
            fld("Sentimen", sig.get("sentiment_bias")),
        ]
    fields.append(fld("Alasan", sig.get("reason", "-"), inline=False))

    return {
        "embeds": [{
            "title": title,
            "color": _COLORS.get(side, _COLORS["none"]),
            "fields": fields,
            "footer": {"text": "forex-bot • eksekusi MANUAL • bukan saran finansial"},
            "timestamp": sig.get("time_utc"),
        }]
    }


def send_discord(webhook_url: str, sig: dict[str, Any], timeout: float = 10.0) -> bool:
    """POST embed ke Discord webhook. Return True bila terkirim (2xx)."""
    if not webhook_url:
        return False
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(webhook_url, json=format_embed(sig))
        resp.raise_for_status()
    return True
