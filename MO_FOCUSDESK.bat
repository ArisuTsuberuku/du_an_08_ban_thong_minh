@echo off
chcp 65001 >nul
title FocusDesk - Ban hoc thong minh (app 3 cap)
REM ============================================================
REM  Nhap dup file nay de mo app quan ly 3 cap (he_thong_quan_ly_3_cap.py)
REM  - Tu xin quyen Administrator (can de khoa Task Manager + chan web qua file hosts)
REM  - Thoat khan cap: phim cuu ho do Admin dat (Admin > Cuu ho) + PIN, file UNLOCK.txt, hoac KHOI_PHUC_KHAN_CAP.bat
REM ============================================================
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Dang xin quyen Administrator...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
cd /d "%~dp0"
set "PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not exist "%PY%" if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" he_thong_quan_ly_3_cap.py
if %errorlevel% neq 0 pause
