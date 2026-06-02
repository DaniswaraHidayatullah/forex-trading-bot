"""Smoke test wiring /context tanpa network (fetcher di-mock).

Jalankan: python tests/smoke_context.py  (dari root, dgn cwd=data_service di path)
"""
import sys
from pathlib import Path

# main.py memakai import top-level (from config import ...), jadi jalankan
# seolah cwd = data_service.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data_service"))

import main  # noqa: E402


def run():
    # Mock semua sumber data agar deterministik & tanpa network.
    main.ff.fetch_calendar = lambda *a, **k: []  # tidak ada event -> tidak blackout
    main.sentiment_fetcher.fetch_sentiment = lambda *a, **k: {
        "bias": "short", "score": -0.4, "headlines_total": 10,
        "headlines_scored": 5, "samples": ["Gold falls as hawkish Fed eyes rate hike"],
    }
    main.cot_fetcher.fetch_cot = lambda *a, **k: {
        "market": "GOLD", "bias": "long", "noncomm_long": 200000,
        "noncomm_short": 50000, "report_date": "2026-05-27",
    }
    # Hindari cache file lama mempengaruhi hasil.
    main.get_or_set = lambda key, ttl, producer, **kw: producer()
    main.settings.api_token = ""

    ctx = main.context("XAUUSD")
    print("trade_allowed :", ctx["trade_allowed"])
    print("sentiment_bias:", ctx["sentiment_bias"])
    print("news.blocked  :", ctx["news"]["blocked"])
    print("sentiment.bias:", ctx["sentiment"]["bias"])
    print("cot.bias      :", ctx["cot"]["bias"])

    # Berita short vs COT long -> konflik -> gabungan harus 'flat'.
    assert ctx["trade_allowed"] is True
    assert ctx["sentiment_bias"] == "flat", ctx["sentiment_bias"]

    # Kasus 2: berita short, COT flat -> gabungan ikut berita = short.
    main.cot_fetcher.fetch_cot = lambda *a, **k: {"market": "GOLD", "bias": "flat"}
    ctx2 = main.context("XAUUSD")
    assert ctx2["sentiment_bias"] == "short", ctx2["sentiment_bias"]

    # Kasus 3: berita flat -> gabungan flat (tak membatasi arah).
    main.sentiment_fetcher.fetch_sentiment = lambda *a, **k: {"bias": "flat", "score": 0.0}
    ctx3 = main.context("XAUUSD")
    assert ctx3["sentiment_bias"] == "flat", ctx3["sentiment_bias"]

    print("\nSMOKE OK: penggabungan bias berita+COT benar.")


if __name__ == "__main__":
    run()
