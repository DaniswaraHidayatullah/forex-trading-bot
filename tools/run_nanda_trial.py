"""Separate 30-day paper journal for Nanda Method."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean

import main
from fetchers import nanda_method, signal_engine

ROOT = Path(__file__).resolve().parents[1]
LOG = Path(os.getenv("NANDA_TRIAL_LOG", str(ROOT / "signals" / "nanda_trial.json")))


def summarize(entries: list[dict]) -> dict:
    counts = {status: sum(e["status"] == status for e in entries)
              for status in ("open", "win", "loss", "unknown", "expired")}
    closed = sorted((e for e in entries if "result_r" in e),
                    key=lambda e: e["exit_candle_utc"])
    results = [e["result_r"] for e in closed]
    equity = peak = drawdown = 0
    for result in results:
        equity += result
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {"signals": len(entries), "closed": len(closed), **counts,
            "win_rate_pct": round(100 * counts["win"] / len(closed), 1) if closed else None,
            "net_r": equity, "expectancy_r": round(mean(results), 3) if closed else None,
            "max_drawdown_r": drawdown,
            "profit_factor": round(2 * counts["win"] / counts["loss"], 2) if counts["loss"] else None,
            "late": sum(e["late"] for e in entries)}


def _post(title: str, description: str, channel: str) -> bool:
    return main._push_discord({"allowed_mentions": {"parse": []}, "embeds": [{
        "title": title, "description": description, "color": 5793266,
        "footer": {"text": "PAPER · bukan rekomendasi entry · tanpa order otomatis"},
    }]}, channel=channel)


def run_trial(now: datetime | None = None) -> dict:
    if os.getenv("NANDA_TRIAL_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        return {"enabled": False}
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    state = json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else {
        "started_utc": None, "last_candle_utc": None, "entries": [],
    }
    if state.get("reported_utc"):
        return state["summary"]
    key = main.settings.twelvedata_api_key
    if not key:
        print("[nanda-paper] TWELVEDATA_API_KEY tidak tersedia")
        return {"error": "no-price-key"}
    symbol = main.settings.signal_symbol
    bars = main.get_or_set(f"px_{symbol}_15min", 300,
                           lambda: signal_engine.fetch_series(symbol, "15min", 500, key))
    if len(bars) < 85:
        print("[nanda-paper] data M15 belum cukup")
        return {"error": "insufficient-history"}
    entries = state["entries"]
    if not state["started_utc"]:
        state["started_utc"] = now.isoformat(timespec="seconds")
    deadline = datetime.fromisoformat(state["started_utc"]) + timedelta(days=30)
    state.setdefault("pip_price", nanda_method.EXNESS_PIP_PRICE)
    nanda_method.resolve_entries(entries, bars, now)
    found, last = nanda_method.candidates(bars, min(now, deadline), state["last_candle_utc"],
                                          state["pip_price"])
    known = {e["id"] for e in entries}
    for e in sorted(found, key=lambda row: row["candle_utc"]):
        if e["id"] in known or any(
            x["status"] == "open" or x["exit_candle_utc"] >= e["candle_utc"] for x in entries
        ):
            continue
        entries.append(e)
        known.add(e["id"])
        nanda_method.resolve_entries([e], bars, now)
        if now < deadline and not e["late"] and e["status"] == "open" and _post(
            "🧪 NANDA METHOD · SELL XAUUSD M15",
            f"Support `{e['entry']:g}` jebol (close `{e['breakdown_close']:g}`), lalu retest.\n"
            f"**Entry simulasi** `{e['entry']:g}` · **SL** `{e['sl']:g}` · **TP** `{e['tp']:g}`\n"
            f"50 / 100 pip Exness · RR 1:2 · candle retest {e['candle_utc'][:16]} UTC\n"
            "Retest sudah tercatat; harga bisa sudah bergerak.", "sinyal",
        ):
            e["notified_utc"] = now.isoformat(timespec="seconds")
    state["last_candle_utc"] = last
    stats = state["summary"] = summarize(entries)
    win_rate = f"{stats['win_rate_pct']}%" if stats["closed"] else "n/a"
    if now >= deadline and not stats["open"] and _post(
        "📊 NANDA METHOD · Hasil paper 30 hari",
        f"{stats['signals']} kandidat · {stats['closed']} selesai (TP/SL) · "
        f"{stats['win']}W/{stats['loss']}L · WR {win_rate}\n"
        f"Net {stats['net_r']:+g}R · PF {stats['profit_factor']} · "
        f"expectancy {stats['expectancy_r']}R · drawdown {stats['max_drawdown_r']}R\n"
        f"{stats['unknown']} tidak pasti · {stats['expired']} kedaluwarsa. "
        "Simulasi OHLC dengan spread asumsi, tanpa komisi/slippage.", "report",
    ):
        state["reported_utc"] = now.isoformat(timespec="seconds")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    temp = LOG.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(LOG)
    print(f"[nanda-paper] {stats}")
    return stats
