@echo off
REM ============================================================
REM  Jalankan data_service lokal untuk tes DEMO MT5.
REM  Double-click file ini. Biarkan jendela terbuka selama tes.
REM  EA cukup panggil http://127.0.0.1:8000
REM  Tekan Ctrl+C atau tutup jendela untuk mematikan.
REM ============================================================
cd /d "%~dp0data_service"
echo Memulai data_service di http://127.0.0.1:8000 ...
echo (biarkan jendela ini terbuka selama MT5 demo berjalan)
python -m uvicorn main:app --host 127.0.0.1 --port 8000
pause
