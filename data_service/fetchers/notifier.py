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

    sent_txt = (
        f"Sentimen {sig.get('sentiment_bias')} ({sig.get('sentiment_score')})"
        if sig.get("sentiment_available", True)
        else "Sentimen tidak tersedia"
    )
    risk_pct = sig.get("risk_pct")
    risk_note = f"  ·  ⚠️ ~{risk_pct}% akun" if risk_pct else ""
    desc = "\n".join([
        f"**{prof}**  ·  {stars} {conf}",
        "",
        f"💰 **Entry**  `{sig.get('entry')}`   _(zona {sig.get('entry_zone_low')}–{sig.get('entry_zone_high')})_",
        f"🎯 **Take Profit**  `{sig.get('tp')}`   → **+${sig.get('reward_per_001')}**  _({sig.get('tp_pips')} pips)_",
        f"🛑 **Stop Loss**  `{sig.get('sl')}`   → **−${sig.get('risk_per_001')}**{risk_note}  _({sig.get('sl_pips')} pips)_",
        f"📦 **Lot** `{sig.get('suggested_lot')}`   ·   ⚖️ **RR 1:{float(sig.get('rr', 3)):g}**",
        "",
        f"⏱️ Masuk **sekarang** — berlaku ~{sig.get('valid_minutes')} menit",
        f"⏳ Perkiraan tahan: {sig.get('hold')}",
        f"📊 Tren {arrow} · RSI {sig.get('rsi')} · {sent_txt}",
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


def format_outcome_embed(entry: dict[str, Any], stats_text: str) -> dict[str, Any]:
    """Embed laporan hasil sinyal (kena TP / kena SL / kedaluwarsa)."""
    status = entry.get("status")
    side = str(entry.get("side", "")).upper()
    prof = entry.get("profile", "")
    rr = entry.get("rr", 3)
    if status == "win":
        title = f"✅ TP TERCAPAI — {side} XAUUSD"
        color = 3066993
        line = f"Entry `{entry.get('entry')}` → TP `{entry.get('tp')}`  (**+{rr}R**, +${entry.get('reward_usd', '')})"
    elif status == "loss":
        title = f"❌ SL KENA — {side} XAUUSD"
        color = 15158332
        line = f"Entry `{entry.get('entry')}` → SL `{entry.get('sl')}`  (**−1R**, −${entry.get('risk_usd', '')})"
    else:
        title = f"⌛ KEDALUWARSA — {side} XAUUSD"
        color = 9807270
        line = f"Entry `{entry.get('entry')}` tidak menyentuh TP/SL dalam batas waktu."
    desc = "\n".join([f"**{prof}** · sinyal {entry.get('time_utc', '')[:16]} UTC", "", line,
                      "", f"📈 {stats_text}"])
    return {"embeds": [{"title": title, "description": desc, "color": color,
                        "footer": {"text": "Rekap otomatis · eksekusi manual"}}]}


def format_burst_embed(direction: str, move_usd: float, price: float,
                       sentiment_bias: str, note: str = "") -> dict[str, Any]:
    """Embed INFO pergerakan besar (kemungkinan berita). BUKAN sinyal entry."""
    arrow = "🚀 NAIK" if direction == "up" else "⚠️ TURUN"
    pips = abs(move_usd) * 10
    desc = "\n".join([
        f"**{arrow} {pips:.0f} pips dalam ~1 jam**  (${abs(move_usd):.0f})",
        f"Harga sekarang: `{price}`  ·  Sentimen berita: {sentiment_bias}",
        "",
        "ℹ️ Ini **INFO**, bukan sinyal entry. Backtest menunjukkan mengejar "
        "ledakan berita tidak menguntungkan (whipsaw). Sistem menunggu "
        "pullback yang lebih aman." + (f"\n{note}" if note else ""),
    ])
    return {"embeds": [{"title": "⚡ PERGERAKAN BESAR TERDETEKSI — XAUUSD",
                        "description": desc, "color": 16776960,
                        "footer": {"text": "Deteksi berita otomatis · bukan saran finansial"}}]}


def format_digest_embed(info: dict[str, Any]) -> dict[str, Any]:
    """Ringkasan harian (dikirim 1x/hari saat sesi London buka)."""
    desc = "\n".join([
        f"💰 Emas: `{info.get('price')}`",
        f"📊 Tren Harian(H1): {info.get('trend_harian')} · Intraday(H4): {info.get('trend_intraday')}",
        f"📰 Sentimen: {info.get('sent_bias')} ({info.get('sent_score')}) "
        f"dari {info.get('headlines')} berita",
        f"🏦 COT: {info.get('cot_bias')}",
        "",
        f"📈 Performa v2: {info.get('stats')}",
        f"🚧 Gate sentimen (bayangan): {info.get('shadow_stats')}",
        f"📌 Posisi terbuka: {info.get('open_positions')}",
    ])
    return {"embeds": [{"title": "☀️ RINGKASAN HARIAN — XAUUSD Scalpers Boys",
                        "description": desc, "color": 3447003,
                        "footer": {"text": "Ringkasan otomatis tiap buka sesi London"}}]}


DISCORD_API = "https://discord.com/api/v10"


def _payload(sig_or_payload: dict[str, Any]) -> dict[str, Any]:
    """Terima dict sinyal ATAU payload embed jadi (punya key 'embeds')."""
    if "embeds" in sig_or_payload:
        return sig_or_payload
    return format_embed(sig_or_payload)


def send_webhook(webhook_url: str, sig: dict[str, Any], timeout: float = 10.0) -> bool:
    """Kirim embed lewat Discord WEBHOOK. Return True bila terkirim (2xx)."""
    if not webhook_url:
        return False
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(webhook_url, json=_payload(sig))
        resp.raise_for_status()
    return True


def send_bot(token: str, channel_id: str, sig: dict[str, Any], timeout: float = 10.0) -> bool:
    """Kirim embed lewat Discord BOT (REST API) ke sebuah channel.

    Syarat: bot sudah di-invite ke server & punya izin kirim pesan di channel.
    Hanya butuh REST (tanpa gateway/websocket) -> ringan.
    """
    if not token or not channel_id:
        return False
    url = f"{DISCORD_API}/channels/{channel_id}/messages"
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(
            url,
            headers={"Authorization": f"Bot {token}"},
            json=_payload(sig),
        )
        resp.raise_for_status()
    return True


# Alias kompatibilitas lama.
send_discord = send_webhook
