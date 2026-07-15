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

import threading
import time

from config import settings
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fetchers import cot as cot_fetcher
from fetchers import forexfactory as ff
from fetchers import notifier, signal_engine
from fetchers import sentiment as sentiment_fetcher
from storage import get_or_set

app = FastAPI(title="Forex Bot Data Service", version="1.0.0")

_NEWS_OVERRIDE = 0.30  # |skor berita| >= ini -> berita menang atas COT


def _combine_bias(news_bias: str, cot_bias: str, news_score: float = 0.0) -> str:
    """Tentukan bias arah akhir. Sentimen BERITA jadi penggerak utama; COT
    hanya konfirmasi. Aturannya:
      - berita 'flat'                    -> 'flat'
      - COT searah / COT 'flat'          -> ikut arah berita
      - konflik, berita KUAT (|skor|>=0.3) -> berita menang (COT itu data
        mingguan yang lambat; berita hari ini lebih relevan)
      - konflik, berita lemah            -> 'flat'

    Catatan: untuk emas, posisi COT non-commercial hampir selalu net-long,
    jadi COT tidak boleh memaksa arah sendirian (kalau tidak, SELL terblokir
    permanen). Karena itu berita yang memimpin.
    """
    if news_bias == "flat":
        return "flat"
    if cot_bias == "flat" or cot_bias == news_bias:
        return news_bias
    if abs(news_score) >= _NEWS_OVERRIDE:
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
    try:
        events = get_or_set(
            "ff_calendar",
            settings.cache_ttl_seconds,
            ff.fetch_calendar,
            max_stale_seconds=settings.news_max_stale_seconds,
        )
    except Exception as e:  # noqa: BLE001
        # Kalender tak terjangkau: JANGAN matikan seluruh sinyal. Degradasi:
        # anggap tidak blackout, tapi tandai supaya kelihatan di log/diagnosa.
        print("PERINGATAN kalender berita gagal:", e)
        return {"symbol": symbol.upper(), "blocked": False, "event": None,
                "available": False}
    result = ff.upcoming_blackout(
        events,
        currencies=currencies,
        min_impact=settings.news_min_impact,
        blackout_minutes=settings.news_blackout_minutes,
    )
    return {"symbol": symbol.upper(), "available": True, **result}


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


# Cache TTL harga per interval (detik) -> hemat kuota Twelve Data.
_PRICE_TTL = {"5min": 120, "15min": 300, "30min": 600, "1h": 900,
              "2h": 1800, "4h": 3600, "1day": 7200}


def _signal_for(symbol: str, equity: float, profile: str) -> dict:
    """Hitung sinyal untuk satu profil (dipakai endpoint & poller Discord)."""
    if profile not in signal_engine.PROFILES:
        profile = "intraday"
    ctx = context(symbol)  # type: ignore[arg-type]
    bias = ctx.get("sentiment_bias", "flat")
    blocked = not ctx.get("trade_allowed", True)
    sent = ctx.get("sentiment") or {}
    sent_score = float(sent.get("score", 0.0) or 0.0)
    sent_available = int(sent.get("headlines_total", 0) or 0) > 0

    # Harga real-time (cache 60 dtk). Gagal -> None (fallback ke bar terakhir).
    try:
        quote = get_or_set(
            f"quote_{settings.signal_symbol}", 60,
            lambda: signal_engine.fetch_price(
                settings.signal_symbol, settings.twelvedata_api_key
            ),
        )
    except Exception:  # noqa: BLE001
        quote = None

    def _cached_fetch(interval: str, size: int) -> list[dict]:
        return get_or_set(
            f"px_{settings.signal_symbol}_{interval}",
            _PRICE_TTL.get(interval, 600),
            lambda: signal_engine.fetch_series(
                settings.signal_symbol, interval, size, settings.twelvedata_api_key
            ),
        )

    def _produce() -> dict:
        return signal_engine.build_signal(
            sentiment_bias=bias,
            news_blocked=blocked,
            api_key=settings.twelvedata_api_key,
            symbol=settings.signal_symbol,
            equity=equity,
            profile=profile,
            rr=settings.signal_reward_ratio,
            use_sentiment=settings.signal_use_sentiment,
            sentiment_score=sent_score,
            sentiment_available=sent_available,
            quote=quote,
            max_risk_usd=settings.signal_max_risk_usd,
            fetch_fn=_cached_fetch,
        )

    return get_or_set(
        f"signal_{symbol.upper()}_{profile}_{int(equity)}",
        settings.signal_cache_ttl_seconds,
        _produce,
    )


@app.get("/signal", dependencies=[Depends(_auth)])
def signal(
    symbol: str = Query("XAUUSD", min_length=6),
    equity: float = Query(100.0, gt=0),
    profile: str = Query("intraday"),
) -> dict:
    """Sinyal XAUUSD untuk EKSEKUSI MANUAL (cloud, 24/7).

    profile: scalp | intraday | swing (RR tetap 1:3, beda timeframe & jarak SL).
    """
    return _signal_for(symbol, equity, profile)


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
    combined = _combine_bias(news_bias, cot_bias, float(s.get("score", 0.0) or 0.0))

    return {
        "symbol": symbol.upper(),
        "trade_allowed": not n["blocked"],
        "sentiment_bias": combined,
        "news": n,
        "sentiment": s,
        "cot": c,
    }


# --- Auto-push sinyal ke Discord (background, jalan di Railway 24/7) -----

def _discord_configured() -> bool:
    return bool(
        (settings.discord_bot_token and settings.discord_channel_id)
        or settings.discord_webhook_url
    )


def _push_discord(sig: dict, channel: str = "sinyal") -> bool:
    """Kirim ke Discord. `channel` = kunci routing (sinyal/report/analysis/
    alert/price/news/calendar/dollar/prediction); fallback DISCORD_CHANNEL_ID.
    """
    if settings.discord_bot_token:
        chan_id = settings.discord_channels.get(channel) or settings.discord_channel_id
        if chan_id:
            return notifier.send_bot(settings.discord_bot_token, chan_id, sig)
    if settings.discord_webhook_url:
        return notifier.send_webhook(settings.discord_webhook_url, sig)
    return False


def _signal_poller() -> None:
    """Loop: cek sinyal tiap N detik untuk tiap profil, kirim ke Discord saat
    ada sinyal BARU.

    Dedupe per-profil: kirim hanya saat arah berubah (none->buy/sell atau
    buy<->sell), supaya tidak spam tiap siklus untuk kondisi yang sama.
    """
    last_side: dict[str, str | None] = {}
    interval = max(60, settings.signal_poll_seconds)
    profiles = [p.strip() for p in settings.signal_profiles.split(",") if p.strip()]
    min_level = {"none": 0, "medium": 2, "strong": 3}.get(settings.signal_min_confidence, 0)
    while True:
        for profile in profiles:
            try:
                sig = _signal_for("XAUUSD", 100.0, profile)
                side = sig.get("signal", "none")
                strong_enough = sig.get("confidence_level", 0) >= min_level
                if side in ("buy", "sell") and strong_enough:
                    if last_side.get(profile) != side:
                        _push_discord(sig)
                        last_side[profile] = side
                elif side == "none":
                    last_side[profile] = None
            except Exception as e:  # noqa: BLE001 - jangan matikan loop
                print("signal poller error:", profile, e)
        time.sleep(interval)


@app.on_event("startup")
def _start_poller() -> None:
    if _discord_configured() and settings.signal_auto_push:
        threading.Thread(target=_signal_poller, daemon=True).start()
        print("Discord signal poller aktif.")
