# Laporan Audit Sistem Sinyal XAUUSD M15

**Tanggal:** 2026-07-23 · **Modal simulasi:** $100 · **Tahap:** 1 (audit) + 2 (backtest)
**Batasan tahap ini:** tidak ada VPS / MT5 / EA / auto-execution (ditunda awal Agustus).

---

## 1. Flow sistem saat ini

```
GitHub Actions cron (*/15 menit)
        │
        ▼
tools/run_signal_once.py  (scheduler + orchestrator)
        │  baca state: signals/log.json, signals/meta.json
        ├── _resolve_open()   → cek posisi terbuka kena TP/SL → kirim 📑bot-report
        ├── _new_signals()    → main._signal_for(profil) → signal_engine.build_signal()
        │                        └── konfirmasi arah pakai /context (sentimen+COT+news)
        ├── _check_burst()    → deteksi ledakan harga → ⚡market-alert
        ├── _daily_digest()   → ringkasan harian → 🧠bot-analysis
        └── _market_feeds()   → 👑price 🥇news-gold 🌎news 📅calendar 💵dollar 👽predict
        │
        ▼
commit signals/*.json  (state persist) + push embed ke Discord (bot REST)
```

Harga: Twelve Data (`XAU/USD`). Berita: RSS multi-sumber → leksikon. Tidak ada MT5/broker.

---

## 2. Pemetaan file (aktual → struktur GPT yang diharapkan)

| Fungsi | File saat ini | Modul GPT setara |
|---|---|---|
| Technical signal + confidence + SL/TP | `data_service/fetchers/signal_engine.py` | signal_engine + technical_indicators + risk_manager |
| Sentiment lexicon + skoring | `data_service/fetchers/sentiment.py` | sentiment_lexicon + sentiment_gate |
| Gabungan bias (gate) | `data_service/main.py::_combine_bias`, `context` | sentiment_gate |
| Scheduler / orchestrator | `tools/run_signal_once.py` + `.github/workflows/signal.yml` | scheduler (terpisah) |
| Logging / state | `signals/log.json`, `signals/meta.json` (via runner) | logger + state_manager |
| Pelacak hasil (TP/SL, winrate) | `data_service/fetchers/tracker.py` | performance_metrics + shadow_tester |
| Backtest | `tools/backtest.py`, **`tools/backtest_scenarios.py` (baru)** | backtester |
| Notifikasi | `data_service/fetchers/notifier.py` | (di luar scope GPT) |

> **Catatan:** struktur `src/…` yang GPT usulkan BELUM diterapkan — restructure penuh = risiko tinggi untuk sistem yang sedang live. Pemetaan di atas menunjukkan fungsi sudah termodularisasi (engine terpisah dari scheduler), hanya beda lokasi/nama. Rekomendasi: rename bertahap saat migrasi VPS, bukan sekarang.

---

## 3. Temuan bug & risiko

### 🟢 AMAN (sudah benar)
- **Look-ahead bias — BERSIH.** `build_signal` memakai indikator bar `[-2]` (candle tertutup), bukan bar berjalan. Tren H1 dipetakan dari bar H1 tertutup (`+timedelta(1h) <= t`). Harga real-time (`quote`) hanya untuk *entry price*, dengan guard "menyimpang >1 ATR → skip". Backtest baru (`backtest_scenarios.py`) juga memakai close candle selesai + resolve ke depan → tidak ada kebocoran.
- **State persist.** `signals/log.json` + `meta.json` di-commit tiap run → candle terakhir & posisi terbuka tersimpan; setelah workflow gagal, run berikutnya lanjut dari state.
- **Maks 1 posisi/profil.** Cegah spam saat setup bertahan.

### 🟡 RISIKO (perlu diperbaiki saat refactor / VPS)
1. **Duplikat sinyal — mitigasi tidak langsung.** Dedup saat ini bergantung pada *open-position check*, BUKAN `candle_timestamp`. Jika satu candle M15 diproses 2× (cron jitter) saat belum ada posisi terbuka, teori-nya bisa 2 sinyal — tapi sinyal pertama membuka posisi → yang kedua ke-skip. **Belum ada penanda `candle_ts` eksplisit.** → Rekomendasi: simpan `last_signal_candle_ts` per profil, tolak jika sama.
2. **Candle M15 terlewat.** Cron 15 menit + jitter GitHub (5–15 mnt telat). Runner hanya melihat candle **terakhir tertutup**; jika 2 candle tertutup di antara 2 run (cron telat >15 mnt), candle di tengah **tidak pernah dievaluasi**. Ini penyebab utama "sinyal terlambat/terlewat" yang kamu rasakan. → Solusi nyata baru di VPS (loop tiap menit). Untuk sekarang: terima keterbatasan, dokumentasikan.
3. **Confidence rating BEDA dari spec GPT.** Sistem live saat ini **mengeksekusi ⭐ (1-bintang) juga** (setelah pelonggaran kemarin). Spec GPT: hanya **⭐⭐⭐** yang dieksekusi, ⭐/⭐⭐ jadi shadow. → Ini keputusan desain, bukan bug. Perlu diselaraskan (lihat §6).
4. **Logging belum selengkap spec.** Field seperti `MAE/MFE/spread/slippage/technical_score/matched_lexicon_terms/exit_reason` belum dicatat di `log.json` live (baru ada di backtester). → Perlu diperkaya sebelum forward-test serius.
5. **Sentiment gate tidak bisa di-backtest historis** (tidak ada arsip berita ber-timestamp per candle). → Efeknya HANYA terukur live via shadow tracking (sudah jalan).

