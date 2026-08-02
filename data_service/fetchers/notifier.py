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
    sym = sig.get("symbol", "XAUUSD")

    if side not in ("buy", "sell"):
        # Tidak ada sinyal -> kartu minimalis.
        desc = f"**{prof}**\n\n⚪ {sig.get('reason', 'Belum ada setup, tunggu.')}"
        return {"embeds": [{
            "title": f"⚪ {sym} — tunggu",
            "description": desc,
            "color": _COLORS["none"],
            "footer": {"text": "Eksekusi manual · bukan saran finansial"},
            "timestamp": sig.get("time_utc"),
        }]}

    arrow = "↑" if side == "buy" else "↓"
    title = f"🟢 BUY {sym}" if side == "buy" else f"🔴 SELL {sym}"

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
    news = sig.get("top_news") or []
    if news:
        desc += "\n\n📰 **Berita penggerak:**"
        for h in news[:2]:
            desc += f"\n> {str(h)[:120]}"

    return {
        "embeds": [{
            "title": title,
            "description": desc,
            "color": _COLORS.get(side, _COLORS["none"]),
            "footer": {"text": "Eksekusi manual · bukan saran finansial"},
            "timestamp": sig.get("time_utc"),
        }]
    }


def format_outcome_embed(entry: dict[str, Any], stats_text: str,
                         pnl_usd: float = 0.0) -> dict[str, Any]:
    """Embed laporan hasil sinyal. pnl_usd = $ P/L NYATA (magnitude) dari runner
    (config-derived per simbol/lot)."""
    status = entry.get("status")
    side = str(entry.get("side", "")).upper()
    prof = entry.get("profile", "")
    rr = entry.get("rr", 3)
    sym = entry.get("symbol", "XAUUSD")
    pnl = abs(pnl_usd)
    if status == "win":
        title = f"✅ TP TERCAPAI — {side} {sym}"
        color = 3066993
        line = (f"Entry `{entry.get('entry')}` → TP `{entry.get('tp')}`  "
                f"(**+${pnl:,.2f}** / +{pnl * 100:,.0f}¢ · +{float(rr):g}R)")
    elif status == "loss":
        title = f"❌ SL KENA — {side} {sym}"
        color = 15158332
        line = (f"Entry `{entry.get('entry')}` → SL `{entry.get('sl')}`  "
                f"(**−${pnl:,.2f}** / −{pnl * 100:,.0f}¢ · −1R)")
    else:
        title = f"⌛ KEDALUWARSA — {side} {sym}"
        color = 9807270
        line = f"Entry `{entry.get('entry')}` tidak menyentuh TP/SL dalam batas waktu."
    desc = "\n".join([f"**{prof}** · sinyal {entry.get('time_utc', '')[:16]} UTC", "", line,
                      "", f"📈 {stats_text}"])
    return {"embeds": [{"title": title, "description": desc, "color": color,
                        "footer": {"text": "Rekap v2 · akun Cent $100 · eksekusi manual"}}]}


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


def _simple(title: str, desc: str, color: int, footer: str) -> dict[str, Any]:
    return {"embeds": [{"title": title, "description": desc, "color": color,
                        "footer": {"text": footer}}]}


_SPARK = "▁▂▃▄▅▆▇█"


def _sparkline(vals: list[float]) -> str:
    if len(vals) < 2:
        return "—"
    lo, hi = min(vals), max(vals)
    rng = hi - lo or 1.0
    return "".join(_SPARK[min(7, int((v - lo) / rng * 7))] for v in vals)


def _bar(pct: float, width: int = 12) -> str:
    fill = max(0, min(width, round(pct / 100 * width)))
    return "█" * fill + "░" * (width - fill)


