@echo off
chcp 65001 >nul
echo ============================================================
echo   CONG CU KHOI PHUC KHAN CAP - BAN HOC THONG MINH (STEM)
echo ============================================================
echo [1/4] Dang mo khoa Task Manager goc cua Windows (DisableTaskMgr = 0)...
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Policies\System" /v DisableTaskMgr /t REG_DWORD /d 0 /f >nul 2>&1
echo [2/4] Dang tat trinh khoa may va tien trinh giam sat ngam...
taskkill /f /im FocusDeskLauncher.exe >nul 2>&1
taskkill /f /im pythonw.exe >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*he_thong_quan_ly_3_cap*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
echo [3/4] Dang khoi dong lai Windows Explorer (hien lai Taskbar/Desktop)...
taskkill /f /im explorer.exe >nul 2>&1
start explorer.exe
echo [4/4] HOAN TAT! May tinh da tro ve trang thai binh thuong.
timeout /t 3
