@echo off
title OLP AI Monitor - PARTNER
echo ==================================================
echo OLP AI - CLIENT GIAM SAT CHO HUNG
echo ==================================================
echo.
echo Server: http://147.182.134.43:8000
echo.
echo [Step 1/2] Dang cai dat thu vien...
pip install -r requirements.txt
echo.
echo [Step 2/2] Khoi dong client...
python client.py
pause
