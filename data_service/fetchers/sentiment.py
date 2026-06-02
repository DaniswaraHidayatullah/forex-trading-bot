"""Scraping & scoring sentimen berita untuk XAUUSD (emas) dan USD.

Alur:
  1. fetch_headlines()  -> scrape beberapa RSS berita finansial (gratis, publik).
  2. score_sentiment()  -> beri skor tiap headline pakai leksikon kata kunci
     khusus penggerak harga EMAS, lalu agregasi jadi bias arah:
        "long"  -> berita cenderung mendorong emas naik
        "short" -> berita cenderung menekan emas turun
        "flat"  -> netral / data tidak cukup

Scorer leksikon ini ringan & deterministik (cocok Railway free tier, tanpa
torch/model berat). Peningkatan dibanding versi awal:
  - handling NEGASI ("no rate cut", "dollar not rising") -> sinyal dibalik
  - INTENSIFIER/DAMPENER ("sharply"/"slightly") -> bobot dikuat/dilemahkan
  - DEDUP headline berulang antar-feed -> satu berita tak mendominasi
  - leksikon diperluas (real yields, DXY, ETF flow, central bank buying, dll.)

Arsitektur dibuat PLUGGABLE: score_texts(..., backend=...) memilih backend.
Default "lexicon" (gratis). Backend "llm" tersedia sebagai opsi upgrade (butuh
paket `anthropic` + ANTHROPIC_API_KEY); kalau tak tersedia otomatis fallback ke
lexicon supaya default selalu jalan tanpa biaya.

Catatan: emas berlawanan arah dengan USD & yield. "Dolar menguat" / "Fed hawkish"
  = bearish emas; "rate cut" / "inflasi panas" / "ketegangan geopolitik" = bullish.
"""
from __future__ import annotations

import os
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
    "gold", "xau", "bullion", "dollar", "usd", "greenback", "dxy",
    "fed", "fomc", "powell", "inflation", "cpi", "pce", "ppi",
    "treasury", "yield", "yields", "rate", "rates", "jobs", "payroll",
    "nonfarm", "geopolitical", "safe haven", "safe-haven",
)

# Frasa bullish utk EMAS (skor positif). Bobot = nilai.
_BULLISH: dict[str, float] = {
    "rate cut": 1.5, "rate cuts": 1.5, "cuts rates": 1.5, "cut rates": 1.4,
    "dovish": 1.3, "dovish fed": 1.4, "dovish pivot": 1.5,
    "weak dollar": 1.3, "dollar falls": 1.2, "dollar slips": 1.2,
    "dollar weakens": 1.2, "dollar drops": 1.2, "softer dollar": 1.2,
    "yields fall": 1.1, "yields drop": 1.1, "falling yields": 1.1,
    "lower yields": 1.1, "real yields fall": 1.2,
    "safe haven": 1.2, "safe-haven": 1.2, "haven demand": 1.2,
    "geopolitical": 1.1, "tension": 0.9, "tensions": 0.9, "war": 0.9,
    "conflict": 0.8, "escalation": 1.0,
    "recession": 1.0, "recession fears": 1.2, "stimulus": 0.9,
    "hot inflation": 1.2, "inflation rises": 1.0, "sticky inflation": 1.0,
    "inflation accelerates": 1.1, "cpi beats": 1.0, "fed pause": 1.1,
    "central bank buying": 1.3, "etf inflows": 1.1, "gold demand": 1.2,
    "gold rises": 1.4, "gold jumps": 1.5, "gold gains": 1.4, "gold climbs": 1.4,
    "gold rallies": 1.5, "gold surges": 1.5, "gold soars": 1.6,
    "gold hits record": 1.6, "record high": 1.2, "all-time high": 1.3,
}

# Frasa bearish utk EMAS (skor negatif). Bobot = nilai.
_BEARISH: dict[str, float] = {
    "rate hike": 1.5, "rate hikes": 1.5, "hikes rates": 1.5, "hike rates": 1.4,
    "hawkish": 1.3, "hawkish fed": 1.4, "hawkish pivot": 1.5,
    "strong dollar": 1.3, "dollar rises": 1.2, "dollar gains": 1.2,
    "dollar strengthens": 1.2, "dollar climbs": 1.2, "firmer dollar": 1.2,
    "yields rise": 1.1, "yields climb": 1.1, "rising yields": 1.1,
    "higher yields": 1.1, "real yields rise": 1.2,
    "tapering": 1.0, "tightening": 1.1, "qt": 0.8,
    "risk appetite": 0.9, "risk-on": 0.9, "risk on": 0.9, "stocks rally": 0.8,
    "strong jobs": 1.1, "robust jobs": 1.1, "strong payrolls": 1.1,
    "cooling inflation": 1.1, "inflation eases": 1.1, "inflation falls": 1.1,
    "cpi misses": 1.0, "etf outflows": 1.1,
    "gold falls": 1.4, "gold drops": 1.4, "gold slips": 1.3, "gold slides": 1.4,
    "gold tumbles": 1.5, "gold retreats": 1.3, "gold edges lower": 1.2,
    "gold sinks": 1.5, "profit-taking": 0.9,
}

# Kata yang membalik arti frasa sesudahnya (negasi).
_NEGATORS = {
    "no", "not", "non", "never", "without", "fails", "fail", "failed",
    "denies", "denied", "rules", "unlikely", "less", "lower", "lack",
    "isn't", "aren't", "won't", "wasn't", "doesn't", "didn't", "don't",
}

