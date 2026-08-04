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
from fetchers import journal, notifier, signal_engine, tracker  # noqa: E402

LOG_FILE = Path(os.getenv("SIGNAL_LOG", str(ROOT / "signals" / "log.json")))
META_FILE = LOG_FILE.parent / "meta.json"
EQUITY = float(os.getenv("EQUITY", "100"))
EXPIRE_DAYS = {"Harian": 2, "Scalping": 1, "Intraday": 3, "Swing": 10,
               "BTC": 3, "MeanRev": 2}
_LEVEL = {"none": 0, "medium": 2, "strong": 3}
BURST_ATR_MULT = 3.0        # ledakan = gerak 1 jam >= 3x ATR(M15)
BURST_COOLDOWN_H = 2        # jangan alert ledakan lagi dalam N jam
DIGEST_HOUR_UTC = 7         # ringkasan harian saat London buka (~14:00 WIB)
MAX_CONCURRENT = 2          # maks posisi terbuka per profil (opsi B)
DEDUP_MIN = 14              # menit; cegah 2 sinyal di candle M15 yang sama


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


def _conflict_skips(sigs: dict[str, dict], profiles: list[str]) -> set[str]:
    """Profil yang di-skip krn konflik timeframe (ada BUY & SELL sekaligus).
    Skip yang keyakinan lebih rendah; kalau seri -> skip semua yang konflik.
    Cegah sinyal hedge (BUY+SELL barengan = bayar spread 2x, nol hasil)."""
    clash = [p for p in profiles if sigs.get(p, {}).get("signal") in ("buy", "sell")]
    if len({sigs[p]["signal"] for p in clash}) <= 1:
        return set()   # searah semua / cuma satu arah -> tak ada konflik
    maxlvl = max(sigs[p].get("confidence_level", 0) for p in clash)
    winners = [p for p in clash if sigs[p].get("confidence_level", 0) == maxlvl]
    return set(clash) if len(winners) != 1 else set(clash) - set(winners)


def _journal_append(entry: dict, symbol: str, sig: dict) -> None:
    """Tulis trade baru ke Google Sheets; simpan baris di entry (aman kalau gagal)."""
    if not journal.enabled():
        return
    try:
        row = journal.append_trade(symbol, sig)
        if row:
            entry["sheet_row"] = row
            print(f"   [jurnal] {symbol} baris {row} ditulis")
    except Exception as e:  # noqa: BLE001 - gagal sheet jangan jatuhkan bot
        print("   [jurnal] gagal tulis:", repr(e)[:120])


def _stats_texts(entries: list[dict]) -> tuple[str, str]:
    """(stats sinyal v2, stats bayangan) sebagai teks siap tampil."""
    v2 = tracker.summarize([e for e in entries if _is_v2(e)])
    sh = tracker.summarize([e for e in entries if e.get("shadow")])
    v2_txt = f"{tracker.stats_line(v2)} (sejak 14 Jul, sistem v2)"
    sh_txt = (f"{sh['winrate_pct']}% ({sh['wins']}W/{sh['losses']}L, "
              f"{sh['open']} terbuka) — makin RENDAH makin bagus gate-nya")
    return v2_txt, sh_txt


def _td_symbol(e: dict) -> str:
    """Simbol Twelve Data untuk sebuah entri (BTC vs emas)."""
    if e.get("symbol") == "BTCUSD":
        return main.settings.btc_symbol
    return main.settings.signal_symbol


