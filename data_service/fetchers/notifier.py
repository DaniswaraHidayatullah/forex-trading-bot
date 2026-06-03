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
    prof = sig.get("profile", "")
    stars = sig.get("confidence_stars", "")
    if side == "buy":
        title = f"🟢 BUY XAUUSD • {prof} {stars}"
    elif side == "sell":
        title = f"🔴 SELL XAUUSD • {prof} {stars}"
    else:
        title = f"⚪ Tidak ada sinyal • {prof}"

    def fld(name: str, val: Any, inline: bool = True) -> dict[str, Any]:
        return {"name": name, "value": str(val), "inline": inline}

    fields: list[dict[str, Any]] = []
    if side in ("buy", "sell"):
        sl_txt = f"`{sig.get('sl')}`  ({sig.get('sl_pips')} pips • -${sig.get('risk_per_001')})"
        tp_txt = f"`{sig.get('tp')}`  ({sig.get('tp_pips')} pips • +${sig.get('reward_per_001')})"
        fields += [
            fld("Profil", f"{prof}  ({sig.get('trend_tf')}→{sig.get('entry_tf')})", inline=False),
            fld("Entry", f"`{sig.get('entry')}`"),
            fld("Lot", sig.get("suggested_lot")),
            fld("RR", f"1:{int(sig.get('rr', 3))}"),
            fld("Stop Loss", sl_txt, inline=False),
            fld("Take Profit", tp_txt, inline=False),
            fld("Tahan posisi", sig.get("hold")),
            fld("RSI / Tren", f"{sig.get('rsi')} / {sig.get('trend')}"),
            fld("Sentimen", f"{sig.get('sentiment_bias')} ({sig.get('sentiment_score')})"),
            fld("Keyakinan", f"{stars} {sig.get('confidence')}"),
        ]
    fields.append(fld("Alasan", sig.get("reason", "-"), inline=False))

    return {
        "embeds": [{
            "title": title,
            "color": _COLORS.get(side, _COLORS["none"]),
            "fields": fields,
            "footer": {"text": "forex-bot • eksekusi MANUAL • bukan saran finansial • risiko di tangan kamu"},
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
