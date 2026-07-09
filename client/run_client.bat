@echo off
title OLP AI Monitor Client
echo ==================================================
echo DANG KHOI DONG CLIENT GIAM SAT HIEU SUAT OLP AI...
echo ==================================================
echo.
echo [Step 1/2] Dang kiem tra va cai dat thu vien Python can thiet...
pip install -r requirements.txt
echo.
echo [Step 2/2] Dang khoi chay Script theo doi...
python client.py
pause