---

## 4. HASIL BACKTEST 3 SKENARIO (data nyata 210 hari, M15, semua sinyal teknikal)

Aturan identik semua skenario; hanya SL/TP beda. Termasuk spread 3 pip + slippage.
Ini grup **ALL TECHNICAL** (tanpa sentiment gate — belum bisa historis).

| Skenario | Trade | WR% | Profit Factor | Expectancy/trade | Net R | Net $ | Max DD |
|---|---|---|---|---|---|---|---|
| **A · SL30/TP60** (risk 3%) | 6300 | 31.7% | **0.73** ❌ | −0.050R | −315R | −$4095 | −430R |
| **B · SL50/TP100** (risk 5%) | 4305 | 33.5% | 0.87 ❌ | +0.005R | +21R | −$2048 | −109R |
| **C · SL100/TP200** (risk 10%) | 1986 | 34.9% | **1.00** | +0.047R | +93R | −$63 | −41R |

**Temuan kunci (berbasis data, bukan asumsi):**
- **30:60 adalah yang TERBURUK, bukan terbaik** — persis peringatan agar tidak memilih hanya karena risiko kecil. SL 30 pip terlalu ketat untuk emas: **4042 dari 4305 loss "kena SL lalu harga tetap mencapai arah TP"** = ter-wick habis-habisan.
- **Makin lebar SL makin baik** di semua metrik: WR↑, PF↑, expectancy↑, drawdown↓, wick-out↓.
- **Tidak ada satu pun skenario yang PROFIT bersih** setelah spread — bahkan C hanya break-even (PF 1.00). Strategi teknikal MURNI (semua setup, semua sesi) = tidak punya edge setelah biaya.
- Sesi **New York** paling bagus di ketiga skenario; **London** paling jelek.
- WR 31–35% — jauh dari 60% trial (10 trade = sampel keberuntungan kecil).

**Konsekuensi untuk $100 (jujur):** target "3% risk" (SL 30 pip) secara matematis **bertabrakan** dengan realita volatilitas emas — SL sedekat itu pasti ter-wick. Di modal $100 + lot minimum 0.01, **tidak bisa punya risiko 3% DAN SL yang cukup lebar sekaligus**. Pilihannya: (a) terima risiko lebih tinggi (SL lebih lebar), atau (b) tambah modal agar 0.01 lot = %-risiko lebih kecil, atau (c) andalkan FILTER (sesi NY + sentiment gate + confidence) untuk menyaring, bukan sekadar mengetatkan SL.

---

## 5. Peran sentiment gate & confidence (kenapa live > backtest teknikal murni)

Backtest di atas mengeksekusi SEMUA setup teknikal (~9–30/hari) → rugi. Sistem LIVE hanya eksekusi sebagian kecil (10 trade dalam beberapa minggu) karena disaring: sesi, batas risiko, sentiment gate, confidence. **Inilah hipotesis utama yang harus diuji:** apakah penyaringan (bukan strategi dasarnya) yang menciptakan edge. Shadow tracking (executed vs blocked vs all-technical) yang sudah jalan adalah alat ukurnya — butuh lebih banyak sampel.

---

## 6. Rencana perubahan (usulan, BELUM dieksekusi)

**Prioritas tinggi (sebelum forward-test serius):**
1. **Selaraskan confidence** dgn spec: hanya ⭐⭐⭐ dieksekusi; ⭐/⭐⭐ jadi shadow. (keputusan user)
2. **Dedup by candle_timestamp** — simpan `last_signal_candle_ts` per profil.
3. **Perkaya logging** — tambah field MAE/MFE/spread/slippage/matched_terms/exit_reason ke `log.json`.
4. **Config pip/point eksplisit** (sudah ada di backtester `PipConfig`; pindahkan ke `config/` saat data broker riil tersedia).

**Prioritas menengah:**
5. Pertimbangkan **SL adaptif-ATR** (bukan fixed pip) — backtest lama menunjukkan ATR-based lebih baik dari fixed pip; tapi konfirmasi dgn sampel lebih besar.
6. Fokus filter sesi **New York** (bukti kuat di 3 skenario).

**Ditunda ke awal Agustus (VPS):**
- Windows VPS + MT5 demo + EA/auto-exec + forward-test 24/5 + loop per-menit (hilangkan missed-candle).

---

## 7. Keputusan yang butuh input kamu

1. **Confidence:** kembalikan ke "hanya ⭐⭐⭐ dieksekusi" (spec GPT, lebih selektif) atau pertahankan pelonggaran kemarin (lebih sering, kualitas turun)?
2. **SL/TP:** dari data, **C (100:200) paling sehat** tapi risiko 10%/trade di $100. Mau tetap fixed 100:200, atau balik ke **ATR-adaptif** (lebih pintar mengikuti volatilitas)?
3. **Prioritas berikutnya:** selesaikan logging + dedup dulu, atau langsung uji "executed vs shadow" lebih lama untuk membuktikan nilai sentiment gate?

> Semua temuan di sini dapat direproduksi: `python tools/backtest_scenarios.py`.
