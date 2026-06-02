"""Scraping & scoring sentimen berita untuk XAUUSD (emas) dan USD.

Alur:
  1. fetch_headlines()  -> scrape beberapa RSS berita finansial (gratis, publik).
  2. score_sentiment()  -> beri skor tiap headline pakai leksikon kata kunci
     yang khusus untuk penggerak harga EMAS, lalu agregasi jadi bias arah:
        "long"  -> berita cenderung mendorong emas naik
        "short" -> berita cenderung menekan emas turun
        "flat"  -> netral / data tidak cukup

Kenapa leksikon, bukan model ML berat?
  Railway free-tier RAM kecil. Leksikon ringan, deterministik, gampang dites,
  dan mudah di-tuning. Struktur dipisah: fetch (butuh network) vs score (murni),
  jadi nanti bisa diganti FinBERT/LLM tanpa mengubah pemanggilnya.

Catatan: emas berlawanan arah dengan USD & yield. "Dolar menguat" / "Fed hawkish"
  = bearish emas; "rate cut" / "inflasi panas" / "ketegangan geopolitik" = bullish.
"""
from __future__ import annotations

import re
from typing import Any
from xml.etree import ElementTree as ET

import httpx

# Feed RSS publik (bisa di-override lewat env SENTIMENT_FEEDS, pisah koma).
DEFAULT_FEEDS = [
    "https://www.forexlive.com/feed/news",
    "https://www.fxstreet.com/rss/news",
    "https://www.investing.com/rss/commodities_Gold.rss",
]

# Headline dianggap relevan utk emas hanya jika menyentuh salah satu tema ini.
_RELEVANCE = (
    "gold",
    "xau",
    "bullion",
    "dollar",
    "usd",
    "greenback",
    "fed",
    "fomc",
    "powell",
    "inflation",
    "cpi",
    "pce",
    "treasury",
    "yield",
    "rate",
    "jobs",
    "payroll",
    "nonfarm",
)

# Frasa bullish utk EMAS (skor positif). Bobot = nilai.
_BULLISH: dict[str, float] = {
    "rate cut": 1.5,
    "rate cuts": 1.5,
    "cuts rates": 1.5,
    "dovish": 1.3,
    "weak dollar": 1.3,
    "dollar falls": 1.2,
    "dollar slips": 1.2,
    "dollar weakens": 1.2,
    "dollar drops": 1.2,
    "yields fall": 1.1,
    "yields drop": 1.1,
    "falling yields": 1.1,
    "safe haven": 1.2,
    "safe-haven": 1.2,
    "geopolitical": 1.1,
    "tension": 0.9,
    "tensions": 0.9,
    "war": 0.9,
    "conflict": 0.8,
    "recession": 1.0,
    "recession fears": 1.2,
    "stimulus": 0.9,
    "hot inflation": 1.2,
    "inflation rises": 1.0,
    "sticky inflation": 1.0,
    "fed pause": 1.1,
    "gold rises": 1.4,
    "gold jumps": 1.5,
    "gold gains": 1.4,
    "gold climbs": 1.4,
    "gold rallies": 1.5,
    "gold surges": 1.5,
    "gold hits record": 1.6,
    "record high": 1.2,
    "demand for gold": 1.2,
    "haven demand": 1.2,
}

# Frasa bearish utk EMAS (skor negatif). Bobot = nilai.
_BEARISH: dict[str, float] = {
    "rate hike": 1.5,
    "rate hikes": 1.5,
    "hikes rates": 1.5,
    "hawkish": 1.3,
    "strong dollar": 1.3,
    "dollar rises": 1.2,
    "dollar gains": 1.2,
    "dollar strengthens": 1.2,
    "dollar climbs": 1.2,
    "yields rise": 1.1,
    "yields climb": 1.1,
    "rising yields": 1.1,
    "higher yields": 1.1,
    "tapering": 1.0,
    "tightening": 1.1,
    "risk appetite": 0.9,
    "risk-on": 0.9,
    "risk on": 0.9,
    "stocks rally": 0.8,
    "strong jobs": 1.1,
    "robust jobs": 1.1,
    "strong payrolls": 1.1,
    "cooling inflation": 1.1,
    "inflation eases": 1.1,
    "inflation falls": 1.1,
    "gold falls": 1.4,
    "gold drops": 1.4,
    "gold slips": 1.3,
    "gold slides": 1.4,
    "gold tumbles": 1.5,
    "gold retreats": 1.3,
    "gold edges lower": 1.2,
    "profit-taking": 0.9,
}


