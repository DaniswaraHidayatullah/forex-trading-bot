"""Sekali jalan: hitung sinyal lalu kirim ke Discord bila ada sinyal BARU.

Dipakai oleh GitHub Actions (cron) supaya bot jalan GRATIS & permanen tanpa
server nyala 24/7. Anti-spam pakai file state (last_side per profil) yang
dipersist lewat Actions cache.

Rahasia (TWELVEDATA_API_KEY, DISCORD_BOT_TOKEN/CHANNEL_ID atau DISCORD_WEBHOOK_URL)
dibaca dari environment (GitHub Secrets). Tidak ada rahasia di kode.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# main.py memakai import top-level (from config import ...), jadi data_service
# harus ada di sys.path.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data_service"))

import main  # noqa: E402

STATE_FILE = Path(os.getenv("STATE_FILE", str(ROOT / "signal_state.json")))
EQUITY = float(os.getenv("EQUITY", "100"))
_LEVEL = {"none": 0, "medium": 2, "strong": 3}


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def main_run() -> None:
    settings = main.settings
    profiles = [p.strip() for p in settings.signal_profiles.split(",") if p.strip()]
    min_level = _LEVEL.get(settings.signal_min_confidence, 0)
    last = _load_state()

    if not main._discord_configured():
        print("PERINGATAN: Discord belum dikonfigurasi (set Secrets). Lanjut hitung saja.")

    for profile in profiles:
        try:
            sig = main._signal_for("XAUUSD", EQUITY, profile)
            side = sig.get("signal", "none")
            level = sig.get("confidence_level", 0)
            if side in ("buy", "sell") and level >= min_level:
                if last.get(profile) != side:
                    sent = main._push_discord(sig)
                    last[profile] = side
                    print(f"[{profile}] {side.upper()} dikirim={sent} | {sig.get('reason')}")
                else:
                    print(f"[{profile}] {side.upper()} sama spt sebelumnya -> skip")
            elif side == "none":
                last[profile] = None
                print(f"[{profile}] none -> {sig.get('reason')}")
            else:
                print(f"[{profile}] {side} keyakinan kurang (level {level}) -> skip")
        except Exception as e:  # noqa: BLE001 - jangan gagalkan job
            print(f"[{profile}] ERROR: {e}")

    try:
        STATE_FILE.write_text(json.dumps(last))
    except OSError as e:
        print("gagal simpan state:", e)


if __name__ == "__main__":
    main_run()