def _resolve_open(entries: list[dict]) -> None:
    """Cek sinyal terbuka: sudah kena TP/SL? Kirim rekap + update status.
    Di-fetch PER SIMBOL (emas & BTC beda deret harga)."""
    open_entries = [e for e in entries if e.get("status") == "open"]
    if not open_entries:
        return
    api_key = main.settings.twelvedata_api_key
    if not api_key:
        print("skip resolve: tidak ada TWELVEDATA_API_KEY")
        return

    now = datetime.now(timezone.utc)
    # Kelompokkan per simbol -> 1 fetch M5 per simbol (hemat kredit).
    by_sym: dict[str, list[dict]] = {}
    for e in open_entries:
        by_sym.setdefault(_td_symbol(e), []).append(e)

    bars_by_sym: dict[str, list[dict]] = {}
    for sym, grp in by_sym.items():
        oldest = min(tracker.parse_utc(e["time_utc"]) for e in grp)
        minutes = (now - oldest).total_seconds() / 60
        size = min(5000, max(50, int(minutes / 5) + 20))
        try:
            bars_by_sym[sym] = signal_engine.fetch_series(sym, "5min", size, api_key)
        except Exception as e:  # noqa: BLE001
            print(f"resolve: gagal ambil M5 {sym}:", e)

    for e in open_entries:
        bars = bars_by_sym.get(_td_symbol(e))
        if bars is None:
            continue
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
        if e.get("sheet_row"):
            try:
                exitp = (e.get("tp") if e["status"] == "win"
                         else e.get("sl") if e["status"] == "loss" else e.get("entry"))
                journal.close_trade(e.get("symbol", "XAUUSD"), e["sheet_row"],
                                    e["status"], exitp)
            except Exception as ex:  # noqa: BLE001
                print("   [jurnal] gagal update:", repr(ex)[:120])
        if e.get("shadow"):
            # Bayangan (diblokir sentimen): dilacak diam-diam, tanpa Discord.
            print(f"[shadow ] {e.get('profile')} {e['side']} -> {e['status']}")
            continue
        pf = _portfolio_stats(entries)
        stats_text = (f"Akun Cent: **${pf['balance']:,.2f}** ({pf['wins']}W/{pf['losses']}L · "
                      f"WR {pf['wr']:.0f}% · PF {pf['pf']:.2f} · Net "
                      f"{'+' if pf['net_usd'] >= 0 else '−'}${abs(pf['net_usd']):,.2f})")
        pnl = abs(_trade_pnl(e)) if e["status"] in ("win", "loss") else 0.0
        payload = notifier.format_outcome_embed(e, stats_text, pnl)
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

    # KONFLIK TIMEFRAME (mis. Harian H1 naik vs Intraday H4 turun) -> pasar
    # bimbang. Auto-skip yang keyakinannya lebih rendah; seri -> skip dua-duanya.
    _sigs: dict[str, dict] = {}
    for p in profiles:
        try:
            _sigs[p] = main._signal_for("XAUUSD", EQUITY, p)
        except Exception:  # noqa: BLE001
            _sigs[p] = {}
    skip_profiles = _conflict_skips(_sigs, profiles)
    if skip_profiles:
        print(f"[konflik TF] { {p: _sigs[p].get('signal') for p in _sigs} } "
              f"-> skip {sorted(skip_profiles)}")

    now = datetime.now(timezone.utc)
    for profile in profiles:
        if profile in skip_profiles:
            print(f"[{profile}] SKIP — konflik timeframe (pasar bimbang, cegah hedge)")
            continue
        label = signal_engine.PROFILES.get(profile, signal_engine.PROFILES["intraday"])["label"]
        open_real = [e for e in entries if e.get("status") == "open"
                     and e.get("profile") == label and not e.get("shadow")]
        open_shadow_list = [e for e in entries if e.get("status") == "open"
                            and e.get("profile") == label and e.get("shadow")]
        open_shadow = len(open_shadow_list) >= MAX_CONCURRENT
        # Dedup candle: jangan buka posisi baru bila ada posisi profil ini yang
        # dibuka < DEDUP_MIN menit lalu (cegah dobel di candle M15 yang sama).
        recent = any((now - tracker.parse_utc(e["time_utc"])) < timedelta(minutes=DEDUP_MIN)
                     for e in open_real + open_shadow_list)
        if len(open_real) >= MAX_CONCURRENT or recent:
            print(f"[{profile}] {len(open_real)} posisi terbuka (maks {MAX_CONCURRENT})"
                  f"{' / candle sama' if recent else ''} -> tunggu")
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
        entry = {
            "id": uuid.uuid4().hex[:8],
            "symbol": "XAUUSD",
            "profile": sig.get("profile"),
            "side": side,
            "entry": sig.get("entry"), "sl": sig.get("sl"), "tp": sig.get("tp"),
            "rr": sig.get("rr", 3),
            "risk_usd": sig.get("risk_per_001"), "reward_usd": sig.get("reward_per_001"),
            "confidence": sig.get("confidence_level"),
            "version": sig.get("version", "v1"), "momentum": sig.get("momentum"),
            "time_utc": sig.get("time_utc"),
            "status": "open",
        }
        _journal_append(entry, "XAUUSD", sig)
        entries.append(entry)
        print(f"[{profile}] SINYAL {side.upper()} {sig.get('confidence_stars')} "
              f"dikirim={sent} @ {sig.get('entry')}")