# Pengali bobot bila kata ini mendahului frasa.
_INTENSIFIERS = {
    "sharply": 1.4, "surges": 1.4, "soars": 1.5, "plunges": 1.4,
    "strongly": 1.3, "significantly": 1.3, "heavily": 1.3, "very": 1.2,
    "deeply": 1.3, "record": 1.3,
}
_DAMPENERS = {
    "slightly": 0.6, "modest": 0.6, "modestly": 0.6, "edges": 0.6,
    "marginally": 0.5, "somewhat": 0.7, "mildly": 0.6,
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
        combined = f"{title}. {desc}".strip()
        combined = re.sub(r"<[^>]+>", " ", combined)   # buang tag HTML
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


def _dedupe(headlines: list[str]) -> list[str]:
    """Buang headline duplikat/near-duplikat antar-feed (key alnum 60 char)."""
    seen: set[str] = set()
    out: list[str] = []
    for h in headlines:
        key = re.sub(r"[^a-z0-9]", "", h.lower())[:60]
        if key and key not in seen:
            seen.add(key)
            out.append(h)
    return out


def _context_adjust(text: str, idx: int) -> tuple[float, float]:
    """Lihat <=3 kata sebelum frasa di posisi idx -> (sign, multiplier)."""
    before = text[max(0, idx - 32):idx]
    words = [w.strip(".,;:!?()'\"") for w in before.split()[-3:]]
    sign = 1.0
    mult = 1.0
    for w in words:
        if w in _NEGATORS:
            sign *= -1.0
        if w in _INTENSIFIERS:
            mult *= _INTENSIFIERS[w]
        if w in _DAMPENERS:
            mult *= _DAMPENERS[w]
    return sign, mult


def _phrase_indices(text: str, phrase: str) -> list[int]:
    idxs: list[int] = []
    start = 0
    while True:
        i = text.find(phrase, start)
        if i < 0:
            break
        idxs.append(i)
        start = i + len(phrase)
    return idxs


def _score_one(text: str) -> float:
    """Skor satu headline: bobot bullish - bearish, dgn negasi & intensifier."""
    score = 0.0
    for phrase, w in _BULLISH.items():
        for idx in _phrase_indices(text, phrase):
            sign, mult = _context_adjust(text, idx)
            score += sign * mult * w
    for phrase, w in _BEARISH.items():
        for idx in _phrase_indices(text, phrase):
            sign, mult = _context_adjust(text, idx)
            score -= sign * mult * w
    return score


def score_sentiment(
    headlines: list[str],
    threshold: float = 0.15,
    min_headlines: int = 3,
) -> dict[str, Any]:
    """Agregasi sentimen dari banyak headline jadi satu bias arah emas (lexicon)."""
    headlines = _dedupe(headlines)
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
        "backend": "lexicon",
        "headlines_total": total,
        "headlines_scored": scored,
        "samples": samples,
    }


def _score_llm(headlines: list[str], threshold: float, min_headlines: int) -> dict[str, Any]:
    """Backend opsional pakai LLM (Anthropic). Lazy import; kalau paket/Key tak
    ada -> ValueError supaya pemanggil fallback ke lexicon. Tetap gratis secara
    default karena backend ini hanya aktif bila SENTIMENT_BACKEND=llm + key ada.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY belum diset")
    try:
        import anthropic  # noqa: F401  (lazy; tidak ada di requirements default)
    except ImportError as e:  # pragma: no cover - hanya saat backend llm dipakai
        raise ValueError("paket 'anthropic' belum terpasang") from e

    items = _dedupe([h for h in headlines if _is_relevant(h.lower())])[:30]
    if len(items) < min_headlines:
        return {"bias": "flat", "score": 0.0, "backend": "llm",
                "headlines_total": len(headlines), "headlines_scored": len(items),
                "samples": items[:5]}

    client = anthropic.Anthropic(api_key=api_key)
    joined = "\n".join(f"- {t[:180]}" for t in items)
    prompt = (
        "Nilai sentimen berikut untuk HARGA EMAS (XAUUSD). Balas HANYA satu "
        "angka desimal -1..1 (negatif=bearish emas, positif=bullish emas):\n" + joined
    )
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=16,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = "".join(getattr(b, "text", "") for b in msg.content).strip()
    m = re.search(r"-?\d+(\.\d+)?", raw)
    score = max(-1.0, min(1.0, float(m.group()))) if m else 0.0
    bias = "long" if score > threshold else "short" if score < -threshold else "flat"
    return {"bias": bias, "score": round(score, 4), "backend": "llm",
            "headlines_total": len(headlines), "headlines_scored": len(items),
            "samples": items[:5]}


def score_texts(
    headlines: list[str],
    threshold: float = 0.15,
    min_headlines: int = 3,
    backend: str = "lexicon",
) -> dict[str, Any]:
    """Dispatcher backend. Default 'lexicon' (gratis). 'llm' opsional dgn
    fallback otomatis ke lexicon bila tak tersedia -> default selalu jalan.
    """
    if backend == "llm":
        try:
            return _score_llm(headlines, threshold, min_headlines)
        except Exception:
            pass  # fallback aman ke lexicon
    return score_sentiment(headlines, threshold=threshold, min_headlines=min_headlines)


def fetch_sentiment(
    feeds: list[str] | None = None,
    threshold: float = 0.15,
    min_headlines: int = 3,
    backend: str = "lexicon",
) -> dict[str, Any]:
    """Pipeline lengkap: scrape headline lalu skoring. Dipakai endpoint /sentiment."""
    headlines = fetch_headlines(feeds)
    return score_texts(headlines, threshold=threshold, min_headlines=min_headlines, backend=backend)