def _strip_ns(tag: str) -> str:
    """Buang namespace XML: '{http://...}title' -> 'title'."""
    return tag.split("}", 1)[-1].lower()


def _parse_feed(xml_text: str) -> list[str]:
    """Ambil judul (+deskripsi) item dari satu dokumen RSS/Atom."""
    texts: list[str] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return texts

    for elem in root.iter():
        if _strip_ns(elem.tag) not in ("item", "entry"):
            continue
        title = ""
        desc = ""
        for child in elem:
            name = _strip_ns(child.tag)
            if name == "title" and child.text:
                title = child.text.strip()
            elif name in ("description", "summary") and child.text:
                desc = child.text.strip()
        # Bersihkan tag HTML yang mungkin nyangkut di deskripsi.
        combined = f"{title}. {desc}".strip()
        combined = re.sub(r"<[^>]+>", " ", combined)
        combined = re.sub(r"\s+", " ", combined).strip()
        if combined:
            texts.append(combined)
    return texts


def fetch_headlines(feeds: list[str] | None = None, timeout: float = 12.0) -> list[str]:
    """Scrape semua feed; lewati feed yang gagal. Kembalikan list teks headline."""
    feeds = feeds or DEFAULT_FEEDS
    headlines: list[str] = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; forex-bot/1.0)"}
    with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
        for url in feeds:
            try:
                resp = client.get(url)
                resp.raise_for_status()
            except (httpx.HTTPError, httpx.InvalidURL):
                continue  # satu feed mati tidak boleh menjatuhkan yang lain
            headlines.extend(_parse_feed(resp.text))
    return headlines


def _is_relevant(text: str) -> bool:
    return any(k in text for k in _RELEVANCE)


def _score_one(text: str) -> float:
    """Skor satu headline: total bobot bullish - bearish (0 jika netral)."""
    score = 0.0
    for phrase, w in _BULLISH.items():
        if phrase in text:
            score += w
    for phrase, w in _BEARISH.items():
        if phrase in text:
            score -= w
    return score


def score_sentiment(
    headlines: list[str],
    threshold: float = 0.15,
    min_headlines: int = 3,
) -> dict[str, Any]:
    """Agregasi sentimen dari banyak headline jadi satu bias arah emas.

    Return:
      {
        "bias": "long|short|flat",
        "score": float di [-1, 1] (rata-rata ternormalisasi),
        "headlines_total": int,   # semua yang ter-scrape
        "headlines_scored": int,  # yang relevan & punya sinyal
        "samples": list[str],     # contoh headline pendorong (maks 5)
      }
    """
    total = len(headlines)
    pos_w = 0.0
    neg_w = 0.0
    scored = 0
    samples: list[str] = []

    for raw in headlines:
        text = raw.lower()
        if not _is_relevant(text):
            continue
        s = _score_one(text)
        if s == 0.0:
            continue
        scored += 1
        if s > 0:
            pos_w += s
        else:
            neg_w += -s
        if len(samples) < 5:
            samples.append(raw[:160])

    denom = pos_w + neg_w
    score = 0.0 if denom <= 0 else (pos_w - neg_w) / denom

    if scored < min_headlines:
        bias = "flat"  # data tidak cukup -> jangan paksa arah
    elif score > threshold:
        bias = "long"
    elif score < -threshold:
        bias = "short"
    else:
        bias = "flat"

    return {
        "bias": bias,
        "score": round(score, 4),
        "headlines_total": total,
        "headlines_scored": scored,
        "samples": samples,
    }


def fetch_sentiment(
    feeds: list[str] | None = None,
    threshold: float = 0.15,
    min_headlines: int = 3,
) -> dict[str, Any]:
    """Pipeline lengkap: scrape headline lalu skoring. Dipakai endpoint /sentiment."""
    headlines = fetch_headlines(feeds)
    return score_sentiment(headlines, threshold=threshold, min_headlines=min_headlines)