def format_portfolio(p: dict[str, Any]) -> dict[str, Any]:
    """Dashboard portofolio dari hasil sinyal (equity, WR, PF, DD, target)."""
    bal, start = p["balance"], p["start"]
    chg = (bal / start - 1) * 100
    emo = "🟢" if chg >= 0 else "🔴"
    tgt_pct = min(100, max(0, (bal - start) / (p["target"] - start) * 100)) if p["target"] > start else 0
    net = p["net_usd"]
    dd = p["dd_usd"]
    g = p.get("per_sym", {}).get("XAUUSD", [0, 0])
    b = p.get("per_sym", {}).get("BTCUSD", [0, 0])
    # Akun CENT: saldo di MT5 tampil dalam sen (1 USD = 100¢). $ = nilai riil.
    desc = "\n".join([
        f"# {emo} ${bal:,.2f}   ({chg:+.1f}% dari ${start:.0f})",
        f"### 🪙 = {bal * 100:,.0f}¢  (tampilan akun Cent di MT5)",
        f"`{p['spark']}`",
        "",
        f"💵 **Net {'+' if net >= 0 else '−'}${abs(net):,.2f}**  ·  Win rate **{p['wr']:.0f}%**  ·  PF **{p['pf']:.2f}**",
        f"✅ {p['wins']}W / ❌ {p['losses']}L  ·  📉 Max DD **−${abs(dd):,.2f}** ({abs(dd) * 100:,.0f}¢)",
        f"🔥 Streak terpanjang: {p['mcw']}W / {p['mcl']}L  ·  ⏳ {p['open']} posisi terbuka",
        f"🥇 XAU {g[0]}W/{g[1]}L    ·    🪙 BTC {b[0]}W/{b[1]}L",
        "",
        f"🎯 **Target ${p['target']:.0f}** ({p['target'] * 100:,.0f}¢)**:** `{_bar(tgt_pct)}` {tgt_pct:.0f}%",
    ])
    return _simple("📊 PORTOFOLIO v2 — Scalper's Boys (Cent $100)", desc, 3447003,
                   "Akun Cent $100 · $ nyata @lot 0.2 · era cent · ¢ = tampilan MT5 · bukan saran finansial")


def format_weekly(w: dict[str, Any]) -> dict[str, Any]:
    desc = "\n".join([
        f"**Periode:** {w['period']}",
        "",
        f"📊 Sinyal minggu ini: **{w['n']}**  ({w['wins']}W / {w['losses']}L, WR {w['wr']:.0f}%)",
        f"💵 Net minggu ini: **{'+' if w['net_usd'] >= 0 else '−'}${abs(w['net_usd']):,.2f}**  "
        f"({abs(w['net_usd']) * 100:,.0f}¢)",
        f"🏆 Terbaik: {w['best']}  ·  💥 Terburuk: {w['worst']}",
        "",
        f"🧠 Catatan: {w['note']}",
    ])
    return _simple("📅 REKAP MINGGUAN", desc, 10181046,
                   "Ringkasan otomatis tiap Minggu malam")


def format_news_reminder(ev: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": "@everyone ⏰ **BERITA HIGH-IMPACT ~30 MENIT LAGI**",
        "embeds": [{
            "title": f"🔴 {ev.get('title')} ({ev.get('currency')})",
            "description": (f"Jadwal: `{str(ev.get('time_utc'))[11:16]} UTC`\n"
                           "Volatilitas emas bisa melonjak. Bot pause entry di sekitar "
                           "waktu ini — hati-hati kalau posisi terbuka."),
            "color": 15158332,
            "footer": {"text": "Pengingat kalender ekonomi"},
        }],
    }


_DIR_WORD = {"up": "📈 NAIK", "down": "📉 TURUN", "flat": "➡️ NETRAL"}


