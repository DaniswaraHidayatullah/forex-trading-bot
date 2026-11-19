"""Isolated 30-day BTCUSDT perpetual paper study, never an order runner."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import main
from fetchers import btc_futures_trial, notifier

ROOT = Path(__file__).resolve().parents[1]
TRIAL_FILE = Path(os.getenv("BTC_FUTURES_TRIAL_LOG", str(ROOT / "signals" / "btc_futures_trial.json")))


def _load() -> dict:
    if TRIAL_FILE.exists():
        return json.loads(TRIAL_FILE.read_text(encoding="utf-8"))
    return {"started_utc": None, "last_candle_utc": None, "entries": []}


def _save(state: dict) -> None:
    TRIAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    TRIAL_FILE.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def _send(e: dict) -> bool:
    channel_id = os.getenv("DISCORD_BTC_TRIAL_CHANNEL_ID", "").strip()
    # Default is log-only, to avoid presenting unvalidated futures research as a trade alert.
    if not (channel_id and main.settings.discord_bot_token):
        return False
    payload = {"allowed_mentions": {"parse": []}, "embeds": [{
        "title": "🧪 BTCUSDT Perpetual M15 · PAPER, bukan sinyal live",
        "description": (
            f"{e['setup']} · {e['side'].upper()} · candle {e['candle_utc'][:16]} UTC\n"
            f"Entry teoretis {e['entry']} · SL {e['sl']} · TP {e['tp']} · RR 1:2\n"
            "Harga masuk dari open candle berikutnya (simulasi). "
            "Belum menghitung biaya futures, funding, atau slippage. "
            "Jangan dipakai untuk entry uang asli."
        ),
        "color": 3447003, "timestamp": e["candle_utc"],
    }]}
    return notifier.send_bot(main.settings.discord_bot_token, channel_id, payload)


def run_trial(now: datetime | None = None) -> dict:
    if os.getenv("BTC_FUTURES_TRIAL_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        return {"enabled": False}
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    state = _load()
    bars = btc_futures_trial.fetch_klines()
    entries = state["entries"]
    btc_futures_trial.resolve(entries, bars, now)
    if state["started_utc"] is None:
        state["started_utc"] = now.isoformat(timespec="seconds")
    deadline = datetime.fromisoformat(state["started_utc"]) + timedelta(days=30)
    added = sent = 0
    if now < deadline:
        found, last = btc_futures_trial.candidates(bars, now, state["last_candle_utc"])
        known = {e["id"] for e in entries}
        for e in found:
            if e["id"] in known:
                continue
            candle_dt = datetime.fromisoformat(e["candle_utc"])
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
            btc_futures_trial.resolve([e], bars, now)
            today_sent = sum(x.get("notified_utc", "").startswith(now.date().isoformat())
                             for x in entries)
            if not e["late"] and today_sent < 6 and _send(e):
                e["notified_utc"] = now.isoformat(timespec="seconds")
                sent += 1
        if last:
            state["last_candle_utc"] = last
    else:
        state["finished_utc"] = deadline.isoformat(timespec="seconds")
    _save(state)
    print(f"[btc-paper] +{added} kandidat, {sent} ke Discord, total {len(entries)}")
    return {"enabled": True, "added": added, "sent": sent, "total": len(entries)}
