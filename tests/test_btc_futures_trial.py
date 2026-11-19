"""BTC futures study tests: no API key, broker, Discord or live orders."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_service.fetchers import btc_futures_trial as study

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data_service"))
sys.path.insert(0, str(ROOT / "tools"))
import run_btc_futures_trial as runner  # noqa: E402


def _bars():
    t = datetime(2026, 11, 1, tzinfo=timezone.utc)
    out = []
    for i in range(120):
        p = 100_000 + 10 * i + (20 if i % 3 else -20)
        out.append({"datetime": (t + timedelta(minutes=15 * i)).isoformat(),
                    "open": p, "high": p + 40, "low": p - 40,
                    "close": p + (5 if i % 2 else -5)})
    return out


def test_first_run_only_latest_closed_candle_and_dedup():
    bars = _bars()
    now = datetime.fromisoformat(bars[-1]["datetime"]) + timedelta(minutes=2)
    found, last = study.candidates(bars, now, None)
    assert last == bars[-2]["datetime"]
    assert all(e["candle_utc"] == last for e in found)
    assert study.candidates(bars, now, last)[0] == []


def test_future_candle_never_used_as_signal():
    bars = _bars()
    now = datetime.fromisoformat(bars[-1]["datetime"]) + timedelta(minutes=2)
    found, _ = study.candidates(bars, now, bars[-3]["datetime"])
    assert all(e["candle_utc"] != bars[-1]["datetime"] for e in found)


def test_exchange_parser_validates_continuity(monkeypatch):
    t = datetime(2026, 11, 1, tzinfo=timezone.utc)
    base = int(t.timestamp() * 1000)

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return [[base, "1", "2", "0.5", "1.5", "0"],
                    [base + 30 * 60_000, "1", "2", "0.5", "1.5", "0"]]

    monkeypatch.setattr(study.httpx, "get", lambda *a, **kw: Response())
    with pytest.raises(ValueError, match="kontinu"):
        study.fetch_klines()


def test_paper_runner_isolated_and_log_only_by_default(monkeypatch, tmp_path):
    t = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(runner, "TRIAL_FILE", tmp_path / "btc.json")
    monkeypatch.setattr(runner.main, "settings", SimpleNamespace(discord_bot_token="test"))
    monkeypatch.delenv("DISCORD_BTC_TRIAL_CHANNEL_ID", raising=False)
    monkeypatch.setattr(runner.btc_futures_trial, "fetch_klines", _bars)
    candidate = {
        "id": "btc-futures-paper-1", "setup": "trend", "side": "buy",
        "candle_utc": (t - timedelta(minutes=15)).isoformat(),
        "entry_candle_utc": t.isoformat(), "status": "open", "late": False,
    }
    monkeypatch.setattr(runner.btc_futures_trial, "candidates",
                        lambda bars, now, after: ([candidate.copy()], candidate["candle_utc"]))
    monkeypatch.setattr(runner.btc_futures_trial, "resolve", lambda entries, bars, now: None)
    monkeypatch.setattr(runner.notifier, "send_bot",
                        lambda *args, **kwargs: pytest.fail("Discord must stay silent by default"))
    assert runner.run_trial(t)["added"] == 1
    assert runner.run_trial(t + timedelta(minutes=15))["added"] == 0
    data = json.loads(runner.TRIAL_FILE.read_text(encoding="utf-8"))
    assert len(data["entries"]) == 1
    assert "notified_utc" not in data["entries"][0]
