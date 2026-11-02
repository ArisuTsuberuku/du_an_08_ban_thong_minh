@echo off
chcp 65001 >nul
echo ============================================================
echo   CONG CU KHOI PHUC KHAN CAP - BAN HOC THONG MINH (STEM)
echo ============================================================
echo [1/4] Dang mo khoa Task Manager goc cua Windows (DisableTaskMgr = 0)...
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Policies\System" /v DisableTaskMgr /t REG_DWORD /d 0 /f >nul 2>&1
echo [2/4] Dang tat trinh khoa may va tien trinh giam sat ngam...
taskkill /f /im SmartDeskLauncher.exe >nul 2>&1
taskkill /f /im pythonw.exe >nul 2>&1
echo [3/4] Dang khoi phuc giao dien Windows Explorer (Taskbar/Desktop)...
tasklist /FI "IMAGENAME eq explorer.exe" 2>NUL | find /I /N "explorer.exe">NUL
if "%ERRORLEVEL%"=="1" start explorer.exe
echo [4/4] HOAN TAT! May tinh da tro ve trang thai binh thuong.
timeout /t 3