def _new_btc_signals(entries: list[dict]) -> None:
    """Sinyal BTCUSD (domain terpisah, 24/7). Teknikal-only -> maks ⭐⭐.
    Log ke file yang sama dgn field symbol='BTCUSD' agar portofolio gabungan."""
    if not main.settings.btc_enabled:
        return
    profiles = [p.strip() for p in main.settings.btc_profiles.split(",") if p.strip()]
    now = datetime.now(timezone.utc)
    for profile in profiles:
        prof = signal_engine.BTC_PROFILES.get(profile)
        if not prof:
            continue
        label = prof["label"]
        open_btc = [e for e in entries if e.get("status") == "open"
                    and e.get("symbol") == "BTCUSD" and e.get("profile") == label]
        recent = any((now - tracker.parse_utc(e["time_utc"])) < timedelta(minutes=DEDUP_MIN)
                     for e in open_btc)
        if len(open_btc) >= MAX_CONCURRENT or recent:
            print(f"[btc:{profile}] {len(open_btc)} posisi terbuka (maks {MAX_CONCURRENT})"
                  f"{' / candle sama' if recent else ''} -> tunggu")
            continue
        try:
            sig = main._btc_signal_for(EQUITY, profile)
        except Exception as e:  # noqa: BLE001
            print(f"[btc:{profile}] ERROR: {e}")
            continue

        side = sig.get("signal", "none")
        print(f"[btc:{profile}] {side} | sentimen={sig.get('sentiment_bias')}"
              f"/{sig.get('sentiment_score')} | {sig.get('reason')}")
        if side not in ("buy", "sell"):
            continue

        payload = notifier.format_embed(sig)
        if sig.get("confidence_level", 0) >= 3:
            payload["content"] = "@everyone ⭐⭐⭐ SINYAL BTC KUAT — teknikal+sentimen+momentum searah!"
        sent = main._push_discord(payload, channel="btc_signal")
        entry = {
            "id": uuid.uuid4().hex[:8],
            "symbol": "BTCUSD",
            "profile": sig.get("profile"),
            "side": side,
            "entry": sig.get("entry"), "sl": sig.get("sl"), "tp": sig.get("tp"),
            "rr": sig.get("rr", 2),
            "risk_usd": sig.get("risk_per_001"), "reward_usd": sig.get("reward_per_001"),
            "confidence": sig.get("confidence_level"),
            "version": sig.get("version", "v1"), "momentum": sig.get("momentum"),
            "time_utc": sig.get("time_utc"),
            "status": "open",
        }
        _journal_append(entry, "BTCUSD", sig)
        entries.append(entry)
        print(f"[btc:{profile}] SINYAL {side.upper()} {sig.get('confidence_stars')} "
              f"dikirim={sent} @ {sig.get('entry')}")


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


def _lot_mult(e: dict) -> float:
    """$ P/L per 1 unit gerak harga (dari config; emas ~0.2, BTC ~0.005)."""
    s = main.settings
    if e.get("symbol") == "BTCUSD":
        return s.btc_usd_per_pip / s.btc_pip_price
    return s.usd_per_pip / s.pip_price


def _trade_pnl(e: dict) -> float:
    """P/L $ NYATA dari harga (skala cent 0.2): win=+jarak TP, loss=-jarak SL."""
    en = float(e.get("entry") or 0)
    m = _lot_mult(e)
    if e["status"] == "win":
        return abs(float(e.get("tp") or en) - en) * m
    return -abs(en - float(e.get("sl") or en)) * m


def _cent_era(e: dict) -> bool:
    """Trade sejak era CENT (fresh start) — pra-cent tak dihitung."""
    try:
        return tracker.parse_utc(e["time_utc"]) >= tracker.parse_utc(main.settings.portfolio_since)
    except (ValueError, TypeError, KeyError):
        return False


