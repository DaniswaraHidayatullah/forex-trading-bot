# Forex Trading Bot — XAUUSD & AUDUSD

Bot trading otomatis dengan arsitektur dua bagian:

1. **`data_service/`** — service Python (FastAPI) yang di-deploy ke Railway.
   Tugasnya: scraping kalender berita ForexFactory (blackout) + **scraping
   headline berita finansial lalu menghitung skor sentimen** + ambil data COT,
   lalu menyajikannya lewat HTTP supaya bisa dibaca oleh EA.
2. **`ea/`** — Expert Advisor MQL5 yang jalan di MetaTrader 5 (broker Exness).
   Tugasnya: deteksi tren (H4), cari entry (H1), eksekusi order dengan
   manajemen risiko, dan minta "izin" ke data_service sebelum entry
   (news blackout + bias sentimen gabungan berita & COT).

```
┌────────────────────┐   HTTP    ┌──────────────────────────┐
│  MT5 + EA (MQL5)    │ ───────►  │  data_service (Railway)  │
│  - tren H4          │  /context │  - ForexFactory (blackout)│
│  - entry H1         │ ◄───────  │  - Sentimen berita (RSS) │
│  - lot bertingkat   │   JSON    │  - COT (CFTC)            │
│  - RR 1:3, 3x/hari  │           │  - cache JSON            │
└────────────────────┘           └──────────────────────────┘
```

## Strategi (sesuai desain)

| Parameter        | Nilai                                  |
|------------------|----------------------------------------|
| Pairs            | XAUUSD, AUDUSD                          |
| Timeframe tren   | H4 (EMA 50 vs EMA 200)                  |
| Timeframe entry  | H1 (pullback band RSI + ATR stop)       |
| Lot (per layer)  | bertingkat (lihat tabel di bawah)       |
| Risk : Reward    | 1 : 3                                   |
| Layering         | 1 entry + maks 2 layer (pyramiding searah) |
| Jam operasi      | 24 jam, tanpa batas waktu sesi (`InpMaxTradesDay=0`) |
| Spread guard     | skip entry bila spread > ATR × `InpMaxSpreadAtr` |
| News filter      | hindari entry ±N menit sekitar high-impact |
| Sentimen         | **berita (RSS) sebagai penggerak utama**, COT sebagai konfirmasi |

### Layering (1 entry + 2 layer)

- Posisi pertama = entry awal (layer 1). EA boleh menambah **maks 2 layer lagi**
  (total 3 posisi searah) — diatur `InpMaxLayers`.
- Layer baru ditambah **hanya saat harga sudah bergerak menguntungkan** minimal
  `InpLayerStepAtr × ATR` dari entry layer terjauh (ini **pyramiding ke arah
  profit**, BUKAN averaging-down yang menambah saat rugi).
- Saat menambah layer, SL posisi lama digeser ke **breakeven** (`InpBreakevenLayer`)
  supaya pyramid tidak berbalik jadi rugi.
- Tidak hedging: kalau sinyal berlawanan arah posisi terbuka, EA tidak entry.

> Catatan: kalau yang kamu maksud "layer" adalah **averaging-down** (menambah saat
> harga melawan untuk menurunkan harga rata-rata), beri tahu saya — itu profil
> risiko berbeda dan butuh manajemen SL terpisah. Default saat ini sengaja
> pyramiding karena lebih aman untuk strategi tren + SL tetap + RR 1:3.

### Lot bertingkat (mengikuti ekuitas)

| Ekuitas (USD)     | Lot   |
|-------------------|-------|
| < 400             | 0.01  |
| 400 – 599         | 0.02  |
| 600 – 799         | 0.03  |
| 800 – 999         | 0.04  |
| ≥ 1000            | 0.05 (maks) |

Aturan: di bawah 400 USD = 0.01 lot; mulai 400 USD = 0.02 lot; tiap kenaikan
200 USD lot bertambah 0.01; dibatasi maksimal 0.05. Semua bisa diatur lewat
input EA (`InpEquityBase`, `InpEquityStep`, `InpLotStep`, `InpLotMax`, dst).

### Sentimen berita (modul scraping)

- `data_service/fetchers/sentiment.py` men-scrape headline dari beberapa feed
  RSS finansial (ForexLive, FXStreet, Investing-Gold — bisa diubah via env
  `SENTIMENT_FEEDS`), lalu memberi skor pakai **leksikon penggerak harga emas**
  (mis. "rate cut"/"weak dollar" = bullish emas; "hawkish"/"strong dollar" =
  bearish emas). Hasilnya: `bias` long/short/flat + `score` di [-1, 1].
- Endpoint `GET /sentiment?symbol=XAUUSD` mengembalikan skor sentimen ini.
- `GET /context` menggabungkan **sentimen berita (utama)** dengan **COT
  (konfirmasi)** jadi satu `sentiment_bias` yang dibaca EA. Berita yang memimpin
  supaya bias COT emas yang struktural net-long tidak memblokir SELL permanen.

## Catatan penting

- **Selalu uji di akun DEMO + backtest dulu** sebelum pakai dana riil.
  Di account kecil, proyeksi compounding dibatasi minimum lot size — fase ini
  fokusnya validasi logika bot, bukan profit.
- Ini software, bukan saran finansial. Keputusan trading & risiko ada di tangan kamu.

## Cara push ke GitHub kamu

```bash
cd forex-trading-bot
git init
git add .
git commit -m "Initial scaffold: data service + EA + CI/CD"
git branch -M main
git remote add origin https://github.com/<username>/<repo>.git
git push -u origin main
```

## Deploy data_service ke Railway

