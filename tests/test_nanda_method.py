import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from data_service.fetchers import nanda_method as nanda

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data_service"))
sys.path.insert(0, str(ROOT / "tools"))
import run_nanda_trial as runner  # noqa: E402


def sample():
    start = datetime(2026, 11, 20, tzinfo=timezone.utc)

    def bar(i, o, h, low, c):
        return dict(datetime=(start + i * nanda.BAR).isoformat(), open=o, high=h, low=low, close=c)

    return ([bar(i, 4000 - i, 4001 - i, 3999 - i, 4000 - i) for i in range(84)]
            + [bar(i, 3790, 3791, 3789, 3790) for i in range(84, 100)]
            + [bar(100, 3790, 3791, 3769, 3770), bar(101, 3788.7, 3789, 3788.4, 3788.8)])


def test_breakdown_and_first_retest_sell_only_on_closed_bar():
    bars = sample()
    t = datetime.fromisoformat(bars[-1]["datetime"])
    pip = nanda.EXNESS_PIP_PRICE
    assert not nanda.candidates(bars, t + timedelta(minutes=14), None, pip)[0]
    found, last = nanda.candidates(bars, t + nanda.BAR, None, pip)
    assert len(found) == 1
    e = found[0]
    assert (e["side"], e["entry"], e["sl"], e["tp"], e["status"]) == (
        "sell", 3789, 3789.50, 3788.00, "open")
    assert nanda.candidates(bars, t + nanda.BAR, last, pip)[0] == []
    bars[-1].update(open=3790, high=3792)
    assert nanda.candidates(bars, t + nanda.BAR, None, pip)[0] == []


def test_ambiguous_tp_not_claimed_and_same_bar_stop_is_conservative():
    bars = sample()
    now = datetime.fromisoformat(bars[-1]["datetime"]) + nanda.BAR
    bars[-1]["low"] = 3777
    assert nanda.candidates(bars, now, None, nanda.EXNESS_PIP_PRICE)[0][0]["status"] == "unknown"
    bars[-1]["high"] = 3789.25
    assert nanda.candidates(bars, now, None, nanda.EXNESS_PIP_PRICE)[0][0]["status"] == "loss"


def test_runner_keeps_separate_state_and_tags_discord(monkeypatch, tmp_path):
    bars = sample()
    t = datetime.fromisoformat(bars[-1]["datetime"]) + nanda.BAR
    sent = []
    monkeypatch.setattr(runner, "LOG", tmp_path / "nanda.json")
    monkeypatch.setattr(runner.main, "settings", SimpleNamespace(
        twelvedata_api_key="test", signal_symbol="XAU/USD", pip_price=0.1))
    monkeypatch.setattr(runner.main, "get_or_set", lambda key, ttl, fetch: bars)
    monkeypatch.setattr(runner.main, "_push_discord",
                        lambda msg, channel: sent.append((msg, channel)) or True)
    monkeypatch.setenv("NANDA_TRIAL_ENABLED", "true")
    assert runner.run_trial(t)["open"] == 1
    assert "3789.5" in sent[0][0]["embeds"][0]["description"]
    assert runner.run_trial(t)["signals"] == 1
    new_time = t + nanda.BAR
    bars.append(dict(datetime=t.isoformat(), open=3788.8, high=3789.1, low=3787, close=3787.5))
    assert runner.run_trial(new_time)["net_r"] == 2
    assert runner.run_trial(t + timedelta(days=30))["win"] == 1
    assert [channel for _, channel in sent] == ["sinyal", "report"]
    assert all("NANDA METHOD" in msg["embeds"][0]["title"] for msg, _ in sent)
