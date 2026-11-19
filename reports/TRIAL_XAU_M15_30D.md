# Uji sinyal XAUUSD M15 selama 30 hari

Status: paper/shadow saja. Tidak ada order, EA, MT5, VPS, atau transaksi akun.
Sinyal utama (`signals/log.json`) dan konfigurasinya tidak berubah.

## Aturan kandidat

- Sumber harga: Twelve Data XAU/USD M15; hanya candle yang sudah selesai.
- Ikut tren: H1 EMA21/50 terpisah lebih dari 1,5 ATR M15, M15 RSI 40–60,
  candle M15 ditutup searah tren.
- Balik ekstrem: H1 tidak sedang tren kuat, RSI M15 <=35 dengan candle naik
  untuk BUY atau >=65 dengan candle turun untuk SELL.
- Entry hipotesis: open M15 berikutnya. SL: maksimum antara 1,2 ATR dan wick
  12 candle terakhir plus buffer 0,5 ATR + 0,30 harga. TP = 2 kali jarak SL.
- Satu kandidat per setup per candle. Maksimum satu posisi paper terbuka per
  setup; tidak ada layering. Kandidat lain tetap bisa muncul setelah posisi
  paper setup itu selesai.
- Tidak ada syarat sentimen untuk menciptakan kandidat; eksperimen ini menguji
  sinyal teknikal lebih sering. Sentimen bot utama tidak diubah.

## Pencatatan dan Discord

- File `signals/trial_log.json` dibuat pada run pertama yang berhasil; trial
  berakhir 30 hari sesudahnya. Status: open, win, loss, expired, unknown.
- ID memakai nama setup dan timestamp candle, sehingga run GitHub yang berulang
  tidak menggandakan log. Candle yang terlewat karena cron terlambat dicatat
  sebagai `late` dan tidak dikirim sebagai sinyal fresh.
- Paling banyak enam kartu PAPER per hari UTC di Discord, tanpa `@everyone`.
  Secara default dikirim ke channel sinyal yang ada. Bila ingin channel khusus,
  isi GitHub Actions variable `DISCORD_TRIAL_CHANNEL_ID` dengan ID channel itu.
- `TRIAL_ENABLED=false` menonaktifkan trial tanpa mematikan bot utama.
- Setelah 30 hari dan tidak ada posisi paper terbuka, ringkasan dikirim ke
  channel report sekali: frekuensi, WR, net R, PF, dan jumlah unknown.

## Keterbatasan penting

- Prapemeriksaan kasar pada 10.000 candle Exness M15 (17 Maret–18 Agustus
  2026) menghasilkan 265 kandidat non-overlap: 54 win, 134 loss, 14 expired,
  62 unknown karena celah data, dan 1 masih open pada ujung data. WR yang
  dapat dinilai 28,7%, net -26R sebelum slippage. Ini **bukan** bukti strategi
  bagus; trial ini hanya mengumpulkan bukti prospektif, bukan rekomendasi entry.
  Feed historis Exness dan feed live Twelve Data juga tidak identik.
- Hasil adalah simulasi dari candle OHLC Twelve Data, bukan fill Exness.
  Spread diasumsikan 0,30 satuan harga, slippage 0. SELL diperiksa di ask
  perkiraan. Jika SL dan TP tersentuh pada candle sama, dihitung loss.
- Jeda data menjadi `unknown`, tidak diam-diam dianggap menang/kalah.
- Estimasi USD pada 0,2 cent lot adalah asumsi, bukan lot rekomendasi. Periksa
  contract size, tick value, spread, dan volume di MT5 XAUUSDc sebelum
  menggunakan sinyal ini dengan modal $100.
- GitHub Actions bisa terlambat; tidak ada jaminan jumlah sinyal harian.
- Strategi belum terbukti menguntungkan. Nilai setelah 30 hari harus dilihat
  bersama ukuran sampel, biaya aktual, net R, PF, dan drawdown. Jika jumlah
  sinyal tertutup masih sedikit, lanjutkan pengamatan alih-alih mengklaim edge.
