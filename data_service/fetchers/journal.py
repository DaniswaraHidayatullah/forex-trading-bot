"""Sinkron jurnal trading ke Google Sheets (Scalper's Boys Journal).

Bot mengisi KOLOM OBJEKTIF (A-Q) di tab XAUUSD/BTCUSD saat sinyal keluar,
lalu meng-update Result (Q) + Exit (L) saat kena TP/SL. Kolom rumus
(P/L, Balance, Win Rate, dll) & kolom manual (Emosi, Disiplin, Catatan pribadi)
TIDAK disentuh. Semua dibungkus try/except di pemanggil -> gagal sheet tak
pernah menjatuhkan bot.

Kredensial dari env: GSHEET_SA_JSON (isi file service-account JSON) + GSHEET_ID.
Kolom (row header=7, data mulai row 8):
  A TradeID B Tanggal C Bulan D Tahun E Waktu F Arah G Setup H Sesi
  I Entry J TP K SL L Exit M Lot N Risk$ O PotProfit$ P R:R Q Result
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

_ID = os.getenv("GSHEET_ID", "")
_SA = os.getenv("GSHEET_SA_JSON", "")
_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_DATA_START = 8

_MONTHS_ID = ["", "Januari", "Februari", "Maret", "April", "Mei", "Juni",
              "Juli", "Agustus", "September", "Oktober", "November", "Desember"]

_gc = None


def enabled() -> bool:
    return bool(_ID and _SA)


def _client():
    global _gc
    if _gc is None:
        import gspread
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_info(json.loads(_SA), scopes=_SCOPES)
        _gc = gspread.authorize(creds)
    return _gc


def _tab(symbol: str) -> str:
    return "BTCUSD" if str(symbol).upper().startswith("BTC") else "XAUUSD"


def _session(hour_utc: int) -> str:
    if hour_utc < 7:
        return "Asia"
    if hour_utc < 12:
        return "London"
    if hour_utc < 21:
        return "New York"
    return "Asia"


def _parse_utc(ts: str | None) -> datetime:
    if ts:
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
        except (ValueError, TypeError):
            pass
    return datetime.now(timezone.utc)


def append_trade(symbol: str, sig: dict[str, Any]) -> int | None:
    """Tulis 1 baris trade OPEN. Return nomor baris, atau None kalau nonaktif/gagal."""
    if not enabled():
        return None
    tab = _tab(symbol)
    ws = _client().open_by_key(_ID).worksheet(tab)
    col_a = ws.col_values(1)
    row = max(len(col_a) + 1, _DATA_START)

    dt = _parse_utc(sig.get("time_utc"))
    ymd = dt.strftime("%Y%m%d")
    seq = sum(1 for v in col_a if str(v).startswith(f"{tab}-{ymd}-")) + 1
    trade_id = f"{tab}-{ymd}-{seq:03d}"
    hh = dt.hour % 12 or 12
    waktu = f"{hh}:{dt.minute:02d}:{dt.second:02d} {'AM' if dt.hour < 12 else 'PM'}"
    side = str(sig.get("signal", "")).upper()

    # A..Q (17 kolom); L (Exit) dikosongkan sampai trade ditutup.
    values = [
        trade_id, f"{dt.month}/{dt.day}/{dt.year}", _MONTHS_ID[dt.month], dt.year,
        waktu, side, sig.get("profile", "Signal"), _session(dt.hour),
        sig.get("entry"), sig.get("tp"), sig.get("sl"), "",
        sig.get("suggested_lot"), sig.get("risk_per_001"), sig.get("reward_per_001"),
        float(sig.get("rr", 2)), "OPEN",
    ]
    ws.update(values=[values], range_name=f"A{row}:Q{row}", value_input_option="USER_ENTERED")

    note = (f"{sig.get('profile')} • RR 1:{float(sig.get('rr', 2)):g} • "
            f"{sig.get('confidence', '')} • sentimen {sig.get('sentiment_bias')} "
            f"({sig.get('sentiment_score')}) • RSI {sig.get('rsi')}")
    ws.update(values=[[note]], range_name=f"AC{row}", value_input_option="USER_ENTERED")
    return row


def close_trade(symbol: str, row: int, status: str, exit_price: float | None) -> bool:
    """Update Result (Q) + Exit (L) saat trade selesai. status: win|loss|expired."""
    if not enabled() or not row:
        return False
    result = {"win": "TP", "loss": "SL", "expired": "BE"}.get(status, "BE")
    ws = _client().open_by_key(_ID).worksheet(_tab(symbol))
    if exit_price is not None:
        ws.update(values=[[exit_price]], range_name=f"L{row}", value_input_option="USER_ENTERED")
    ws.update(values=[[result]], range_name=f"Q{row}", value_input_option="USER_ENTERED")
    return True
