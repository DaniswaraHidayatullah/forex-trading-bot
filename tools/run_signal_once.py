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
EQUITY = float(os.getenv("EQUITY", "100"))
EXPIRE_DAYS = {"Scalping": 1, "Intraday": 3, "Swing": 10}
_LEVEL = {"none": 0, "medium": 2, "strong": 3}


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
        stats_text = tracker.stats_line(tracker.summarize(entries))
        payload = notifier.format_outcome_embed(e, stats_text)
        sent = main._push_discord(payload)
        print(f"[resolve] {e.get('profile')} {e['side']} -> {e['status']} (dikirim={sent})")


def _new_signals(entries: list[dict]) -> None:
    profiles = [p.strip() for p in main.settings.signal_profiles.split(",") if p.strip()]
    min_level = _LEVEL.get(main.settings.signal_min_confidence, 2)

    for profile in profiles:
        label = signal_engine.PROFILES.get(profile, signal_engine.PROFILES["intraday"])["label"]
        if any(e.get("status") == "open" and e.get("profile") == label for e in entries):
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

        if side not in ("buy", "sell"):
            continue
        if sig.get("confidence_level", 0) < min_level:
            print(f"[{profile}] keyakinan < {main.settings.signal_min_confidence} -> tidak dikirim")
            continue

        sent = main._push_discord(sig)
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


def main_run() -> None:
    if not main._discord_configured():
        print("PERINGATAN: Discord belum dikonfigurasi (Secrets).")
    entries = _load_log()
    _resolve_open(entries)
    _new_signals(entries)
    _save_log(entries)
    print("REKAP:", tracker.stats_line(tracker.summarize(entries)))


if __name__ == "__main__":
    main_run()
