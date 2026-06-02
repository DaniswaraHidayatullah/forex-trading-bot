"""Konfigurasi terpusat. Dibaca dari environment variables / .env."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    cache_ttl_seconds: int = 1800
    news_blackout_minutes: int = 30
    news_min_impact: str = "high"  # low | medium | high
    # Kalender berita sensitif waktu: kalau cache lebih tua dari ini & feed
    # gagal, jangan dipakai (biar EA fail-safe). Default 6 jam.
    news_max_stale_seconds: int = 21600
    api_token: str = ""

    # --- Sentimen berita (scraping RSS + skoring leksikon) ----------------
    sentiment_enabled: bool = True
    # Backend skoring: "lexicon" (gratis, default) | "llm" (butuh ANTHROPIC_API_KEY
    # + paket anthropic; otomatis fallback ke lexicon bila tak tersedia).
    sentiment_backend: str = "lexicon"
    sentiment_threshold: float = 0.15   # |skor| di atas ini baru jadi bias arah
    sentiment_min_headlines: int = 3    # minimal headline relevan agar tak "flat"
    # Cache khusus berita lebih pendek (berita cepat basi).
    sentiment_cache_ttl_seconds: int = 900
    # Daftar feed RSS; override lewat env SENTIMENT_FEEDS (pisah koma) bila perlu.
    sentiment_feeds: list[str] = [
        "https://www.forexlive.com/feed/news",
        "https://www.fxstreet.com/rss/news",
        "https://www.investing.com/rss/commodities_Gold.rss",
    ]

    # Mata uang yang relevan per simbol -> dipakai untuk memfilter berita & COT
    symbol_currencies: dict[str, list[str]] = {
        "XAUUSD": ["USD"],          # emas digerakkan terutama oleh USD
        "AUDUSD": ["AUD", "USD"],
    }

    # Nama pasar COT (CFTC) per currency. Dispesifikkan agar tidak salah
    # kontrak (mis. "GOLD" bisa cocok ke beberapa pasar).
    cot_market_names: dict[str, str] = {
        "USD": "U.S. DOLLAR INDEX",
        "AUD": "AUSTRALIAN DOLLAR",
        "XAU": "GOLD - COMMODITY EXCHANGE",
    }


settings = Settings()
