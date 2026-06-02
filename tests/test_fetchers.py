"""Test logika murni yang tidak butuh network.

Fokus ke: penentuan blackout berita & perhitungan bias COT.
"""
from datetime import datetime, timedelta, timezone

from data_service.fetchers.cot import _net_bias
from data_service.fetchers.forexfactory import _normalize_impact, upcoming_blackout
from data_service.fetchers.sentiment import _parse_feed, score_sentiment


def test_normalize_impact():
    assert _normalize_impact("High Impact Expected") == "high"
    assert _normalize_impact("medium") == "medium"
    assert _normalize_impact("Low") == "low"
    assert _normalize_impact("") == "holiday"


def test_net_bias():
    assert _net_bias(1000, 100) == "long"
    assert _net_bias(100, 1000) == "short"
    assert _net_bias(500, 500) == "flat"
    assert _net_bias(0, 0) == "flat"


def _ev(currency, impact, dt):
    return {"currency": currency, "impact": impact, "title": "x", "time_utc": dt.isoformat()}


def test_blackout_blocks_near_high_impact_usd():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    events = [_ev("USD", "high", now + timedelta(minutes=10))]
    res = upcoming_blackout(events, ["USD"], "high", 30, now=now)
    assert res["blocked"] is True
    assert res["event"]["currency"] == "USD"


def test_blackout_ignores_far_event():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    events = [_ev("USD", "high", now + timedelta(minutes=90))]
    res = upcoming_blackout(events, ["USD"], "high", 30, now=now)
    assert res["blocked"] is False


def test_blackout_ignores_low_impact_and_other_currency():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    events = [
        _ev("USD", "low", now + timedelta(minutes=5)),
        _ev("EUR", "high", now + timedelta(minutes=5)),
    ]
    res = upcoming_blackout(events, ["USD"], "high", 30, now=now)
    assert res["blocked"] is False


# --- Sentimen berita ---------------------------------------------------

def test_sentiment_bullish_gold():
    headlines = [
        "Gold rallies as dovish Fed signals rate cut",
        "Weak dollar lifts bullion to record high",
        "Safe haven demand for gold rises on geopolitical tension",
    ]
    res = score_sentiment(headlines, min_headlines=3)
    assert res["bias"] == "long"
    assert res["score"] > 0
    assert res["headlines_scored"] == 3


def test_sentiment_bearish_gold():
    headlines = [
        "Gold falls as hawkish Fed eyes rate hike",
        "Strong dollar and rising yields pressure bullion",
        "Gold tumbles after robust jobs report",
    ]
    res = score_sentiment(headlines, min_headlines=3)
    assert res["bias"] == "short"
    assert res["score"] < 0


def test_sentiment_irrelevant_headlines_flat():
    headlines = [
        "Apple unveils new iPhone lineup",
        "Local football team wins championship",
    ]
    res = score_sentiment(headlines, min_headlines=3)
    assert res["bias"] == "flat"
    assert res["headlines_scored"] == 0


def test_sentiment_below_min_headlines_flat():
    headlines = ["Gold jumps on weak dollar"]  # cuma 1 sinyal, di bawah minimum
    res = score_sentiment(headlines, min_headlines=3)
    assert res["bias"] == "flat"


def test_storage_max_stale_blocks_old_cache():
    import json
    import time

    import pytest

    from data_service import storage

    key = "test_stale_unit"
    path = storage._path(key)

    # Isi cache lalu paksa timestamp jadi tua (2 jam lalu).
    storage.get_or_set(key, ttl_seconds=0, producer=lambda: {"v": 1})
    data = json.loads(path.read_text())
    data["ts"] = time.time() - 7200
    path.write_text(json.dumps(data))

    def boom():
        raise RuntimeError("feed down")

    # Cache 2 jam, batas stale 1 jam -> harus menolak (raise).
    with pytest.raises(RuntimeError):
        storage.get_or_set(key, ttl_seconds=0, producer=boom, max_stale_seconds=3600)

    # Batas stale 3 jam -> masih boleh pakai cache basi.
    out = storage.get_or_set(key, ttl_seconds=0, producer=boom, max_stale_seconds=10800)
    assert out == {"v": 1}

    path.unlink(missing_ok=True)


def test_parse_feed_basic_rss():
    xml = """<?xml version='1.0'?>
    <rss version='2.0'><channel>
      <item><title>Gold rises on rate cut bets</title>
            <description>Bullion gains as &lt;b&gt;dollar&lt;/b&gt; slips</description></item>
      <item><title>Stocks rally</title></item>
    </channel></rss>"""
    items = _parse_feed(xml)
    assert len(items) == 2
    assert "Gold rises" in items[0]
    assert "<b>" not in items[0]  # tag HTML dibersihkan
