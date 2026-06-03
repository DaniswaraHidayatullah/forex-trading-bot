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
    """Bentuk payload embed Discord yang bersih & ringkas dari dict sinyal."""
    side = sig.get("signal", "none")
    prof = sig.get("profile", "")
    stars = sig.get("confidence_stars", "")
    conf = sig.get("confidence", "")

    if side not in ("buy", "sell"):
        # Tidak ada sinyal -> kartu minimalis.
        desc = f"**{prof}**\n\n⚪ {sig.get('reason', 'Belum ada setup, tunggu.')}"
        return {"embeds": [{
            "title": "⚪ XAUUSD — tunggu",
            "description": desc,
            "color": _COLORS["none"],
            "footer": {"text": "Eksekusi manual · bukan saran finansial"},
            "timestamp": sig.get("time_utc"),
        }]}

    arrow = "↑" if side == "buy" else "↓"
    title = "🟢 BUY XAUUSD" if side == "buy" else "🔴 SELL XAUUSD"

    desc = "\n".join([
        f"**{prof}**  ·  {stars} {conf}",
        "",
        f"💰 **Entry**  `{sig.get('entry')}`   _(zona {sig.get('entry_zone_low')}–{sig.get('entry_zone_high')})_",
        f"🎯 **Take Profit**  `{sig.get('tp')}`   → **+${sig.get('reward_per_001')}**  _({sig.get('tp_pips')} pips)_",
        f"🛑 **Stop Loss**  `{sig.get('sl')}`   → **−${sig.get('risk_per_001')}**  _({sig.get('sl_pips')} pips)_",
        f"📦 **Lot** `{sig.get('suggested_lot')}`   ·   ⚖️ **RR 1:{int(sig.get('rr', 3))}**",
        "",
        f"⏱️ Masuk **sekarang** — berlaku ~{sig.get('valid_minutes')} menit",
        f"⏳ Perkiraan tahan: {sig.get('hold')}",
        f"📊 Tren {arrow} · RSI {sig.get('rsi')} · Sentimen {sig.get('sentiment_bias')} ({sig.get('sentiment_score')})",
    ])

    return {
        "embeds": [{
            "title": title,
            "description": desc,
            "color": _COLORS.get(side, _COLORS["none"]),
            "footer": {"text": "Eksekusi manual · bukan saran finansial"},
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
