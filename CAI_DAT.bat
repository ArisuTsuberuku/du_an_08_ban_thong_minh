@echo off
chcp 65001 >nul
title Cai dat FocusDesk - Ban hoc thong minh
cd /d "%~dp0"
set "SILENT="
if /i "%~1"=="/tu_bo_cai" set "SILENT=1"
echo ==============================================================
echo   CAI DAT FOCUSDESK - chay 1 lan tren may hoc sinh
echo ==============================================================
echo.

REM ---- 1. Tim Python 3.10+ 64-bit -------------------------------------------
set "PY="
if exist "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" set "PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not defined PY for /f "delims=" %%p in ('where python 2^>nul ^| findstr /v /i "WindowsApps"') do if not defined PY set "PY=%%p"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if defined PY goto :havepy
echo [1/5] KHONG tim thay Python.
if defined SILENT goto :wingetpy
choice /c YN /m "Cai Python 3.14 bang winget ngay bay gio"
if errorlevel 2 goto :nopython
:wingetpy
winget install --id Python.PythonInstallManager -e --accept-source-agreements --accept-package-agreements
py install 3.14
if exist "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" set "PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
:havepy
if not defined PY goto :nopython
echo [1/5] Python: %PY%

REM ---- 2. Cai thu vien --------------------------------------------------------
echo [2/5] Dang cai thu vien (lan dau mat 3-10 phut)...
"%PY%" -m pip install --disable-pip-version-check --upgrade pip >nul
"%PY%" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 echo [CANH BAO] Mot so goi cai loi - xem bang kiem tra ben duoi.

REM ---- 3. Kiem tra + tai mo hinh AI con thieu ----------------------------------
echo [3/5] Kiem tra cai dat va tai mo hinh AI con thieu...
"%PY%" kiem_tra_cai_dat.py --sua

REM Bo cai Inno Setup da tu tao loi tat / tac vu -> dung o day
if defined SILENT (
  echo HOAN TAT phan thu vien. Cua so nay tu dong sau 5 giay...
  timeout /t 5 >nul
  exit /b 0
)

REM ---- 4. Loi tat tren Desktop -------------------------------------------------
echo [4/5] Tao loi tat "FocusDesk" tren Desktop...
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\FocusDesk.lnk');$s.TargetPath='%~dp0MO_FOCUSDESK.bat';$s.WorkingDirectory='%~dp0';$s.IconLocation='%SystemRoot%\System32\imageres.dll,109';$s.Save()"

REM ---- 5. Tuy chon -------------------------------------------------------------
echo [5/5] Tuy chon:
choice /c YN /m "Tu mo FocusDesk khi dang nhap Windows (quyen Admin, khong hoi UAC)"
if errorlevel 2 goto :skipauto
schtasks /delete /tn "SmartDesk Ban Hoc Thong Minh" /f >nul 2>&1
schtasks /create /tn "FocusDesk" /tr "\"%~dp0MO_FOCUSDESK.bat\"" /sc onlogon /rl highest /f
:skipauto
where cloudflared >nul 2>&1
if errorlevel 1 (
  choice /c YN /m "Cai cloudflared de phu huynh xem camera tu xa ngoai mang nha"
  if not errorlevel 2 winget install --id Cloudflare.cloudflared -e --accept-source-agreements --accept-package-agreements
)
echo.
echo HOAN TAT. Mo "FocusDesk" tren Desktop.
echo Lan dau: dang nhap Admin -^> doi mat khau; Phu huynh -^> Camera AI -^> "Tu nhan dien camera";
echo          Phu huynh -^> Dien thoai phu huynh -^> lay ma ghep 6 so nhap tren web phu huynh.
pause
exit /b 0

:nopython
if defined SILENT exit /b 2
echo Chua co Python. Tai tai https://www.python.org/downloads/ (chon "Add python.exe to PATH") roi chay lai file nay.
pause
exit /b 1
