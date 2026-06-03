"""Test logika murni yang tidak butuh network.

Fokus ke: penentuan blackout berita & perhitungan bias COT.
"""
from datetime import datetime, timedelta, timezone

from data_service.fetchers.cot import _net_bias
from data_service.fetchers.forexfactory import _normalize_impact, upcoming_blackout
from data_service.fetchers.sentiment import (
    _dedupe,
    _parse_feed,
    _score_one,
    score_sentiment,
    score_texts,
)
from data_service.fetchers.signal_engine import (
    atr_series,
    build_signal,
    ema_series,
    rsi_series,
)


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


def test_sentiment_negation_flips_sign():
    # "rate hike" bearish; "no rate hike" harusnya tidak bearish (>= 0).
    bearish = _score_one("fed signals rate hike soon")
    negated = _score_one("fed signals no rate hike this year")
    assert bearish < 0
    assert negated >= 0  # negasi membalik -> tidak lagi bearish


def test_sentiment_intensifier_and_dampener():
    base = _score_one("fed sounds dovish")           # 'dovish' = +1.3
    strong = _score_one("fed sounds very dovish")    # 'very' intensifier
    weak = _score_one("fed sounds slightly dovish")  # 'slightly' dampener
    assert strong > base > weak > 0


def test_sentiment_dedupe():
    dupes = ["Gold rises on weak dollar", "Gold rises on weak dollar", "Different headline"]
    assert len(_dedupe(dupes)) == 2


def test_score_texts_defaults_to_lexicon():
    headlines = [
        "Gold rallies as dovish Fed signals rate cut",
        "Weak dollar lifts bullion to record high",
        "Safe haven demand for gold rises on geopolitical tension",
    ]
    res = score_texts(headlines, min_headlines=3, backend="lexicon")
    assert res["backend"] == "lexicon"
    assert res["bias"] == "long"


def test_score_texts_llm_falls_back_without_key(monkeypatch):
    # Tanpa ANTHROPIC_API_KEY, backend 'llm' harus fallback ke lexicon (tetap jalan).
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    headlines = [
        "Gold falls as hawkish Fed eyes rate hike",
        "Strong dollar and rising yields pressure bullion",
        "Gold tumbles after robust jobs report",
    ]
    res = score_texts(headlines, min_headlines=3, backend="llm")
    assert res["backend"] == "lexicon"   # fallback
    assert res["bias"] == "short"


# --- Signal engine ----------------------------------------------------

def test_ema_lags_and_follows():
    e = ema_series([float(i) for i in range(1, 21)], 5)
    assert e[0] == 1.0
    assert e[-1] > e[0]
    assert e[-1] < 20.0  # EMA tertinggal di bawah harga terbaru


def test_rsi_all_gains_is_100():
    closes = [float(i) for i in range(1, 30)]
    assert rsi_series(closes, 14)[-1] == 100.0


def test_rsi_all_losses_is_0():
    closes = [float(i) for i in range(30, 0, -1)]
    assert rsi_series(closes, 14)[-1] == 0.0


def test_atr_constant_range():
    n = 25
    a = atr_series([10.0] * n, [8.0] * n, [9.0] * n, 14)
    assert abs(a[-1] - 2.0) < 0.3   # true range ~2


def test_signal_news_blocked():
    r = build_signal(sentiment_bias="flat", news_blocked=True, api_key="x")
    assert r["signal"] == "none"
    assert "Blackout" in r["reason"]


def test_signal_no_api_key():
    r = build_signal(sentiment_bias="flat", news_blocked=False, api_key="")
    assert r["signal"] == "none"
    assert "API_KEY" in r["reason"] or "key" in r["reason"].lower()
    assert r["suggested_lot"] == 0.01


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
