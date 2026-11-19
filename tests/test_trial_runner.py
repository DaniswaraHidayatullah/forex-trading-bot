"""Paper runner state and dedupe; all external calls are mocked."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data_service"))
sys.path.insert(0, str(ROOT / "tools"))

import run_trial_once as runner  # noqa: E402


def test_trial_isolated_deduped_and_ends(monkeypatch, tmp_path):
    t = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)
    log = tmp_path / "trial.json"
    candidate = {
        "id": "paper-trend-202611190945", "setup": "trend", "side": "buy",
        "candle_utc": (t - timedelta(minutes=15)).isoformat(),
        "entry_candle_utc": t.isoformat(),
        "entry": 4000, "sl": 3990, "tp": 4020, "rr": 2,
        "rsi": 50, "atr": 3, "risk_usd_at_02_cent_lot_assumed": 2,
        "spread_assumed": 0.3, "status": "open", "late": False,
    }
    sent = []
    monkeypatch.setattr(runner, "TRIAL_FILE", log)
    monkeypatch.setattr(runner.main, "settings", SimpleNamespace(
        twelvedata_api_key="test-only", signal_symbol="XAU/USD"))
    monkeypatch.setattr(runner.main, "get_or_set", lambda key, ttl, fetch: [{"datetime": t.isoformat()}])
    monkeypatch.setattr(runner.trial_signals, "candidates", lambda bars, now, after: ([candidate.copy()], candidate["candle_utc"]))
    monkeypatch.setattr(runner.trial_signals, "resolve", lambda entries, bars, now: None)
    monkeypatch.setattr(runner, "_send_paper", lambda e: sent.append(e["id"]) or True)

    first = runner.run_trial(t)
    second = runner.run_trial(t + timedelta(minutes=15))
    last = runner.run_trial(t + timedelta(days=31))

    assert (first["added"], second["added"], last["added"]) == (1, 0, 0)
    assert sent == [candidate["id"]]
    state = json.loads(log.read_text(encoding="utf-8"))
    assert len(state["entries"]) == 1
    assert state["finished_utc"]
