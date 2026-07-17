"""Sekali jalan (GitHub Actions cron):
  1. Update hasil sinyal terbuka (kena TP/SL?) -> kirim rekap ke Discord.
  2. Hitung sinyal baru per profil -> kirim bila layak.
Log di signals/log.json (di-commit workflow) = sumber kebenaran anti-spam:
maks 1 sinyal TERBUKA per profil; sinyal baru hanya setelah yang lama selesai.

Rahasia dibaca dari environment (GitHub Secrets). Tidak ada rahasia di kode.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data_service"))

import main  # noqa: E402
from fetchers import notifier, signal_engine, tracker  # noqa: E402

LOG_FILE = Path(os.getenv("SIGNAL_LOG", str(ROOT / "signals" / "log.json")))
META_FILE = LOG_FILE.parent / "meta.json"
EQUITY = float(os.getenv("EQUITY", "100"))
EXPIRE_DAYS = {"Harian": 2, "Scalping": 1, "Intraday": 3, "Swing": 10}
_LEVEL = {"none": 0, "medium": 2, "strong": 3}
BURST_ATR_MULT = 3.0        # ledakan = gerak 1 jam >= 3x ATR(M15)
BURST_COOLDOWN_H = 2        # jangan alert ledakan lagi dalam N jam
DIGEST_HOUR_UTC = 7         # ringkasan harian saat London buka (~14:00 WIB)


def _load_log() -> list[dict]:
    if LOG_FILE.exists():
        try:
            return json.loads(LOG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save_log(entries: list[dict]) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.write_text(json.dumps(entries, indent=1), encoding="utf-8")


def _load_meta() -> dict:
    if META_FILE.exists():
        try:
            return json.loads(META_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_meta(meta: dict) -> None:
    META_FILE.parent.mkdir(parents=True, exist_ok=True)
    META_FILE.write_text(json.dumps(meta, indent=1), encoding="utf-8")


def _is_v2(e: dict) -> bool:
    """Sinyal era sistem baru (bukan backfill lama, bukan bayangan)."""
    return not e.get("legacy") and not e.get("shadow")


def _stats_texts(entries: list[dict]) -> tuple[str, str]:
    """(stats sinyal v2, stats bayangan) sebagai teks siap tampil."""
    v2 = tracker.summarize([e for e in entries if _is_v2(e)])
    sh = tracker.summarize([e for e in entries if e.get("shadow")])
    v2_txt = f"{tracker.stats_line(v2)} (sejak 14 Jul, sistem v2)"
    sh_txt = (f"{sh['winrate_pct']}% ({sh['wins']}W/{sh['losses']}L, "
              f"{sh['open']} terbuka) — makin RENDAH makin bagus gate-nya")
    return v2_txt, sh_txt


def _resolve_open(entries: list[dict]) -> None:
    """Cek sinyal terbuka: sudah kena TP/SL? Kirim rekap + update status."""
    open_entries = [e for e in entries if e.get("status") == "open"]
    if not open_entries:
        return
    api_key = main.settings.twelvedata_api_key
    if not api_key:
        print("skip resolve: tidak ada TWELVEDATA_API_KEY")
        return

    # Satu fetch M5 dipakai semua sinyal terbuka (hemat kredit).
    oldest = min(tracker.parse_utc(e["time_utc"]) for e in open_entries)
    minutes = (datetime.now(timezone.utc) - oldest).total_seconds() / 60
    size = min(5000, max(50, int(minutes / 5) + 20))
    try:
        bars = signal_engine.fetch_series(main.settings.signal_symbol, "5min", size, api_key)
    except Exception as e:  # noqa: BLE001
        print("resolve: gagal ambil M5:", e)
        return

    now = datetime.now(timezone.utc)
    for e in open_entries:
        outcome = tracker.check_outcome(
            bars, e["side"], float(e["sl"]), float(e["tp"]), after_utc=e["time_utc"]
        )
        if outcome is None:
            age = now - tracker.parse_utc(e["time_utc"])
            if age > timedelta(days=EXPIRE_DAYS.get(e.get("profile", "Intraday"), 3)):
                e["status"] = "expired"
            else:
                continue
        else:
            e["status"] = outcome
        e["closed_utc"] = now.isoformat(timespec="seconds")
        if e.get("shadow"):
            # Bayangan (diblokir sentimen): dilacak diam-diam, tanpa Discord.
            print(f"[shadow ] {e.get('profile')} {e['side']} -> {e['status']}")
            continue
        v2_txt, sh_txt = _stats_texts(entries)
        stats_text = v2_txt
        sh = tracker.summarize([x for x in entries if x.get("shadow")])
        if sh["closed"]:
            stats_text += f"\n🚧 Diblokir sentimen (bayangan): {sh_txt}"
        payload = notifier.format_outcome_embed(e, stats_text)
        sent = main._push_discord(payload, channel="report")
        print(f"[resolve] {e.get('profile')} {e['side']} -> {e['status']} (dikirim={sent})")


def _new_signals(entries: list[dict]) -> None:
    profiles = [p.strip() for p in main.settings.signal_profiles.split(",") if p.strip()]
    min_level = _LEVEL.get(main.settings.signal_min_confidence, 2)

    # Diagnostik sentimen: sumber mana yang hidup/diblokir dari runner ini.
    try:
        s = main.sentiment("XAUUSD")
        print(f"[sentimen] bias={s.get('bias')} skor={s.get('score')} "
              f"ter-skor={s.get('headlines_scored')}/{s.get('headlines_total')} "
              f"| sumber={s.get('sources')}")
    except Exception as e:  # noqa: BLE001
        print("[sentimen] ERROR:", e)

    for profile in profiles:
        label = signal_engine.PROFILES.get(profile, signal_engine.PROFILES["intraday"])["label"]
        open_real = any(e.get("status") == "open" and e.get("profile") == label
                        and not e.get("shadow") for e in entries)
        open_shadow = any(e.get("status") == "open" and e.get("profile") == label
                          and e.get("shadow") for e in entries)
        if open_real:
            print(f"[{profile}] masih ada sinyal terbuka -> tunggu selesai")
            continue
        try:
            sig = main._signal_for("XAUUSD", EQUITY, profile)
        except Exception as e:  # noqa: BLE001
            print(f"[{profile}] ERROR: {e}")
            continue

        side = sig.get("signal", "none")
        sent_ok = sig.get("sentiment_available", False)
        print(f"[{profile}] {side} | sentimen tersedia={sent_ok} "
              f"({sig.get('sentiment_bias')}/{sig.get('sentiment_score')}) | {sig.get('reason')}")

        # Sinyal diblokir sentimen -> catat sbg BAYANGAN (tidak dikirim),
        # supaya nilai gate sentimen bisa diukur, bukan diasumsikan.
        if (side == "none" and sig.get("shadow_side") and sig.get("sl") is not None
                and not open_shadow):
            entries.append({
                "id": uuid.uuid4().hex[:8],
                "profile": sig.get("profile"),
                "side": sig["shadow_side"],
                "entry": sig.get("entry"), "sl": sig.get("sl"), "tp": sig.get("tp"),
                "rr": sig.get("rr", 3),
                "risk_usd": sig.get("risk_per_001"), "reward_usd": sig.get("reward_per_001"),
                "confidence": 0, "shadow": True,
                "time_utc": sig.get("time_utc"),
                "status": "open",
            })
            print(f"[{profile}] bayangan {sig['shadow_side'].upper()} dicatat (tidak dikirim)")
            continue

        if side not in ("buy", "sell"):
            continue
        if sig.get("confidence_level", 0) < min_level:
            print(f"[{profile}] keyakinan < {main.settings.signal_min_confidence} -> tidak dikirim")
            continue

        payload = notifier.format_embed(sig)
        if sig.get("confidence_level", 0) >= 3:
            payload["content"] = "@everyone ⭐⭐⭐ SINYAL KUAT — berita & teknikal searah!"
        sent = main._push_discord(payload)
        entries.append({
            "id": uuid.uuid4().hex[:8],
            "profile": sig.get("profile"),
            "side": side,
            "entry": sig.get("entry"), "sl": sig.get("sl"), "tp": sig.get("tp"),
            "rr": sig.get("rr", 3),
            "risk_usd": sig.get("risk_per_001"), "reward_usd": sig.get("reward_per_001"),
            "confidence": sig.get("confidence_level"),
            "time_utc": sig.get("time_utc"),
            "status": "open",
        })
        print(f"[{profile}] SINYAL {side.upper()} dikirim={sent} @ {sig.get('entry')}")


def _m15_cached() -> list[dict]:
    sym = main.settings.signal_symbol
    return main.get_or_set(
        f"px_{sym}_15min", main._PRICE_TTL.get("15min", 600),
        lambda: signal_engine.fetch_series(sym, "15min", 60,
                                           main.settings.twelvedata_api_key),
    )


def _check_burst(meta: dict) -> None:
    """Deteksi ledakan harga (kemungkinan berita) -> kirim INFO ke Discord.
    Bukan sinyal entry (backtest: kejar-berita tidak profit)."""
    now = datetime.now(timezone.utc)
    last = meta.get("last_burst_alert")
    if last and (now - tracker.parse_utc(last)) < timedelta(hours=BURST_COOLDOWN_H):
        return
    try:
        bars = _m15_cached()
        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        atr = signal_engine.atr_series(highs, lows, closes)[-2]
        if atr is None or len(closes) < 6:
            return
        move = closes[-2] - closes[-6]          # pergerakan ~1 jam (4 bar closed)
        if abs(move) < BURST_ATR_MULT * atr:
            return
        ctx = main.context("XAUUSD")  # type: ignore[arg-type]
        payload = notifier.format_burst_embed(
            "up" if move > 0 else "down", move, closes[-1],
            ctx.get("sentiment_bias", "flat"),
        )
        sent = main._push_discord(payload, channel="alert")
        meta["last_burst_alert"] = now.isoformat(timespec="seconds")
        print(f"[burst  ] {move:+.1f} USD/jam terdeteksi (dikirim={sent})")
    except Exception as e:  # noqa: BLE001
        print("[burst  ] ERROR:", e)


def _daily_digest(entries: list[dict], meta: dict) -> None:
    """Ringkasan harian 1x saat sesi London buka -> tiap hari pasti ada kabar."""
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    if now.hour != DIGEST_HOUR_UTC or meta.get("last_digest_date") == today:
        return
    if not signal_engine.market_open(now):
        return
    try:
        ctx = main.context("XAUUSD")  # type: ignore[arg-type]
        sent_d = ctx.get("sentiment") or {}
        bars = _m15_cached()
        h = main._signal_for("XAUUSD", EQUITY, "harian")
        i = main._signal_for("XAUUSD", EQUITY, "intraday")
        v2_txt, sh_txt = _stats_texts(entries)
        opens = [f"{e['profile']} {e['side'].upper()} @ {e['entry']}"
                 for e in entries if e.get("status") == "open" and _is_v2(e)]
        payload = notifier.format_digest_embed({
            "price": bars[-1]["close"],
            "trend_harian": h.get("trend"), "trend_intraday": i.get("trend"),
            "sent_bias": ctx.get("sentiment_bias"), "sent_score": sent_d.get("score"),
            "headlines": sent_d.get("headlines_total"),
            "cot_bias": (ctx.get("cot") or {}).get("bias"),
            "stats": v2_txt, "shadow_stats": sh_txt,
            "open_positions": ", ".join(opens) if opens else "tidak ada",
        })
        sent = main._push_discord(payload, channel="analysis")
        meta["last_digest_date"] = today
        print(f"[digest ] ringkasan harian dikirim={sent}")
    except Exception as e:  # noqa: BLE001
        print("[digest ] ERROR:", e)


def _feed_due(meta: dict, key: str, hours: float) -> bool:
    last = meta.get(key)
    if last and (datetime.now(timezone.utc) - tracker.parse_utc(last)) < timedelta(hours=hours):
        return False
    return True


def _mark(meta: dict, key: str) -> None:
    meta[key] = datetime.now(timezone.utc).isoformat(timespec="seconds")


def _market_feeds(meta: dict) -> None:
    """Isi channel MARKET CENTER: harga, berita, kalender, dolar, prediksi."""
    now = datetime.now(timezone.utc)
    if not signal_engine.market_open(now):
        return

    # 👑 gold-price: tiap jam
    if _feed_due(meta, "last_price", 1.0):
        try:
            bars = _m15_cached()
            c = [b["close"] for b in bars]
            payload = notifier.format_price_embed(
                c[-1], c[-1] - c[-5], c[-1] - c[0],
                max(b["high"] for b in bars), min(b["low"] for b in bars),
            )
            main._push_discord(payload, channel="price")
            _mark(meta, "last_price")
            print("[price  ] update harga dikirim")
        except Exception as e:  # noqa: BLE001
            print("[price  ] ERROR:", e)

    # 🥇 market-news-gold: headline khusus forex/gold/USD ter-skor (tiap 2 jam)
    if _feed_due(meta, "last_news", 2.0):
        try:
            from fetchers import sentiment as sen
            # 🥇 rich items khusus gold/forex/USD (judul+link+gambar+sumber)
            rich = sen.fetch_news_rich(main.settings.sentiment_feeds)
            seen = set(meta.get("sent_titles", []))
            gold_items = []
            for it in rich:
                t = it["title"].lower()
                k = t[:60]
                if k in seen or not sen._is_relevant(t):
                    continue
                it["score"] = sen._score_one(t)
                gold_items.append((it, k))
            gold_items.sort(key=lambda x: -abs(x[0].get("score", 0)))
            if gold_items and main.settings.discord_channels.get("news_gold"):
                def tag(it):
                    s = it.get("score", 0)
                    return "🟢 " if s > 0 else "🔴 " if s < 0 else "⚪ "
                top_g = [i for i, _ in gold_items[:5]]
                sen.enrich_og(top_g)   # gambar+ringkasan ala preview link
                payload = notifier.format_rich_news(top_g, 15844367, tag)
                main._push_discord(payload, channel="news_gold")
                meta["sent_titles"] = (list(seen) + [k for _, k in gold_items[:5]])[-150:]
                print(f"[newsGLD] {min(5, len(gold_items))} berita gold dikirim")

            # 🌎 market-news: berita keuangan UMUM (sumber diperluas)
            grich = sen.fetch_news_rich(main.settings.news_feeds_general)
            seen_g = set(meta.get("sent_titles_gen", []))
            fresh = []
            for it in grich:
                k = it["title"].lower()[:60]
                if k in seen_g:
                    continue
                fresh.append((it, k))
            if fresh:
                top_f = [i for i, _ in fresh[:5]]
                sen.enrich_og(top_f)
                payload = notifier.format_rich_news(top_f, 3447003)
                main._push_discord(payload, channel="news")
                meta["sent_titles_gen"] = (list(seen_g) + [k for _, k in fresh[:5]])[-250:]
                print(f"[newsGEN] {min(5, len(fresh))} berita umum dikirim")
            _mark(meta, "last_news")
        except Exception as e:  # noqa: BLE001
            print("[news   ] ERROR:", e)

    # 📅 economic-calendar: 1x/hari pagi London
    today = now.date().isoformat()
    if now.hour >= DIGEST_HOUR_UTC and meta.get("last_calendar_date") != today:
        try:
            from fetchers import forexfactory as ff
            events = main.get_or_set("ff_calendar", main.settings.cache_ttl_seconds,
                                     ff.fetch_calendar)
            todays = [ev for ev in events
                      if str(ev.get("time_utc", "")).startswith(today)
                      and ev.get("currency") == "USD"
                      and ev.get("impact") in ("high", "medium")]
            main._push_discord(notifier.format_calendar_embed(todays), channel="calendar")
            meta["last_calendar_date"] = today
            print(f"[calendr] {len(todays)} event USD dikirim")
        except Exception as e:  # noqa: BLE001
            print("[calendr] ERROR:", e)

    # 💵 dollar-monitor: USD vs 20 mata uang (termasuk IDR), 2x/hari
    if _feed_due(meta, "last_dollar", 12.0):
        try:
            import httpx as _hx
            pairs = main.settings.dollar_pairs
            r = _hx.get("https://api.twelvedata.com/price",
                        params={"symbol": ",".join(pairs),
                                "apikey": main.settings.twelvedata_api_key},
                        timeout=25)
            data = r.json()
            prices = {}
            for p in pairs:
                v = data.get(p, {})
                if isinstance(v, dict) and v.get("price"):
                    prices[p] = float(v["price"])
            snap = meta.get("dollar_snap") or {}
            rows = []
            for p, px in prices.items():
                cur = p.replace("USD", "").replace("/", "")
                prev = snap.get(p)
                if prev:
                    pct = (px - prev) / prev * 100
                    usd_chg = -pct if p.endswith("/USD") else pct
                    rows.append((cur, round(usd_chg, 2)))
            payload = notifier.format_dollar20_embed(rows, first=not snap)
            main._push_discord(payload, channel="dollar")
            meta["dollar_snap"] = prices
            _mark(meta, "last_dollar")
            print(f"[dollar ] monitor 20 mata uang dikirim ({len(prices)} pair)")
        except Exception as e:  # noqa: BLE001
            print("[dollar ] ERROR:", e)

    # 👽 bot-prediction: hanya saat pandangan BERUBAH
    try:
        ctx = main.context("XAUUSD")  # type: ignore[arg-type]
        h = main._signal_for("XAUUSD", EQUITY, "harian")
        i = main._signal_for("XAUUSD", EQUITY, "intraday")
        sent_d = ctx.get("sentiment") or {}
        cot = (ctx.get("cot") or {}).get("bias", "flat")
        state = f"{h.get('trend')}|{i.get('trend')}|{ctx.get('sentiment_bias')}|{cot}"
        if state != meta.get("last_prediction_state"):
            votes_up = [h.get("trend") == "up", i.get("trend") == "up",
                        ctx.get("sentiment_bias") == "long", cot == "long"].count(True)
            votes_dn = [h.get("trend") == "down", i.get("trend") == "down",
                        ctx.get("sentiment_bias") == "short", cot == "short"].count(True)
            verdict = (f"CENDERUNG NAIK ({votes_up}/4 faktor)" if votes_up > votes_dn
                       else f"CENDERUNG TURUN ({votes_dn}/4 faktor)" if votes_dn > votes_up
                       else "NETRAL / tunggu konfirmasi")
            payload = notifier.format_prediction_embed(
                h.get("trend"), i.get("trend"), ctx.get("sentiment_bias"),
                sent_d.get("score"), cot, verdict,
            )
            main._push_discord(payload, channel="prediction")
            meta["last_prediction_state"] = state
            print(f"[predict] pandangan berubah -> {verdict}")
    except Exception as e:  # noqa: BLE001
        print("[predict] ERROR:", e)


def main_run() -> None:
    if not main._discord_configured():
        print("PERINGATAN: Discord belum dikonfigurasi (Secrets).")
    entries = _load_log()
    meta = _load_meta()
    _resolve_open(entries)
    _new_signals(entries)
    _check_burst(meta)
    _daily_digest(entries, meta)
    _market_feeds(meta)
    _save_log(entries)
    _save_meta(meta)
    v2_txt, sh_txt = _stats_texts(entries)
    print("REKAP v2     :", v2_txt)
    print("REKAP bayangan:", sh_txt)


if __name__ == "__main__":
    main_run()
