"""30-day XAUUSD paper-signal trial, isolated from the live signal log.

Invoked by the existing GitHub Actions runner. No broker integration or orders.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import main
from fetchers import notifier, signal_engine, trial_signals

ROOT = Path(__file__).resolve().parents[1]
TRIAL_FILE = Path(os.getenv("TRIAL_LOG", str(ROOT / "signals" / "trial_log.json")))


def _load() -> dict:
    if TRIAL_FILE.exists():
        return json.loads(TRIAL_FILE.read_text(encoding="utf-8"))
    return {"started_utc": None, "last_candle_utc": None, "entries": []}


def _save(state: dict) -> None:
    TRIAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    TRIAL_FILE.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def _paper_embed(e: dict) -> dict:
    label = "Ikut tren" if e["setup"] == "trend" else "Balik dari ekstrem"
    risk = e["risk_usd_at_02_cent_lot_assumed"]
    desc = (
        f"**{label} · {e['side'].upper()} · candle {e['candle_utc'][:16]} UTC**\n"
        f"Entry simulasi `{e['entry']}`  |  SL `{e['sl']}`  |  TP `{e['tp']}`\n"
        f"RR 1:2 · RSI {e['rsi']} · ATR {e['atr']}\n"
        f"Estimasi risiko pada 0.2 cent lot: ${risk:.2f} (asumsi; bukan rekomendasi lot).\n\n"
        "**PAPER / SHADOW — jangan dianggap instruksi entry.** "
        "Hasil akan dihitung dari candle, bukan transaksi MT5."
    )
    return {"allowed_mentions": {"parse": []}, "embeds": [{
        "title": "🧪 XAUUSD M15 · sinyal percobaan",
        "description": desc,
        "color": 3447003,
        "timestamp": e["candle_utc"],
        "footer": {"text": "Uji 30 hari · tanpa order otomatis · spread asumsi 0.30"},
    }]}


def _send_paper(e: dict) -> bool:
    channel_id = os.getenv("DISCORD_TRIAL_CHANNEL_ID", "").strip()
    if channel_id and main.settings.discord_bot_token:
        return notifier.send_bot(main.settings.discord_bot_token, channel_id, _paper_embed(e))
    return main._push_discord(_paper_embed(e), channel="sinyal")


def summarize(entries: list[dict]) -> dict[str, dict]:
    out = {}
    for setup in ("trend", "range", "all"):
        group = [e for e in entries if setup == "all" or e["setup"] == setup]
        wins = sum(e["status"] == "win" for e in group)
        losses = sum(e["status"] == "loss" for e in group)
        closed = wins + losses
        out[setup] = {
            "signals": len(group), "closed": closed, "wins": wins, "losses": losses,
            "expired": sum(e["status"] == "expired" for e in group),
            "unknown": sum(e["status"] == "unknown" for e in group),
            "winrate_pct": round(100 * wins / closed, 1) if closed else None,
            "net_r": 2 * wins - losses,
            "profit_factor": round(2 * wins / losses, 2) if losses else None,
        }
    return out


def _report_embed(entries: list[dict]) -> dict:
    stats = summarize(entries)
    lines = []
    for setup, label in (("trend", "Ikut tren"), ("range", "Balik ekstrem"), ("all", "Gabungan")):
        s = stats[setup]
        wr = f"{s['winrate_pct']}%" if s["winrate_pct"] is not None else "n/a"
        pf = f"{s['profit_factor']:.2f}" if s["profit_factor"] is not None else "n/a"
        lines.append(f"**{label}:** {s['signals']} kandidat · {s['wins']}W/{s['losses']}L "
                     f"· WR {wr} · net {s['net_r']:+g}R · PF {pf} "
                     f"· {s['unknown']} data tidak pasti")
    lines.append("\nHasil paper berbasis OHLC dan spread asumsi; bukan P/L akun MT5.")
    return {"allowed_mentions": {"parse": []}, "embeds": [{
        "title": "🧪 Hasil uji XAUUSD M15 · 30 hari",
        "description": "\n".join(lines), "color": 3447003,
    }]}


def run_trial(now: datetime | None = None) -> dict:
    if os.getenv("TRIAL_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        return {"enabled": False}
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    state = _load()
    key = main.settings.twelvedata_api_key
    if not key:
        print("[paper] TWELVEDATA_API_KEY tidak tersedia")
        return {"enabled": True, "error": "no-price-key"}
    # Keep the trial's longer history separate from the live engine's 60 bars:
    # sharing a cache here would subtly change the live RSI/ATR calculation.
    # This costs one extra M15 API request per scheduled run (~96/day).
    bars = main.get_or_set(
        f"trial_px_{main.settings.signal_symbol}_15min", 300,
        lambda: signal_engine.fetch_series(main.settings.signal_symbol, "15min", 500, key),
    )
    entries = state["entries"]
    trial_signals.resolve(entries, bars, now)
    if state["started_utc"] is None:
        state["started_utc"] = now.isoformat(timespec="seconds")
    deadline = datetime.fromisoformat(state["started_utc"]) + timedelta(days=30)
    added = sent = 0
    if now < deadline:
        found, last = trial_signals.candidates(bars, now, state["last_candle_utc"])
        known = {e["id"] for e in entries}
        for e in found:
            if e["id"] in known:
                continue
            candle_dt = datetime.fromisoformat(e["candle_utc"])
            # Do not count two simultaneous positions in the same setup.
            active = any(
                old["setup"] == e["setup"] and
                (old["status"] == "open" or
                 datetime.fromisoformat(old["exit_candle_utc"]) > candle_dt)
                for old in entries
            )
            if active:
                continue
            entries.append(e)
            known.add(e["id"])
            added += 1
            trial_signals.resolve([e], bars, now)
            # Backfilled/missed candles are logged but never sent as fresh.
            today_sent = sum(x.get("notified_utc", "").startswith(now.date().isoformat()) for x in entries)
            if not e["late"] and today_sent < 6 and _send_paper(e):
                e["notified_utc"] = now.isoformat(timespec="seconds")
                sent += 1
        if last:
            state["last_candle_utc"] = last
    else:
        state["finished_utc"] = deadline.isoformat(timespec="seconds")
        if not state.get("reported_utc") and not any(e["status"] == "open" for e in entries):
            if main._push_discord(_report_embed(entries), channel="report"):
                state["reported_utc"] = now.isoformat(timespec="seconds")
    _save(state)
    print(f"[paper] +{added} kandidat, {sent} ke Discord, total {len(entries)}; batas {deadline.isoformat()}")
    return {"enabled": True, "added": added, "sent": sent, "total": len(entries),
            "deadline_utc": deadline.isoformat()}