1. Buat project baru di Railway, hubungkan ke repo ini.
2. Set **Root Directory** = `data_service`.
3. Start command otomatis dari `Procfile`.
4. Tambahkan environment variable bila perlu (lihat `.env.example`).
5. Setelah live, salin URL publiknya (mis. `https://xxx.up.railway.app`)
   ke input `DataServiceUrl` di EA.

## Jalanin data_service lokal (untuk tes)

```bash
cd data_service
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
# cek: http://localhost:8000/context?symbol=XAUUSD
```

## Biaya & API key

**Semua sumber data GRATIS dan TANPA API key:**

| Sumber              | Dipakai untuk        | Key? | Biaya |
|---------------------|----------------------|------|-------|
| ForexFactory feed   | kalender / blackout  | Tidak | Gratis |
| RSS (ForexLive dll) | sentimen berita      | Tidak | Gratis |
| CFTC Socrata        | COT positioning      | Tidak | Gratis* |
| Railway free tier   | hosting data_service | Tidak** | Gratis (ada kuota) |
| MetaTrader 5 + Demo | eksekusi & backtest  | Tidak | Gratis |

\* CFTC mengizinkan app-token opsional untuk rate-limit lebih tinggi — **tidak
diperlukan** di sini. \** Railway butuh akun, bukan API key kode.

> Kalau nanti mau berita real-time yang lebih akurat (NewsAPI, Marketaux,
> Alpha Vantage News), itu **baru** butuh API key. Semuanya punya free tier,
> tapi belum dipakai sekarang. Kalau mau, bilang saja — akan saya kabari batas
> free tier-nya sebelum dipasang.

## Demo & Backtest (cara & gratis)

Dua cara menguji sebelum uang riil, keduanya **tanpa biaya**:

### 1. Backtest (Strategy Tester MT5) — cepat, pakai data historis
1. Buka MT5 → **View → Strategy Tester** (Ctrl+R).
2. Pilih Expert = `TrendEA`, Symbol = `XAUUSD`, Timeframe = `H1`.
3. Model = **"Every tick based on real ticks"** (paling akurat untuk gold).
4. Atur rentang tanggal (mis. 1–2 tahun terakhir) lalu **Start**.
5. WebRequest tidak jalan di tester, jadi EA otomatis menganggap context
   "boleh entry, bias flat" (sudah ditangani di kode) — **news/sentimen tidak
   ikut diuji di backtest**, hanya logika teknikal + layering + lot + SL/TP.
6. Lihat tab **Results/Graph**: profit factor, drawdown, jumlah trade.

> Penting: backtest **tidak** menguji filter berita/sentimen (karena butuh
> internet/WebRequest). Untuk menguji itu, pakai forward-test demo (cara 2).

### 2. Forward-test akun DEMO — realistis, uji penuh termasuk berita
1. Buka akun **DEMO** di broker (mis. Exness) — gratis, saldo virtual.
2. Deploy `data_service` ke Railway (gratis) atau jalankan lokal.
3. Di MT5: **Tools → Options → Expert Advisors → Allow WebRequest** dan
   masukkan URL data_service (ini bagian yang kamu bilang nanti kamu bantu).
4. Pasang `TrendEA` ke chart `XAUUSD H1`, isi input `InpDataServiceUrl`.
5. Biarkan jalan beberapa hari/minggu — di sini news filter, sentimen berita,
   layering, dan lot bertingkat semuanya aktif penuh & realistis.

Rekomendasi: backtest dulu untuk validasi logika teknikal, lalu demo
forward-test minimal 2–4 minggu sebelum mempertimbangkan akun riil kecil.

## Perbandingan dengan bot trading lain

| Aspek                 | Bot ini                                   | EA grid/martingale komersial | Freqtrade / FinRL (ML) |
|-----------------------|-------------------------------------------|------------------------------|------------------------|
| Arah strategi         | Tren H4 + pullback H1 (disiplin)          | Sering counter-trend / grid  | Belajar dari data      |
| Manajemen risiko      | SL ATR, RR 1:3, breakeven, lot terukur    | Sering tanpa SL (berbahaya)  | Bergantung konfigurasi |
| Layering              | Pyramiding ke profit, maks 3, BE-protected| Averaging-down (rawan MC)    | Jarang bawaan          |
| News filter           | Ada (blackout high-impact)                | Umumnya tidak ada            | Perlu integrasi sendiri|
| Sentimen berita       | **Ada (scraping RSS + skor)**             | Tidak ada                    | Bisa, tapi rumit       |
| Biaya/API key         | Gratis, tanpa key                         | Sering berbayar              | Gratis (self-host)     |
| Backtest              | MT5 Strategy Tester (teknikal)            | MT5                          | Backtest engine kuat   |
| Kompleksitas setup    | Sedang (EA + 1 service)                   | Rendah                       | Tinggi                 |

**Kelebihan bot ini:** disiplin tren + SL tetap (tidak seperti martingale yang
rawan margin call), punya **lapisan sentimen berita** yang jarang dimiliki EA
ritel, dan semuanya gratis. **Kekurangan vs bot ML:** belum ada pembelajaran
adaptif (bobot leksikon masih manual) dan backtest tidak mencakup berita.

### Ide improve lanjutan (opsional, bertahap)
- Upgrade skor sentimen leksikon → **FinBERT/LLM** (lebih akurat; LLM butuh key).
- Trailing stop ATR untuk mengunci profit pyramid (sekarang baru breakeven).
- Filter kualitas tren (ADX / slope EMA) agar tak entry saat sideways.
- Logging hasil ke file/Sheet untuk evaluasi performa per kondisi berita.
- Walk-forward test untuk validasi parameter (anti over-fitting).
