"""Prediksi PRA-berita untuk EMAS (XAUUSD): skenario arah + lean teknikal/sentimen.

Kita TIDAK menebak angka aktual (mustahil sebelum rilis). Yang disediakan:
  - klasifikasi arah emas BILA aktual > forecast (indikator normal vs inverse)
  - agregasi kondisi SEKARANG (tren H1/intraday + momentum + sentimen) jadi lean

Dipakai runner untuk kartu di channel 🔮news-prediction, ~30-90 menit sebelum
rilis data USD high-impact. Sifatnya skenario ("kalau X → emas Y"), bukan kepastian.
"""
from __future__ import annotations

# Indikator "INVERSE": nilai LEBIH TINGGI = ekonomi LEBIH LEMAH -> USD turun ->
# emas NAIK (kebalikan mayoritas data di mana 'tinggi = kuat = emas turun').
_INVERSE_KEYS = (
    "unemployment rate", "claims", "unemployment change", "misery",
)


def higher_actual_gold_dir(title: str) -> str:
    """Arah emas bila AKTUAL > FORECAST ('data lebih panas').

    'down' untuk mayoritas indikator (data kuat -> USD kuat -> emas turun);
    'up' untuk indikator inverse (mis. tingkat pengangguran naik = lemah).
    """
    t = (title or "").lower()
    return "up" if any(k in t for k in _INVERSE_KEYS) else "down"


def current_lean(h_trend: str | None, i_trend: str | None,
                 momentum: str | None, sent_bias: str | None) -> tuple[str, int, int]:
    """Agregasi kondisi sekarang jadi lean arah emas.

    Return (lean, up_votes, dn_votes). 4 faktor: tren H1, tren intraday,
    momentum, sentimen. Mayoritas menang; seri -> 'flat'.
    """
    ups = [h_trend == "up", i_trend == "up",
           momentum == "up", sent_bias == "long"].count(True)
    dns = [h_trend == "down", i_trend == "down",
           momentum == "down", sent_bias == "short"].count(True)
    lean = "up" if ups > dns else "down" if dns > ups else "flat"
    return lean, ups, dns


def scenario(title: str) -> dict[str, str]:
    """Skenario dua arah utk sebuah event. hot=aktual>forecast, cool=aktual<forecast."""
    hot = higher_actual_gold_dir(title)
    return {"hot_dir": hot, "cool_dir": "up" if hot == "down" else "down"}
