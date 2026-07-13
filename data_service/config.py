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

    # --- Signal engine (sinyal untuk eksekusi manual) ---------------------
    signal_reward_ratio: float = 3.0     # RR 1:3
    signal_atr_mult: float = 1.5         # SL = ATR * ini
    signal_use_sentiment: bool = True    # gate arah pakai sentimen
    signal_cache_ttl_seconds: int = 300  # cache sinyal (per ~bar M30)
    signal_symbol: str = "XAU/USD"       # simbol di Twelve Data
    # API key Twelve Data (GRATIS di twelvedata.com). Set via env TWELVEDATA_API_KEY.
    twelvedata_api_key: str = ""

    # --- Notifikasi Discord (auto-push sinyal) ----------------------------
    # Pilih SALAH SATU: webhook (paling gampang) ATAU bot (token + channel id).
    # Kalau bot token & channel id diisi, itu yang dipakai; jika tidak, webhook.
    discord_webhook_url: str = ""        # set via env DISCORD_WEBHOOK_URL
    discord_bot_token: str = ""          # set via env DISCORD_BOT_TOKEN
    discord_channel_id: str = ""         # set via env DISCORD_CHANNEL_ID
    signal_auto_push: bool = True        # auto kirim ke Discord saat ada sinyal
    signal_poll_seconds: int = 1800      # cek sinyal tiap N detik (default 30 mnt)
    # Profil yang di-auto-push (pisah koma): harian, scalp, intraday, swing
    signal_profiles: str = "harian"
    # Minimal keyakinan untuk auto-push: none | medium | strong.
    # "none" = kirim juga sinyal teknikal-only (frekuensi harian, tidak ketat);
    # kartu tetap menampilkan status sentimen + bintang keyakinan.
    signal_min_confidence: str = "none"
    # Batas risiko $ per trade @0.01 lot (jarak SL). Sinyal dgn risiko lebih
    # besar di-skip (akun kecil tak bisa memperkecil lot di bawah 0.01).
    signal_max_risk_usd: float = 12.0

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
