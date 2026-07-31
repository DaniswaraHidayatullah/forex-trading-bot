# Sistem Sinyal v2 — Sentimen Diperkuat (arsip v1 dipertahankan)

Tanggal: 2026-08-01. Basis: evaluasi 52 trade v1 (WR 42.3%, +22R, PF 1.73, sim ~$144).

## Masalah v1 (kenapa harus diperbaiki)
Bintang keyakinan v1 **terkonfounding dengan arah**:
- Semua 21 sinyal ⭐⭐⭐ = BUY → WR 19%, **-9R** (rugi).
- Semua 29 sinyal ⭐ = SELL → WR 59%, **+30R** (untung).

Akar masalah: leksikon sentimen tahan-bullish (safe-haven/geopolitik) sepanjang
periode emas justru **turun**. Sentimen jadi kontrarian → menaikkan BUY yang salah
ke ⭐⭐⭐. Profit v1 datang dari **teknikal tren** (SELL saat downtrend), bukan sentimen.

## Perubahan v2
1. **Konfirmasi 3-lapis untuk keyakinan** (bukan lagi sentimen saja):
   - Lapis 1 — teknikal (setup dasar) → selalu minimal ⭐.
   - Lapis 2 — sentimen searah **dan** kuat (`|skor| ≥ SENT_STRONG`) → +1.
   - Lapis 3 — **momentum harga** searah (bar entry ~1 jam terakhir, tertutup) → +1.
   - ⭐⭐⭐ hanya bila ketiganya sepakat. Ini memutus kasus "sentimen bullish tapi
     harga turun naik ke ⭐⭐⭐".
2. **Sentimen tidak lagi memveto arah.** Di v1 berita sangat-kuat berlawanan
   memblokir sinyal (dicatat "shadow"). Shadow membuktikan gate itu 50:50 (tak
   menambah nilai), jadi v2 menjadikan sentimen **label keyakinan**, bukan gate.
   Semua setup teknikal dikirim; kekuatan/kelemahan tampil di bintang.
3. **Field baru di log tiap sinyal:** `version` ("v1"/"v2") + `momentum`
   ("up"/"down"/"flat"). Rekap memisah statistik v1 (arsip) vs v2 (aktif) dan
   memecah WR v2 per bintang → uji apakah ⭐⭐⭐ v2 benar lepas dari efek arah.

v1 tidak dihapus: cabang `version=="v1"` di `signal_engine.py` menyimpan perilaku
lama, dan 52 trade lama tetap di `signals/log.json` (dihitung sebagai v1).

## Kalibrasi broker (Exness Standard)
Contract XAUUSD = 100 oz/lot → di **0.01 lot = 1 oz**. Maka **$ risiko per 0.01
lot = jarak SL dalam dolar** (SL $5 = rugi $5). 1 pip (0.10) = $0.10.
Config: `broker_account="standard"`, `pip_price=0.10`, `usd_per_pip=0.10`.

> Catatan risiko modal $100: lot minimum 0.01 tak bisa diperkecil, jadi tiap
> trade menanggung ~3–8% (sesuai jarak SL/ATR emas), 2 posisi bisa ~10–15%.
> Ini konsekuensi akun Standard di modal kecil (Cent akan ~100× lebih halus).
