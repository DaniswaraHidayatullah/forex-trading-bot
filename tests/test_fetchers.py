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


# Rabu 12:00 UTC (pasar buka) -- dipakai supaya test tak tergantung hari nyata.
_WEEKDAY = datetime(2026, 1, 7, 12, 0, tzinfo=timezone.utc)


_TREND_TFS = ("1h", "2h", "4h", "1day")


def _trend_up_fetch(interval, size):
    # Deteksi per-INTERVAL (bukan ukuran): series tren naik vs entry osilasi.
    if interval in _TREND_TFS:
        return [{"open": 1000 + i, "high": 1001 + i, "low": 999 + i, "close": 1000 + i}
                for i in range(size)]
    bars = []
    for i in range(size):
        c = 2000 + (1 if i % 2 == 0 else -1)
        bars.append({"open": c, "high": c + 1, "low": c - 1, "close": c})
    return bars


def test_signal_news_blocked():
    r = build_signal(sentiment_bias="flat", news_blocked=True, api_key="x", now_utc=_WEEKDAY)
    assert r["signal"] == "none"
    assert "Blackout" in r["reason"]


def test_signal_weekend_closed():
    saturday = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)

    def boom(interval, size):
        raise AssertionError("tidak boleh fetch saat pasar tutup")

    r = build_signal(sentiment_bias="long", news_blocked=False, api_key="x",
                     fetch_fn=boom, now_utc=saturday)
    assert r["signal"] == "none"
    assert "TUTUP" in r["reason"]


def test_signal_risk_cap_skips():
    def big_atr_fetch(interval, size):
        if size >= 100:
            return [{"open": 1000 + i, "high": 1001 + i, "low": 999 + i, "close": 1000 + i}
                    for i in range(size)]
        bars = []
        for i in range(size):  # range tiap bar $20 -> ATR ~20 -> SL ~30
            c = 2000 + (1 if i % 2 == 0 else -1)
            bars.append({"open": c, "high": c + 10, "low": c - 10, "close": c})
        return bars

    r = build_signal(sentiment_bias="long", news_blocked=False, api_key="x",
                     profile="intraday", fetch_fn=big_atr_fetch,
                     max_risk_usd=12.0, now_utc=_WEEKDAY)
    assert r["signal"] == "none"
    assert "risiko" in r["reason"].lower()


def test_signal_stale_quote_skips():
    r = build_signal(sentiment_bias="long", news_blocked=False, api_key="x",
                     profile="intraday", fetch_fn=_trend_up_fetch,
                     quote=2100.0, now_utc=_WEEKDAY)  # jauh dari ~2000
    assert r["signal"] == "none"
    assert "bergerak cepat" in r["reason"] or "sinkron" in r["reason"]


def test_combine_bias_strong_news_overrides_cot():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data_service"))
    from main import _combine_bias
    # Konflik + berita kuat -> berita menang
    assert _combine_bias("short", "long", news_score=-0.45) == "short"
    assert _combine_bias("long", "short", news_score=0.35) == "long"
    # Konflik + berita lemah -> flat
    assert _combine_bias("short", "long", news_score=-0.2) == "flat"
    # Searah / COT flat -> ikut berita
    assert _combine_bias("long", "long", news_score=0.1) == "long"
    assert _combine_bias("short", "flat", news_score=-0.1) == "short"
    assert _combine_bias("flat", "long", news_score=0.9) == "flat"


def test_min_headlines_two_gives_bias():
    # 2 headline ter-skor kini cukup (dulu 3 -> sering dipaksa flat di server)
    headlines = [
        "Gold rallies as dovish Fed signals rate cut",
        "Weak dollar lifts bullion to record high",
    ]
    res = score_sentiment(headlines, min_headlines=2)
    assert res["bias"] == "long"


def test_sentiment_gate_creates_shadow():
    # Tren naik (setup BUY) tapi sentimen SHORT -> diblokir, jadi bayangan
    # LENGKAP dgn SL/TP supaya hasil "seandainya" bisa dilacak.
    r = build_signal(
        sentiment_bias="short", news_blocked=False, api_key="x",
        profile="harian", sentiment_score=-0.5, fetch_fn=_trend_up_fetch,
        now_utc=_WEEKDAY,
    )
    assert r["signal"] == "none"
    assert r["shadow_side"] == "buy"
    assert r["sl"] is not None and r["tp"] is not None
    assert "diblokir" in r["reason"]


def test_harian_profile_rr_1_2():
    r = build_signal(
        sentiment_bias="flat", news_blocked=False, api_key="x",
        profile="harian", fetch_fn=_trend_up_fetch, now_utc=_WEEKDAY,
    )
    assert r["signal"] == "buy"
    assert r["profile"] == "Harian"
    assert r["rr"] == 2.0
    assert r["tp_pips"] == r["sl_pips"] * 2   # RR 1:2 dari profil
    assert r["entry_tf"] == "15min"


