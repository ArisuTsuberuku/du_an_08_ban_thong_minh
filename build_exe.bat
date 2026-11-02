@echo off
chcp 65001 >nul
echo =====================================================================
echo   DONG GOI PHAN MEM BAN HOC THONG MINH (dist\SmartDeskLauncher.exe)
echo   Che do: --onefile --noconsole --uac-admin (Quyen Administrator)
echo =====================================================================

python -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --noconsole ^
    --uac-admin ^
    --name SmartDeskLauncher ^
    --collect-data uiautomation ^
    --hidden-import=core.config_manager ^
    --hidden-import=core.failsafe ^
    --hidden-import=services.giam_sat_worker ^
    --hidden-import=trang_web ^
    --hidden-import=win32gui ^
    --hidden-import=win32process ^
    --hidden-import=uiautomation ^
    --hidden-import=comtypes ^
    --hidden-import=comtypes.client ^
    --hidden-import=comtypes.gen ^
    --exclude-module=numpy ^
    --exclude-module=scipy ^
    --exclude-module=pandas ^
    --exclude-module=pyarrow ^
    --exclude-module=onnxruntime ^
    --exclude-module=chromadb ^
    --exclude-module=lxml ^
    --exclude-module=openpyxl ^
    --exclude-module=pymupdf ^
    --exclude-module=pdfplumber ^
    --exclude-module=grpc ^
    --exclude-module=cryptography ^
    he_thong_quan_ly_3_cap.py

if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Dong goi PyInstaller that bai!
    exit /b %ERRORLEVEL%
)

echo.
echo [COPY] Sao chep shared_config.json va KHOI_PHUC_KHAN_CAP.bat vao thu muc dist\...
if exist shared_config.json copy /Y shared_config.json dist\shared_config.json >nul
if exist KHOI_PHUC_KHAN_CAP.bat copy /Y KHOI_PHUC_KHAN_CAP.bat dist\KHOI_PHUC_KHAN_CAP.bat >nul

echo [SUCCESS] Hoan tat! File chay nam tai: dist\SmartDeskLauncher.exe
