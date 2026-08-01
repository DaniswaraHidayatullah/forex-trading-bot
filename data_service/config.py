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
    sentiment_min_headlines: int = 2    # minimal headline ter-skor agar tak "flat"
    # Cache khusus berita lebih pendek (berita cepat basi).
    sentiment_cache_ttl_seconds: int = 900
    # Daftar feed RSS; override lewat env SENTIMENT_FEEDS (pisah koma) bila perlu.
    sentiment_feeds: list[str] = [
        "https://www.forexlive.com/feed/news",
        "https://www.fxstreet.com/rss/news",
        "https://www.investing.com/rss/commodities_Gold.rss",
        "https://www.cnbc.com/id/20910258/device/rss/rss.html",
        "https://feeds.marketwatch.com/marketwatch/marketpulse/",
        "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    ]

    # Feed berita keuangan UMUM utk channel 🌎market-news (lebih luas: saham,
    # ekonomi, dunia, crypto). Semua diuji hidup. Bot sinyal TIDAK memakai ini.
    news_feeds_general: list[str] = [
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",   # top news
        "https://www.cnbc.com/id/100727362/device/rss/rss.html",   # world
        "https://www.cnbc.com/id/10000664/device/rss/rss.html",    # finance
        "https://feeds.marketwatch.com/marketwatch/topstories/",
        "https://feeds.a.dj.com/rss/RSSWorldNews.xml",
        "https://finance.yahoo.com/news/rssindex",
        "https://seekingalpha.com/market_currents.xml",
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
    ]

    # --- Signal engine (sinyal untuk eksekusi manual) ---------------------
    signal_reward_ratio: float = 2.0     # RR 1:2 (semua profil, permintaan user)
    signal_atr_mult: float = 1.5         # SL = ATR * ini
    signal_use_sentiment: bool = True    # gate arah pakai sentimen
    signal_cache_ttl_seconds: int = 300  # cache sinyal (per ~bar M30)
    signal_symbol: str = "XAU/USD"       # simbol di Twelve Data
    # API key Twelve Data (GRATIS di twelvedata.com). Set via env TWELVEDATA_API_KEY.
    twelvedata_api_key: str = ""

    # Versi strategi ("v1" arsip / "v2" aktif) -> dipakai memisah statistik.
    signal_version: str = "v2"

    # --- Spesifikasi BROKER XAUUSD — akun EXNESS CENT, lot 0.2 --------------
    # CENT: 1 lot = 1 oz (100x lebih kecil dari Standard 100 oz). Di 0.2 lot =
    # 0.2 oz -> gerak 1 pip ($0.10) = $0.02 P/L. Jadi SL 100 pips = $2 (2% dari
    # $100). Semua $ di kartu = nilai NYATA pada lot 0.2 cent.
    # (VERIFIKASI di terminal: buka 0.2 lot, cek $ risiko; kalau beda kabari.)
    broker_name: str = "Exness"
    broker_account: str = "cent"         # standard | cent
    signal_lot: float = 0.2              # lot tetap dipakai (akun cent)
    pip_price: float = 0.10              # gerak harga utk 1 pip XAUUSD
    usd_per_pip: float = 0.02            # $ P/L per pip @ 0.2 lot cent (0.2 oz x $0.10)
    min_lot: float = 0.01
    volume_step: float = 0.01
    digits: int = 2

    # --- BITCOIN (BTCUSD) — DOMAIN TERPISAH, kode/VPS sama ------------------
    # BTC beda total dari emas: 24/7, volatilitas ribuan $, tanpa sentimen/COT
    # (leksikon emas tak berlaku) -> BTC = TEKNIKAL-ONLY dulu (maks ⭐⭐ via
    # momentum) sampai leksikon crypto dibangun. Profil & kalkulasi sendiri.
    btc_enabled: bool = True
    btc_symbol: str = "BTC/USD"          # simbol Twelve Data
    btc_profiles: str = "btc"            # profil BTC (lihat BTC_PROFILES)
    # Feed berita CRYPTO (dinilai leksikon crypto sendiri, bukan emas).
    # (cryptoslate dibuang: 403 dari IP datacenter). 4 feed ini teruji hidup.
    btc_sentiment_feeds: list[str] = [
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss",
        "https://decrypt.co/feed",
        "https://bitcoinmagazine.com/feed",
    ]
    # Exness BTCUSD CENT: 1 lot = 0.01 BTC (100x lebih kecil dari Standard 1 BTC).
    # Di 0.2 lot = 0.002 BTC -> gerak $1 = $0.002 P/L. SL ~$450 = ~$0.90 risiko.
    # (VERIFIKASI di terminal; kalau beda kabari.)
    btc_pip_price: float = 1.0           # 1 "pip" BTC = gerak $1 harga
    btc_usd_per_pip: float = 0.002       # $ P/L per $1 gerak @ 0.2 lot cent
    # Batas risiko $ NYATA @0.2 lot cent. BTC ber-SL lebar (ATR ribuan $);
    # $3 = tolak SL > ~$1500 (~2400 pips). Cukup longgar utk data.
    btc_max_risk_usd: float = 3.0

    # --- Routing channel Discord (ID channel bukan rahasia) ---------------
    # Kosongkan salah satu utk fallback ke DISCORD_CHANNEL_ID.
    discord_channels: dict[str, str] = {
        "sinyal": "1511772387828564018",      # 💥sinyal-xauusd
        "report": "1511771662100725861",      # 📑bot-report (hasil TP/SL)
        "analysis": "1511771361326923936",    # 🧠bot-analysis (ringkasan harian)
        "alert": "1511771061572735026",       # ⚡market-alert (burst berita)
        "price": "1511770736518234282",       # 👑gold-price
        "news": "1511770883633451260",        # 🌎market-news
        "calendar": "1511770975564464310",    # 📅economic-calendar
        "dollar": "1511771019868635176",      # 💵dollar-index
        "prediction": "1511771296164348156",  # 👽bot-prediction
        "news_gold": "1526815834712965131",   # 🥇market-news-gold
        "portofolio": "1529772227774513242",   # 📊portofolio (dashboard)
        "weekly": "1529772231402721350",       # 📅rekap-mingguan
        "btc_signal": "1532953796739596379",   # 🪙btc-signal (sinyal BTCUSD)
    }

    # --- Portofolio simulasi (dari hasil sinyal) --------------------------
    portfolio_start: float = 100.0     # saldo awal simulasi ($)
    portfolio_risk_usd: float = 2.0    # $ risiko per 1R (2% dari $100)
    portfolio_target: float = 150.0    # target akun untuk progress bar

    # 20 mata uang utk Dollar Monitor (fmt Twelve Data). XXX/USD = invers.
    dollar_pairs: list[str] = [
        "EUR/USD", "USD/JPY", "GBP/USD", "USD/CHF", "AUD/USD", "NZD/USD",
        "USD/CAD", "USD/CNY", "USD/IDR", "USD/INR", "USD/KRW", "USD/SGD",
        "USD/MYR", "USD/THB", "USD/PHP", "USD/MXN", "USD/BRL", "USD/ZAR",
        "USD/TRY", "USD/SEK",
    ]

    # --- Notifikasi Discord (auto-push sinyal) ----------------------------
    # Pilih SALAH SATU: webhook (paling gampang) ATAU bot (token + channel id).
    # Kalau bot token & channel id diisi, itu yang dipakai; jika tidak, webhook.
    discord_webhook_url: str = ""        # set via env DISCORD_WEBHOOK_URL
    discord_bot_token: str = ""          # set via env DISCORD_BOT_TOKEN
    discord_channel_id: str = ""         # set via env DISCORD_CHANNEL_ID
    signal_auto_push: bool = True        # auto kirim ke Discord saat ada sinyal
    signal_poll_seconds: int = 1800      # cek sinyal tiap N detik (default 30 mnt)
    # Profil yang di-auto-push (pisah koma): harian, scalp, intraday, swing.
    # Dua aliran default: harian (RR1:2, sering) + intraday (RR1:3, selektif).
    signal_profiles: str = "harian,intraday"
    # Minimal keyakinan untuk auto-push: none | medium | strong.
    # "none" = kirim juga sinyal teknikal-only (frekuensi harian, tidak ketat);
    # kartu tetap menampilkan status sentimen + bintang keyakinan.
    signal_min_confidence: str = "none"
    # Batas risiko $ NYATA @0.2 lot cent (jarak SL). $6 = tolak SL > ~300 pips
    # (=$30 gerak = 6% dari $100). Longgar biar sinyal tetap banyak utk data.
    signal_max_risk_usd: float = 6.0

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
