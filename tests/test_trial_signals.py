"""Offline checks for the paper-only trial. No broker, API or Discord calls."""
from datetime import datetime, timedelta, timezone

from data_service.fetchers.trial_signals import candidates, closed_index, resolve


def _bar(t: datetime, o: float, h: float, low: float, c: float) -> dict:
    return {"datetime": t.isoformat(), "open": o, "high": h, "low": low, "close": c}


def test_closed_index_ignores_running_bar():
    t = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)
    bars = [_bar(t + timedelta(minutes=15 * i), 100, 101, 99, 100) for i in range(4)]
    assert closed_index(bars, t + timedelta(minutes=31)) == 1
    assert closed_index(bars, t + timedelta(minutes=45)) == 2


def test_resolve_same_bar_sl_and_tp_is_loss():
    t = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)
    e = {"side": "buy", "entry_candle_utc": t.isoformat(), "sl": 99,
         "tp": 102, "spread_assumed": 0.3, "status": "open"}
    resolve([e], [_bar(t, 100, 103, 98, 101)], t + timedelta(minutes=16))
    assert e["status"] == "loss"
    assert e["result_r"] == -1


def test_resolve_sell_uses_ask_for_stop():
    t = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)
    e = {"side": "sell", "entry_candle_utc": t.isoformat(), "sl": 101,
         "tp": 98, "spread_assumed": 0.3, "status": "open"}
    resolve([e], [_bar(t, 100, 100.8, 99, 100)], t + timedelta(minutes=16))
    assert e["status"] == "loss"  # 100.8 bid + 0.3 spread > 101 ask


def test_missing_candle_does_not_become_a_false_win():
    t = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)
    e = {"side": "buy", "entry_candle_utc": t.isoformat(), "sl": 99,
         "tp": 102, "spread_assumed": 0.3, "status": "open"}
    resolve([e], [_bar(t + timedelta(minutes=15), 100, 103, 100, 102)],
            t + timedelta(minutes=31))
    assert e["status"] == "unknown"
    assert "result_r" not in e


def test_position_expires_before_later_prices():
    t = datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)
    e = {"side": "buy", "entry_candle_utc": t.isoformat(), "sl": 90,
         "tp": 110, "spread_assumed": 0.3, "status": "open"}
    bars = [_bar(t + timedelta(minutes=15 * i), 100, 101, 99, 100)
            for i in range(193)]
    bars.append(_bar(t + timedelta(days=2, minutes=15), 100, 120, 99, 115))
    resolve([e], bars, t + timedelta(days=2, hours=1))
    assert e["status"] == "expired"


def test_trial_initial_run_never_backfills_old_signals():
    t = datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)
    bars = []
    for i in range(500):
        price = 100 + 0.15 * i + (1.5 if i % 6 < 3 else -1.5)
        bars.append(_bar(t + timedelta(minutes=15 * i), price, price + 1,
                         price - 1, price + (0.3 if i % 2 else -0.3)))
    now = t + timedelta(minutes=15 * 500 + 3)
    found, last = candidates(bars, now, None)
    assert last == bars[-2]["datetime"]
    assert all(x["candle_utc"] == bars[-2]["datetime"] for x in found)
    again, _ = candidates(bars, now, last)
    assert again == []
