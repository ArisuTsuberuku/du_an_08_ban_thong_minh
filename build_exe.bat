@echo off
chcp 65001 >nul
echo =====================================================================
echo   DONG GOI PHAN MEM FOCUSDESK (dist\FocusDeskLauncher.exe)
echo   Che do: --onefile --noconsole --uac-admin (Quyen Administrator)
echo   Camera AI (mediapipe) chay rieng bang Python he thong -> chay CAI_DAT.bat truoc
echo =====================================================================

python -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --noconsole ^
    --uac-admin ^
    --name FocusDeskLauncher ^
    --workpath "%TEMP%\FocusDeskLauncher_pyi_build" ^
    --distpath "dist" ^
    --collect-data uiautomation ^
    --hidden-import=core.config_manager ^
    --hidden-import=core.failsafe ^
    --hidden-import=services.giam_sat_worker ^
    --hidden-import=services.posture_controller ^
    --hidden-import=services.telegram_service ^
    --hidden-import=services.cloud_agent ^
    --hidden-import=services.activity_tracker ^
    --hidden-import=services.kiosk_guard ^
    --hidden-import=services.camera_setup ^
    --hidden-import=services.camera_selector ^
    --hidden-import=services.app_scanner ^
    --hidden-import=services.app_icons ^
    --hidden-import=services.live_view ^
    --hidden-import=services.hosts_blocker ^
    --hidden-import=core.secrets_store ^
    --hidden-import=win32api ^
    --hidden-import=win32ui ^
    --hidden-import=win32com.client ^
    --hidden-import=PIL.ImageTk ^
    --hidden-import=telegram_service ^
    --hidden-import=trang_web ^
    --hidden-import=win32gui ^
    --hidden-import=win32process ^
    --hidden-import=uiautomation ^
    --hidden-import=comtypes ^
    --hidden-import=comtypes.client ^
    --hidden-import=comtypes.gen ^
    --exclude-module=mediapipe ^
    --exclude-module=matplotlib ^
    --exclude-module=sounddevice ^
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
echo [COPY] Sao chep cau hinh, file cuu ho va mo hinh AI vao thu muc dist\...
if exist models xcopy /E /I /Y models dist\models >nul
if exist shared_config.json copy /Y shared_config.json dist\shared_config.json >nul
if exist KHOI_PHUC_KHAN_CAP.bat copy /Y KHOI_PHUC_KHAN_CAP.bat dist\KHOI_PHUC_KHAN_CAP.bat >nul

echo [SUCCESS] Hoan tat! File chay nam tai: dist\FocusDeskLauncher.exe
echo [LUU Y] Camera AI chay bang Python cua may (posture_ai_engine.py o thu muc goc) - may chay exe van can CAI_DAT.bat.