def format_news_prediction(ev: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    """Kartu prediksi PRA-berita: kondisi teknikal+sentimen sekarang + skenario
    arah emas saat rilis (aktual vs forecast). Bukan kepastian."""
    lean = _DIR_WORD.get(info.get("lean", "flat"), "➡️ NETRAL")
    hot = _DIR_WORD.get(info.get("hot_dir", "down"), "📉 TURUN")
    cool = _DIR_WORD.get(info.get("cool_dir", "up"), "📈 NAIK")
    fc = ev.get("forecast") or "—"
    prev = ev.get("previous") or "—"
    sc = info.get("sent_score")
    sc_txt = f" ({sc:+.2f})" if isinstance(sc, (int, float)) else ""
    desc = "\n".join([
        f"🕐 Rilis: `{str(ev.get('time_utc'))[11:16]} UTC` (~{info.get('mins', 0):.0f} mnt lagi)",
        f"📊 Forecast: **{fc}**  ·  Sebelumnya: **{prev}**",
        "",
        "**📈 Kondisi SEKARANG:**",
        f"• Tren: H1 **{info.get('h_trend')}** · Intraday **{info.get('i_trend')}**",
        f"• Momentum: **{info.get('momentum')}**  ·  Sentimen: **{info.get('sent_bias')}**{sc_txt}",
        f"➡️ Bias teknikal+sentimen: **{lean}**  ({info.get('ups', 0)} vs {info.get('dns', 0)} faktor)",
        "",
        "**🎲 Skenario saat rilis:**",
        f"• Aktual **> forecast** (lebih panas) → emas **{hot}**",
        f"• Aktual **< forecast** (lebih dingin) → emas **{cool}**",
        "• Sesuai forecast → gerak kecil, cenderung ikut tren",
    ])
    return {
        "content": f"🔮 Prediksi pra-berita: **{ev.get('title')}** (~{info.get('mins', 0):.0f} mnt lagi)",
        "embeds": [{
            "title": f"🔮 {ev.get('title')} ({ev.get('currency')})",
            "description": desc,
            "color": 10181046,
            "footer": {"text": "Skenario, BUKAN kepastian · volatil & spread melebar saat rilis · bukan saran finansial"},
        }],
    }


def format_price_embed(price: float, chg_1h: float, chg_24h: float,
                       hi: float, lo: float) -> dict[str, Any]:
    e = "📈" if chg_24h >= 0 else "📉"
    desc = "\n".join([
        f"# `${price:,.2f}`",
        f"{e} 1 jam: **{chg_1h:+.2f}**  ·  24 jam: **{chg_24h:+.2f}**",
        f"Rentang 24 jam: `{lo:,.2f}` – `{hi:,.2f}`",
    ])
    color = 3066993 if chg_24h >= 0 else 15158332
    return _simple("👑 GOLD PRICE — XAU/USD", desc, color, "Update tiap jam · Twelve Data")


def format_news_embed(items: list[tuple[float, str]]) -> dict[str, Any]:
    lines = []
    for sc, h in items[:6]:
        tag = "🟢" if sc > 0 else "🔴" if sc < 0 else "⚪"
        lines.append(f"{tag} {h[:150]}")
    return _simple("🥇 NEWS FOREX · GOLD · USD",
                   "\n\n".join(lines) + "\n\n🟢 bullish emas · 🔴 bearish emas",
                   15844367, "Kurasi khusus penggerak emas/dolar · tiap ~2 jam")


def format_rich_news(items: list[dict[str, Any]], color: int,
                     tag_fn=None) -> dict[str, Any]:
    """Tiap berita = embed sendiri (judul ber-link, gambar, sumber). Maks 5."""
    embeds = []
    for it in items[:5]:
        prefix = tag_fn(it) if tag_fn else ""
        e: dict[str, Any] = {
            "title": (prefix + str(it.get("title", "")))[:250],
            "color": color,
            "footer": {"text": f"Sumber: {it.get('source', '?')}"},
        }
        if it.get("desc"):
            e["description"] = str(it["desc"])[:250]
        if it.get("link"):
            e["url"] = it["link"]
        if it.get("image"):
            e["image"] = {"url": it["image"]}
        embeds.append(e)
    return {"embeds": embeds}


_FLAGS = {"EUR": "🇪🇺", "JPY": "🇯🇵", "GBP": "🇬🇧", "CHF": "🇨🇭", "AUD": "🇦🇺",
          "NZD": "🇳🇿", "CAD": "🇨🇦", "CNY": "🇨🇳", "IDR": "🇮🇩", "INR": "🇮🇳",
          "KRW": "🇰🇷", "SGD": "🇸🇬", "MYR": "🇲🇾", "THB": "🇹🇭", "PHP": "🇵🇭",
          "MXN": "🇲🇽", "BRL": "🇧🇷", "ZAR": "🇿🇦", "TRY": "🇹🇷", "SEK": "🇸🇪"}


def format_dollar20_embed(rows: list[tuple[str, float]], first: bool) -> dict[str, Any]:
    """rows = [(kode_mata_uang, %perubahan kekuatan USD vs mata uang itu)]."""
    if first:
        desc = "Snapshot awal 20 mata uang direkam — perbandingan mulai update berikutnya."
    else:
        up = sum(1 for _, c in rows if c > 0)
        lines = []
        for cur, chg in sorted(rows, key=lambda x: -x[1]):
            e = "🟢" if chg > 0 else "🔴" if chg < 0 else "⚪"
            lines.append(f"{_FLAGS.get(cur, '🏳️')} **{cur}** {e} USD {chg:+.2f}%")
        verdict = ("💪 USD MENGUAT luas → tekanan turun utk emas" if up >= 13 else
                   "😴 USD MELEMAH luas → dukungan naik utk emas" if up <= 7 else
                   "⚖️ USD campuran")
        desc = "\n".join(lines[:20]) + f"\n\n**{verdict}**  ({up}/20 menguat)"
    return _simple("💵 DOLLAR MONITOR — USD vs 20 mata uang", desc, 5763719,
                   "Termasuk 🇮🇩 IDR · perubahan sejak update sebelumnya · 2x/hari")


def format_calendar_embed(events: list[dict[str, Any]]) -> dict[str, Any]:
    if not events:
        desc = "Tidak ada event USD berdampak tinggi/menengah hari ini. 🎉"
    else:
        rows = []
        for ev in events[:10]:
            imp = "🔴" if ev.get("impact") == "high" else "🟠"
            t = str(ev.get("time_utc", ""))[11:16]
            rows.append(f"{imp} `{t} UTC` **{ev.get('title')}** ({ev.get('currency')})")
        desc = "\n".join(rows) + "\n\n⚠️ Bot pause entry ±30 mnt sekitar event 🔴"
    return _simple("📅 KALENDER EKONOMI HARI INI", desc, 15105570,
                   "ForexFactory · dikirim tiap pagi London")


def format_dollar_embed(eur: float, eur_chg: float, jpy: float,
                        jpy_chg: float) -> dict[str, Any]:
    # EUR/USD turun & USD/JPY naik = dolar menguat = tekanan utk emas.
    usd_up = (eur_chg < 0) + (jpy_chg > 0)
    verdict = ("💪 Dolar MENGUAT → tekanan turun utk emas" if usd_up == 2 else
               "😴 Dolar MELEMAH → dukungan naik utk emas" if usd_up == 0 else
               "⚖️ Dolar campuran (sinyal tidak searah)")
    desc = "\n".join([
        f"EUR/USD: `{eur:.5f}` ({eur_chg:+.4f} /4j)",
        f"USD/JPY: `{jpy:.3f}` ({jpy_chg:+.3f} /4j)",
        "",
        verdict,
    ])
    return _simple("💵 DOLLAR MONITOR (proxy DXY)", desc, 5763719,
                   "EUR/USD+USD/JPY = komponen utama indeks dolar · tiap 4 jam")


def format_prediction_embed(trend_h: str, trend_i: str, sent_bias: str,
                            sent_score: float, cot: str, verdict: str) -> dict[str, Any]:
    desc = "\n".join([
        f"📊 Tren H1: **{trend_h}** · Tren H4: **{trend_i}**",
        f"📰 Sentimen berita: **{sent_bias}** ({sent_score})",
        f"🏦 COT mingguan: **{cot}**",
        "",
        f"🎯 Kesimpulan bot: **{verdict}**",
        "_Update hanya saat pandangan berubah._",
    ])
    return _simple("👽 BOT PREDICTION — pandangan saat ini", desc, 10181046,
                   "Gabungan teknikal+sentimen+COT · bukan kepastian")


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
