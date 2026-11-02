@echo off
chcp 65001 >nul
title Tao bo cai SmartDesk_Setup.exe
cd /d "%~dp0"
echo ==============================================================
echo   TAO BO CAI MOT FILE: dist_installer\SmartDesk_Setup.exe
echo ==============================================================
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo Chua co Inno Setup 6 (cong cu dong goi bo cai, mien phi).
  choice /c YN /m "Cai Inno Setup bang winget ngay bay gio"
  if errorlevel 2 exit /b 1
  winget install --id JRSoftware.InnoSetup -e --accept-source-agreements --accept-package-agreements
  set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
  if not exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
)
if not exist installer\redist\python-installer.exe (
  choice /c YN /m "Kem bo cai Python 3.14 vao file (them ~28MB, cai duoc ca khi may dich chua co Python)"
  if not errorlevel 2 (
    if not exist installer\redist mkdir installer\redist
    powershell -NoProfile -Command "Invoke-WebRequest https://www.python.org/ftp/python/3.14.0/python-3.14.0-amd64.exe -OutFile installer\redist\python-installer.exe -UseBasicParsing"
  )
)
"%ISCC%" installer\SmartDesk.iss
if errorlevel 1 (
  echo [LOI] Dong goi that bai - xem thong bao phia tren.
  pause
  exit /b 1
)
echo.
echo XONG: gui file dist_installer\SmartDesk_Setup.exe sang may khac, nhay dup de cai.
explorer dist_installer
pause
