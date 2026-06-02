"""Entry point data_service.

Endpoint utama:
  GET /health                         -> liveness check
  GET /news?symbol=XAUUSD             -> status blackout berita
  GET /cot?symbol=AUDUSD              -> bias posisi COT
  GET /sentiment?symbol=XAUUSD        -> bias sentimen berita (scraping RSS)
  GET /context?symbol=XAUUSD          -> gabungan: izin trade + bias arah

Endpoint /context inilah yang dipanggil EA sebelum entry.
"""
from __future__ import annotations

from config import settings
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fetchers import cot as cot_fetcher
from fetchers import forexfactory as ff
from fetchers import sentiment as sentiment_fetcher
from storage import get_or_set

app = FastAPI(title="Forex Bot Data Service", version="1.0.0")

def _combine_bias(news_bias: str, cot_bias: str) -> str:
    """Tentukan bias arah akhir. Sentimen BERITA jadi penggerak utama; COT
    hanya konfirmasi. Aturannya:
      - berita 'flat'              -> 'flat'  (tak ada sinyal berita -> tak membatasi arah)
      - COT searah / COT 'flat'    -> ikut arah berita
      - berita vs COT bertentangan -> 'flat'  (sinyal konflik -> jangan dipaksa)

    Catatan: untuk emas, posisi COT non-commercial hampir selalu net-long,
    jadi COT tidak boleh memaksa arah sendirian (kalau tidak, SELL terblokir
    permanen). Karena itu berita yang memimpin.
    """
    if news_bias == "flat":
        return "flat"
    if cot_bias == "flat" or cot_bias == news_bias:
        return news_bias
    return "flat"


def _auth(x_api_key: str | None = Header(default=None)) -> None:
    if settings.api_token and x_api_key != settings.api_token:
        raise HTTPException(status_code=401, detail="invalid api key")


def _currencies_for(symbol: str) -> list[str]:
    return settings.symbol_currencies.get(symbol.upper(), [])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/news", dependencies=[Depends(_auth)])
def news(symbol: str = Query(..., min_length=6)) -> dict:
    currencies = _currencies_for(symbol)
    events = get_or_set(
        "ff_calendar",
        settings.cache_ttl_seconds,
        ff.fetch_calendar,
        max_stale_seconds=settings.news_max_stale_seconds,
    )
    result = ff.upcoming_blackout(
        events,
        currencies=currencies,
        min_impact=settings.news_min_impact,
        blackout_minutes=settings.news_blackout_minutes,
    )
    return {"symbol": symbol.upper(), **result}


@app.get("/cot", dependencies=[Depends(_auth)])
def cot(symbol: str = Query(..., min_length=6)) -> dict:
    # Untuk XAUUSD pakai bias GOLD; selain itu pakai mata uang basis.
    symbol = symbol.upper()
    if symbol == "XAUUSD":
        market = settings.cot_market_names["XAU"]
    else:
        base = symbol[:3]  # mis. AUD dari AUDUSD
        market = settings.cot_market_names.get(base)
        if market is None:
            return {"symbol": symbol, "bias": "flat", "report_date": None}

    data = get_or_set(
        f"cot_{market}", settings.cache_ttl_seconds, lambda: cot_fetcher.fetch_cot(market)
    )
    return {"symbol": symbol, **data}


@app.get("/sentiment", dependencies=[Depends(_auth)])
def sentiment(symbol: str = Query(..., min_length=6)) -> dict:
    """Bias sentimen dari scraping headline berita finansial (fokus emas/USD)."""
    symbol = symbol.upper()
    if not settings.sentiment_enabled:
        return {"symbol": symbol, "bias": "flat", "score": 0.0, "headlines_scored": 0}

    data = get_or_set(
        f"news_sentiment_{settings.sentiment_backend}",
        settings.sentiment_cache_ttl_seconds,
        lambda: sentiment_fetcher.fetch_sentiment(
            feeds=settings.sentiment_feeds,
            threshold=settings.sentiment_threshold,
            min_headlines=settings.sentiment_min_headlines,
            backend=settings.sentiment_backend,
        ),
    )
    return {"symbol": symbol, **data}


@app.get("/context", dependencies=[Depends(_auth)])
def context(symbol: str = Query(..., min_length=6)) -> dict:
    """Gabungan yang dibaca EA: boleh entry atau tidak + bias arah.

    EA memutuskan:
      - kalau news.blocked True       -> jangan entry (blackout berita)
      - kalau sentiment_bias 'long'   -> hanya izinkan BUY (searah sentimen), dst.

    sentiment_bias = gabungan sentimen BERITA (timely) + posisi COT (mingguan).
    """
    n = news(symbol)        # type: ignore[arg-type]
    c = cot(symbol)         # type: ignore[arg-type]
    s = sentiment(symbol)   # type: ignore[arg-type]

    news_bias = s.get("bias", "flat")
    cot_bias = c.get("bias", "flat")
    combined = _combine_bias(news_bias, cot_bias)

    return {
        "symbol": symbol.upper(),
        "trade_allowed": not n["blocked"],
        "sentiment_bias": combined,
        "news": n,
        "sentiment": s,
        "cot": c,
    }
