@echo off
chcp 65001 >nul
title Kiem tra cai dat SmartDesk
cd /d "%~dp0"
set "PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not exist "%PY%" set "PY=python"
set PYTHONIOENCODING=utf-8
"%PY%" kiem_tra_cai_dat.py %*
pause