def _portfolio_stats(entries: list[dict]) -> dict:
    """Ekuitas akun CENT ($100) dari trade v2 era-cent, P/L $ nyata dari harga."""
    tr = sorted([e for e in entries if _is_v2(e) and e["status"] in ("win", "loss")
                 and _cent_era(e)], key=lambda e: e.get("time_utc", ""))
    start = main.settings.portfolio_start
    bal, peak, dd, net = start, start, 0.0, 0.0
    eq = [start]
    cw = cl = mcw = mcl = wins = losses = 0
    gp = gl = 0.0
    per_sym: dict[str, list[int]] = {"XAUUSD": [0, 0], "BTCUSD": [0, 0]}
    for e in tr:
        pnl = _trade_pnl(e)
        net += pnl
        bal += pnl
        eq.append(bal)
        peak = max(peak, bal)
        dd = min(dd, bal - peak)
        sym = e.get("symbol", "XAUUSD")
        per_sym.setdefault(sym, [0, 0])
        if e["status"] == "win":
            wins += 1
            gp += pnl
            cw, cl = cw + 1, 0
            mcw = max(mcw, cw)
            per_sym[sym][0] += 1
        else:
            losses += 1
            gl += -pnl
            cl, cw = cl + 1, 0
            mcl = max(mcl, cl)
            per_sym[sym][1] += 1
    n = wins + losses
    open_n = sum(1 for e in entries if _is_v2(e) and e["status"] == "open" and _cent_era(e))
    step = max(1, len(eq) // 24)
    return {
        "start": start, "balance": bal, "target": main.settings.portfolio_target,
        "spark": notifier._sparkline(eq[::step]) if len(eq) > 2 else "—",
        "net_usd": net, "wr": (wins / n * 100) if n else 0, "pf": (gp / gl) if gl else 0,
        "wins": wins, "losses": losses, "dd_usd": dd,
        "mcw": mcw, "mcl": mcl, "open": open_n, "per_sym": per_sym,
    }


def _portfolio_dashboard(entries: list[dict], meta: dict) -> None:
    if not _feed_due(meta, "last_portfolio", 12.0):
        return
    p = _portfolio_stats(entries)
    if p["wins"] + p["losses"] == 0:
        return
    main._push_discord(notifier.format_portfolio(p), channel="portofolio")
    _mark(meta, "last_portfolio")
    print(f"[portfol] dashboard dikirim (saldo ${p['balance']:.0f})")


def _weekly_recap(entries: list[dict], meta: dict) -> None:
    now = datetime.now(timezone.utc)
    # Minggu malam (weekday 6) jam >= 20 UTC, 1x per minggu
    wk = now.isocalendar()
    key = f"{wk[0]}-W{wk[1]}"
    if now.weekday() != 6 or now.hour < 20 or meta.get("last_weekly") == key:
        return
    start = now - timedelta(days=7)
    wtr = [e for e in entries if _is_v2(e) and e["status"] in ("win", "loss")
           and _cent_era(e) and tracker.parse_utc(e["time_utc"]) >= start]
    if not wtr:
        meta["last_weekly"] = key
        return
    wins = sum(1 for e in wtr if e["status"] == "win")
    losses = len(wtr) - wins
    net_usd = sum(_trade_pnl(e) for e in wtr)   # $ nyata era cent

    def _lbl(e):
        return f"{e.get('symbol', 'XAU')[:3]} {e['side'].upper()} @ {e['entry']}"
    best = max(wtr, key=_trade_pnl)
    worst = min(wtr, key=_trade_pnl)
    note = ("Net positif — pertahankan disiplin." if net_usd > 0 else
            "Net negatif — normal di sistem WR rendah, jangan ubah rencana.")
    main._push_discord(notifier.format_weekly({
        "period": f"{start.date()} .. {now.date()}",
        "n": len(wtr), "wins": wins, "losses": losses,
        "wr": wins / len(wtr) * 100, "net_usd": net_usd,
        "best": _lbl(best) + f" (+${_trade_pnl(best):,.2f})" if wins else "—",
        "worst": _lbl(worst) + f" (−${abs(_trade_pnl(worst)):,.2f})" if losses else "—",
        "note": note,
    }), channel="weekly")
    meta["last_weekly"] = key
    print("[weekly ] rekap mingguan dikirim")


def _news_reminder(meta: dict) -> None:
    """Ping ~30 mnt sebelum event USD high-impact (dari kalender)."""
    try:
        from fetchers import forexfactory as ff
        events = main.get_or_set("ff_calendar", main.settings.cache_ttl_seconds, ff.fetch_calendar)
    except Exception:  # noqa: BLE001
        return
    now = datetime.now(timezone.utc)
    alerted = set(meta.get("news_alerted", []))
    for ev in events:
        if ev.get("currency") != "USD" or ev.get("impact") != "high":
            continue
        try:
            et = tracker.parse_utc(str(ev.get("time_utc")))
        except (ValueError, TypeError):
            continue
        mins = (et - now).total_seconds() / 60
        key = f"{ev.get('title')}|{ev.get('time_utc')}"
        if 20 <= mins <= 40 and key not in alerted:
            main._push_discord(notifier.format_news_reminder(ev), channel="alert")
            alerted.add(key)
            meta["news_alerted"] = list(alerted)[-50:]
            print(f"[remind ] {ev.get('title')} ~{mins:.0f} mnt lagi")
            break


NEWS_PRED_MIN = 25          # menit: batas bawah window prediksi pra-berita
NEWS_PRED_MAX = 100         # menit: batas atas (lebar krn cron jarang jalan)


def _news_prediction(meta: dict) -> None:
    """Kirim skenario PRA-berita utk event USD high-impact yg akan rilis:
    kondisi teknikal+sentimen SEKARANG + skenario arah emas (aktual vs forecast).
    """
    now = datetime.now(timezone.utc)
    if not signal_engine.market_open(now):
        return
    try:
        from fetchers import forexfactory as ff
        from fetchers import news_predict as npd
        events = main.get_or_set("ff_calendar", main.settings.cache_ttl_seconds,
                                 ff.fetch_calendar)
    except Exception:  # noqa: BLE001
        return
    predicted = set(meta.get("news_predicted", []))
    tech: dict | None = None
    for ev in events:
        if ev.get("currency") != "USD" or ev.get("impact") != "high":
            continue
        try:
            et = tracker.parse_utc(str(ev.get("time_utc")))
        except (ValueError, TypeError):
            continue
        mins = (et - now).total_seconds() / 60
        key = f"{ev.get('title')}|{ev.get('time_utc')}"
        if not (NEWS_PRED_MIN <= mins <= NEWS_PRED_MAX) or key in predicted:
            continue
        try:
            if tech is None:   # hitung sekali, dipakai semua event dekat
                h = main._signal_for("XAUUSD", EQUITY, "harian")
                i = main._signal_for("XAUUSD", EQUITY, "intraday")
                ctx = main.context("XAUUSD")  # type: ignore[arg-type]
                sent = ctx.get("sentiment") or {}
                lean, ups, dns = npd.current_lean(
                    h.get("trend"), i.get("trend"), h.get("momentum"),
                    ctx.get("sentiment_bias"))
                tech = {"h_trend": h.get("trend"), "i_trend": i.get("trend"),
                        "momentum": h.get("momentum"),
                        "sent_bias": ctx.get("sentiment_bias"),
                        "sent_score": sent.get("score"),
                        "lean": lean, "ups": ups, "dns": dns}
            scen = npd.scenario(ev.get("title", ""))
            info = {**tech, **scen, "mins": mins}
            main._push_discord(notifier.format_news_prediction(ev, info),
                               channel="news_prediction")
            predicted.add(key)
            meta["news_predicted"] = list(predicted)[-50:]
            print(f"[predikB] {ev.get('title')} ~{mins:.0f}mnt -> lean {tech['lean']}")
            break   # satu prediksi per siklus cukup
        except Exception as e:  # noqa: BLE001
            print("[predikB] ERROR:", e)
            return


def main_run() -> None:
    if not main._discord_configured():
        print("PERINGATAN: Discord belum dikonfigurasi (Secrets).")
    entries = _load_log()
    meta = _load_meta()
    _resolve_open(entries)
    _new_signals(entries)
    _new_btc_signals(entries)
    _check_burst(meta)
    _daily_digest(entries, meta)
    _market_feeds(meta)
    _portfolio_dashboard(entries, meta)
    _weekly_recap(entries, meta)
    _news_reminder(meta)
    _news_prediction(meta)
    _save_log(entries)
    _save_meta(meta)
    # Rekap dipisah per VERSI strategi (v1 arsip vs v2 aktif).
    ex_all = [e for e in entries if _is_v2(e)]
    ex_v1 = [e for e in ex_all if e.get("version", "v1") == "v1"]
    ex_v2 = [e for e in ex_all if e.get("version") == "v2"]
    print("REKAP v1 (arsip)   :", tracker.stats_line(tracker.summarize(ex_v1)))
    print("REKAP v2 (aktif)   :", tracker.stats_line(tracker.summarize(ex_v2)))
    # per-bintang v2 (uji apakah ⭐⭐⭐ v2 sudah lepas dari efek arah)
    for star in (3, 2, 1):
        g = [e for e in ex_v2 if e.get("confidence") == star]
        if g:
            print(f"   v2 {star}*: {tracker.stats_line(tracker.summarize(g))}")
    # Pisah per SIMBOL (emas vs BTC) — domain terpisah, dinilai sendiri-sendiri.
    gold = [e for e in ex_all if e.get("symbol", "XAUUSD") != "BTCUSD"]
    btc = [e for e in ex_all if e.get("symbol") == "BTCUSD"]
    print("REKAP emas (XAUUSD):", tracker.stats_line(tracker.summarize(gold)))
    print("REKAP BTC  (BTCUSD):", tracker.stats_line(tracker.summarize(btc)))


if __name__ == "__main__":
    main_run()
