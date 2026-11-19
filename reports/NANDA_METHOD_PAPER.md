# Nanda Method — protokol uji XAUUSD M15

Hipotesis: saat tren besar turun, support yang sudah ditembus dapat bertindak
sebagai resistance saat diretest. Ini **paper-only** (tanpa lot dan tanpa order).
Cerita hasil pribadi tidak menjadi data backtest atau proyeksi.

1. Tren turun pada saat breakdown: close H1 terakhir di bawah SMA20 H1 dan
   SMA20 menurun. Ini definisi pengujian, bukan indikator tambahan dari Nanda.
2. Support = low terendah dari 16 candle M15 sebelum breakdown. Candle breakdown
   harus tutup lebih dari 0,30 unit harga di bawah support.
3. Simulasikan sell limit tepat di support selama 12 candle M15 berikutnya.
   Hanya sentuhan pertama; gap open di atas level tidak dianggap fill.
4. SL 50 pip, TP 100 pip, RR 1:2. Pip XAUUSD **Exness = 0,01** unit harga,
   sesuai [spesifikasi Exness](https://get.exness.help/hc/en-us/articles/17854173039388-Commodities).
   Konvensi trial ini tidak mengubah bot lama. Entry 3789,00 berarti SL 3789,50
   dan TP 3788,00, dengan SL/TP SELL dipantau pada harga ask simulasi.
5. Harga feed diperlakukan sebagai bid; spread asumsi 0,30 saat exit SELL.
   Kalau SL kena pada candle retest, catat loss. Kalau low menyentuh TP tapi
   close tidak mengonfirmasi bahwa TP terjadi setelah entry, tandai unknown.
   Setelah retest, jika SL dan TP kena pada candle yang sama, SL didahulukan.
   Posisi tanpa exit setelah dua hari dilabel expired, tanpa hasil R.

Tidak ada filter RSI, volume, atau sentimen. Satu posisi paper pada satu waktu.
Jendela 30 hari dimulai pada run pertama dengan data cukup. Data Twelve Data
M15 dibagi dengan trial XAU yang sudah ada; tidak berangkat dari feed Exness.
Alert bertanda **NANDA METHOD** masuk channel sinyal bila setup masih open dan
umur close candle retest kurang dari 15 menit. Catch-up kandidat lama dicatat
tanpa dikirim. Hasil akhir dikirim ke `bot-report`. Matikan lewat variable
Actions `NANDA_TRIAL_ENABLED=false`.

Log `signals/nanda_trial.json` menyimpan ringkasan dan kandidat per setup.
WR dan R hanya menghitung TP/SL yang jelas, dengan biaya/slippage selain spread
belum dimodelkan. Pantau expectancy, profit factor, drawdown, dan jumlah
unknown/expired, bukan win rate saja. Untuk membandingkan dengan metode lama,
pakai rentang waktu sama dan catat perbedaan SL/TP dan definisi entry.
