@echo off
setlocal

cd /d "%~dp0"
python "%~dp0download_ipfs_from_csv.py" "%~dp0mini_JailBreakV_28K_cids.csv"

echo.
pause