def test_tracker_outcomes():
    from data_service.fetchers.tracker import check_outcome, summarize

    bars = [
        {"datetime": "2026-01-07 12:05:00", "high": 2005, "low": 1998, "close": 2000},
        {"datetime": "2026-01-07 12:10:00", "high": 2012, "low": 1999, "close": 2010},
    ]
    # BUY tp=2011 tercapai di bar ke-2
    assert check_outcome(bars, "buy", sl=1990, tp=2011) == "win"
    # BUY sl=1999 kena di bar pertama (sebelum TP)
    assert check_outcome(bars, "buy", sl=1999, tp=2011) == "loss"
    # SL & TP di bar yang sama -> konservatif loss
    assert check_outcome(bars, "buy", sl=1999.5, tp=2004) == "loss"
    # filter waktu: bar pertama dilewati
    assert check_outcome(bars, "buy", sl=1998.5, tp=2011,
                         after_utc="2026-01-07T12:06:00+00:00") == "win"
    s = summarize([{"status": "win", "rr": 3}, {"status": "loss"}, {"status": "open"}])
    assert s["winrate_pct"] == 50.0 and s["net_r"] == 2.0 and s["open"] == 1


def test_signal_no_api_key():
    r = build_signal(sentiment_bias="flat", news_blocked=False, api_key="", now_utc=_WEEKDAY)
    assert r["signal"] == "none"
    assert "API_KEY" in r["reason"] or "key" in r["reason"].lower()
    assert r["suggested_lot"] == 0.01


def test_build_signal_buy_full_path_with_confidence():
    def fake_fetch(interval, size):
        if size >= 100:  # series tren -> naik kuat
            return [{"open": 1000 + i, "high": 1001 + i, "low": 999 + i, "close": 1000 + i}
                    for i in range(size)]
        bars = []  # series entry -> osilasi -> RSI ~50
        for i in range(size):
            c = 2000 + (1 if i % 2 == 0 else -1)
            bars.append({"open": c, "high": c + 1, "low": c - 1, "close": c})
        return bars

    r = build_signal(
        sentiment_bias="long", news_blocked=False, api_key="x",
        profile="intraday", sentiment_score=0.5, fetch_fn=fake_fetch,
        now_utc=_WEEKDAY,
    )
    assert r["signal"] == "buy"
    assert r["confidence_level"] == 3        # searah long + sentimen kuat (0.5)
    assert r["tp_pips"] == r["sl_pips"] * 3  # RR 1:3
    assert r["profile"] == "Intraday"


def test_build_signal_confidence_weak_when_flat():
    def fake_fetch(interval, size):
        if size >= 100:
            return [{"open": 1000 + i, "high": 1001 + i, "low": 999 + i, "close": 1000 + i}
                    for i in range(size)]
        bars = []
        for i in range(size):
            c = 2000 + (1 if i % 2 == 0 else -1)
            bars.append({"open": c, "high": c + 1, "low": c - 1, "close": c})
        return bars

    r = build_signal(
        sentiment_bias="flat", news_blocked=False, api_key="x",
        profile="intraday", sentiment_score=0.0, fetch_fn=fake_fetch,
        now_utc=_WEEKDAY,
    )
    assert r["signal"] == "buy"           # flat -> tetap boleh (gerbang lolos)
    assert r["confidence_level"] == 1     # lemah (teknikal saja)


def test_discord_embed_buy():
    from data_service.fetchers.notifier import format_embed
    sig = {
        "signal": "buy", "entry": 2350.4, "sl": 2345.1, "tp": 2366.3,
        "suggested_lot": 0.01, "rr": 3, "rsi": 47.0, "trend": "up",
        "sentiment_bias": "long", "reason": "uptrend", "time_utc": "2026-06-03T00:00:00+00:00",
    }
    sig["profile"] = "Scalping"
    p = format_embed(sig)
    emb = p["embeds"][0]
    assert emb["color"] == 3066993
    assert "BUY" in emb["title"]
    desc = emb["description"]
    for token in ("Entry", "Take Profit", "Stop Loss", "Lot", "sekarang", "berlaku"):
        assert token in desc


def test_discord_embed_none():
    from data_service.fetchers.notifier import format_embed
    p = format_embed({"signal": "none", "reason": "tunggu"})
    emb = p["embeds"][0]
    assert emb["color"] == 9807270
    assert "tunggu" in emb["description"]


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


def test_finbert_bias_aggregation():
    from data_service.fetchers.sentiment import _finbert_bias
    pos = [[{"label": "positive", "score": 0.9}, {"label": "negative", "score": 0.05},
            {"label": "neutral", "score": 0.05}]] * 3
    bias, score = _finbert_bias(pos)
    assert bias == "long" and score > 0
    neg = [[{"label": "positive", "score": 0.05}, {"label": "negative", "score": 0.9},
            {"label": "neutral", "score": 0.05}]] * 3
    bias2, score2 = _finbert_bias(neg)
    assert bias2 == "short" and score2 < 0


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
