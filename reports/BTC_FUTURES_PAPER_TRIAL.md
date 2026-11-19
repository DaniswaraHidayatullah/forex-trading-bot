# BTC futures research trial (30 hari)

Jalur ini **bukan sinyal live atau instruksi membuka posisi**. Bot BTCUSD lama
tetap utuh. Jalur baru membaca candle publik **Binance USD-M BTCUSDT perpetual**
M15 setiap run GitHub Actions (jadwal nominal 15 menit), tanpa API key dan tanpa
akses akun exchange. Jika endpoint tidak tersedia, jalur BTC paper gagal aman;
bot lain tetap berjalan. Tidak ada jaminan jumlah kandidat per hari.

Kandidat eksplorasi: tren EMA21/50 dan RSI 38–62 searah candle, atau reversal
RSI <=35/>=65. Hanya candle M15 yang sudah tutup. Entry dihitung teoretis pada
open candle berikutnya; hasil diperiksa dari candle OHLC. Bila SL dan TP tersentuh
pada candle sama, dihitung loss. Posisi dengan gap data ditandai unknown.
Maksimal satu posisi per setup; riwayat disimpan terpisah di
`signals/btc_futures_trial.json`, maksimal 30 hari sejak run pertama. Hasil
`win/loss` adalah **R kotor**, bukan keuntungan akun.

Asumsi riset: spread 1 basis point, slippage 0, RR 1:2, jarak SL minimal
0,15% harga atau 1,2 ATR. **Biaya taker/maker, funding, slippage nyata,
likuidasi dan variasi spread belum dimodelkan**, sehingga performa simulasi
cenderung optimistis. Tidak ada leverage, lot, deposit, atau eksekusi otomatis.

Secara default kandidat hanya masuk log JSON, **tidak dikirim ke Discord**.
Jika ingin kartu percobaan yang jelas berlabel PAPER, set variable GitHub
`DISCORD_BTC_TRIAL_CHANNEL_ID` ke channel khusus riset. Maksimal 6 kartu/hari,
tanpa mention. `BTC_FUTURES_TRIAL_ENABLED=false` menghentikan trial. Jangan
campur statistik ini dengan BTCUSD CFD Exness atau XAUUSD; produk dan biaya
berbeda. Jangan nilai hasil sebelum ada sampel cukup dan perhitungan biaya.
