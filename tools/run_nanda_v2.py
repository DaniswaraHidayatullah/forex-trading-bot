"""Scheduled PAPER only. No broker credentials, MT5, or order submission."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import main
from fetchers import nanda_alerts, nanda_v2, signal_engine
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
    audit = state.get("alerts", {}).values()
    sent = [a for a in audit if a["status"] == "sent"]
    lines.append(f"Discord menerima {sum(a['kind']=='watch' for a in sent)} pantauan dan "
                 f"{sum(a['kind']=='confirm' for a in sent)} konfirmasi. WR di atas = baseline "
                 "OHLC; hasil sesudah notifikasi memerlukan tick Exness.")
    return "\n".join(lines)


def notify(state, bars, symbol, key, clock):
    now = clock()
    if now >= datetime.fromisoformat(state["start"]) + timedelta(days=30):
        return
    audit = state.setdefault("alerts", {})
    watch = nanda_alerts.watch(bars, now)
    jobs = [("watch", watch)] if watch else []
    jobs += [("confirm", e) for e in state["signals"] if "breakdown_utc" in e and e["strategy_taken"] and
             timedelta(0) <= now-datetime.fromisoformat(e["time"]) < nanda_v2.BAR]
    jobs = [(kind, e) for kind, e in jobs if audit.get(e["id"], {}).get("status")
            not in {"sent", "delivery_unknown", "already_retested", "already_hit_exit", "stale"}]
    if not jobs:
        return
    try:
        minutes = signal_engine.fetch_series(symbol, "1min", 20, key)
    except Exception:
        minutes = []  # fail closed without interrupting the paper ledgers
    for kind, e in jobs:
        checked = clock()
        row = audit[e["id"]] = dict(kind=kind, time=e["time"], checked_utc=checked.isoformat(),
                                    breakdown_utc=e["breakdown_utc"], support=e["support"])
        row["status"] = nanda_alerts.guard(e, minutes, checked, kind)
        if row["status"] != "ok":
            continue
        latest = next(b for b in reversed(minutes) if nanda_alerts._utc(b["datetime"])
                      == checked.replace(second=0, microsecond=0))
        bid = latest["close"]
        age = int((checked-datetime.fromisoformat(e["time"])).total_seconds() // 60)
        if kind == "watch":
            title = "👀 Pantau retest · Nanda XAUUSD"
            msg = (f"Support {e['support']:g} jebol · tren H1 turun. Tunggu retest pertama. "
                   f"Belum ada entry. Pantauan berakhir setelah 12 candle M15 atau gap data.")
        else:
            title = "🧪 Rejection terkonfirmasi · PAPER · Nanda V2 SELL"
            msg = (f"Support {e['support']:g} diretest lalu M15 ditutup bearish.\n"
                   f"Entry simulasi {e['entry']:g} · SL {e['sl']:g} · TP {e['tp']:g}\n"
                   f"Risiko nominal di entry simulasi: 0,10 lot ${10*e['distance']:.2f}; "
                   f"0,05 lot ${5*e['distance']:.2f}. Bukan instruksi entry.")
        row["feed_price"] = bid
        row["feed_minute"] = latest["datetime"]
        msg += f"\nUmur setup {age} mnt · harga feed M1 {bid:g} (bukan kuotasi Exness)."
        try:
            sent = _post(title, msg, "sinyal")
        except Exception:
            # A timeout does not prove Discord rejected the message. Do not blindly resend.
            row["status"] = "delivery_unknown"
            continue
        if sent:
            row.update(status="sent", acknowledged_utc=clock().isoformat())
        else:
            row["status"] = "send_failed"


def run_trial(now=None):
    if os.getenv("NANDA_V2_ENABLED", "true").lower() not in {"true", "yes", "1"}:
        return
    fixed = now
    def clock():
        return fixed or datetime.now(timezone.utc)
    now = clock()
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
    notify(state, bars, symbol, key, clock)
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
