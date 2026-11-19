"""Scheduled PAPER only. No broker credentials, MT5, or order submission."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import main
from fetchers import nanda_v2, signal_engine
from run_nanda_trial import _post

LOG = Path(__file__).resolve().parents[1] / "signals" / "nanda_v2_trial.json"


def report(state):
    trades = state["strategy"]["trades"]
    wins = sum(x["reason"] == "tp" for x in trades)
    losses = sum(x["reason"] == "sl" for x in trades)
    closed = wins + losses
    wr = f"{100*wins/closed:.1f}%" if closed else "n/a"
    lines = [f"Strategi: {wins} TP / {losses} SL · WR {wr} · {len(trades)-closed} tidak pasti.",
             "Akun USD Standard, modal $100 masing-masing · tanpa batas $3:"]
    for name, book in state["accounts"].items():
        assumed = sum(x["reason"] in ("gap", "ambiguous", "timeout") for x in book["trades"])
        lines.append(f"{name}: **${book['balance']:.2f}** · {len(book['trades'])} selesai "
                     f"· {book['margin_skips']} dilewati (margin) · {assumed} asumsi rugi")
    return "\n".join(lines)


def run_trial(now=None):
    if os.getenv("NANDA_V2_ENABLED", "true").lower() not in {"true", "yes", "1"}:
        return
    now = now or datetime.now(timezone.utc)
    state = json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else nanda_v2.initial(now)
    if state.get("reported_utc"):
        return
    symbol, key = main.settings.signal_symbol, main.settings.twelvedata_api_key
    if not key:
        print("[nanda-v2] no price key")
        return
    bars = main.get_or_set(f"px_{symbol}_15min", 300,
                           lambda: signal_engine.fetch_series(symbol, "15min", 500, key))
    fresh = nanda_v2.advance(state, bars, now)
    for e in fresh:
        _post("🧪 NANDA METHOD V2 · Rejection SELL XAUUSD",
              f"Support {e['support']:g} diretest lalu candle ditutup bearish.\n"
              f"Entry paper {e['entry']:g} · SL {e['sl']:g} · TP {e['tp']:g} · RR 1:2\n"
              f"Risiko nominal sampai SL: 0,1 lot ${10*e['distance']:.2f}; "
              f"0,05 lot ${5*e['distance']:.2f}. Unlimited simulasi; stop-out tetap berlaku. "
              "Bukan instruksi entry.", "sinyal")
    books = [state["strategy"], *state["accounts"].values()]
    if (now >= datetime.fromisoformat(state["start"]) + timedelta(days=30)
            and not any(b["pos"] for b in books)
            and _post("📊 NANDA METHOD V2 · Hasil paper 30 hari", report(state), "report")):
        state["reported_utc"] = now.isoformat()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    tmp = LOG.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(LOG)
    print(f"[nanda-v2] {len(state['signals'])} setup, {len(fresh)} baru/fresh")
