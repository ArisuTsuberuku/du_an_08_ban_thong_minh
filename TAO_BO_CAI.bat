@echo off
chcp 65001 >nul
title Tao bo cai FocusDesk_Setup.exe
cd /d "%~dp0"
echo ==============================================================
echo   TAO BO CAI MOT FILE: dist_installer\FocusDesk_Setup.exe
echo   Chi dong goi file - KHONG chay FocusDesk, KHONG khoa may.
echo ==============================================================
echo.

REM ---- 1. Tim Inno Setup 6 (khong dung khoi if (...) de tranh loi dau ngoac) ----
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if defined ISCC goto :co_iscc

echo Chua co Inno Setup 6 - cong cu dong goi bo cai, mien phi.
choice /c YN /m "Cai Inno Setup bang winget ngay bay gio"
if errorlevel 2 goto :huy
winget install --id JRSoftware.InnoSetup -e --accept-source-agreements --accept-package-agreements
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC goto :khong_iscc

:co_iscc
echo [1/3] Inno Setup: %ISCC%

REM ---- 2. Tuy chon kem bo cai Python ----------------------------------------
if exist "installer\redist\python-installer.exe" goto :dong_goi
choice /c YN /m "[2/3] Kem bo cai Python 3.14 vao file - them khoang 28MB, cai duoc ca khi may dich chua co Python"
if errorlevel 2 goto :dong_goi
if not exist "installer\redist" mkdir "installer\redist"
echo Dang tai python-3.14.0-amd64.exe ...
powershell -NoProfile -Command "Invoke-WebRequest 'https://www.python.org/ftp/python/3.14.0/python-3.14.0-amd64.exe' -OutFile 'installer\redist\python-installer.exe' -UseBasicParsing"
if not exist "installer\redist\python-installer.exe" echo [CANH BAO] Tai Python that bai - bo cai se dung winget tren may dich.

REM ---- 3. Dong goi ------------------------------------------------------------
:dong_goi
echo [3/3] Dang dong goi, may co the cham 1-3 phut...
"%ISCC%" "installer\FocusDesk.iss"
if errorlevel 1 goto :loi_dong_goi
echo.
echo XONG: gui file dist_installer\FocusDesk_Setup.exe sang may khac, nhay dup de cai.
explorer "dist_installer"
pause
exit /b 0

:loi_dong_goi
echo [LOI] Dong goi that bai - xem thong bao phia tren.
pause
exit /b 1

:khong_iscc
echo [LOI] Khong tim thay ISCC.exe sau khi cai. Cai tay tai https://jrsoftware.org/isdl.php roi chay lai file nay.
pause
exit /b 1

:huy
echo Da huy. Chua tao bo cai.
pause
exit /b 0
