@echo off
REM Duoc bo go cai dat goi: tra Windows ve binh thuong truoc khi xoa file FocusDesk
taskkill /f /im FocusDeskLauncher.exe >nul 2>&1
taskkill /f /im SmartDeskLauncher.exe >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*he_thong_quan_ly_3_cap*' -or $_.CommandLine -like '*posture_ai_engine*' -or $_.CommandLine -like '*trang_web.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
REM Tat duong truyen Cloudflare (cloudflared tunnel) do FocusDesk mo, ke ca ban mo coi tu lan chay truoc
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='cloudflared.exe'\" | Where-Object { $_.CommandLine -like '*tunnel*127.0.0.1:*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
schtasks /delete /tn "FocusDesk" /f >nul 2>&1
schtasks /delete /tn "SmartDesk Ban Hoc Thong Minh" /f >nul 2>&1
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Policies\System" /v DisableTaskMgr /t REG_DWORD /d 0 /f >nul 2>&1
REM Go cac dong chan DNS do FocusDesk ghi vao file hosts (danh dau # FOCUSDESK-BLOCK, ban cu # SMARTDESK-BLOCK)
powershell -NoProfile -Command "$h=\"$env:SystemRoot\System32\drivers\etc\hosts\"; (Get-Content $h) | Where-Object { $_ -notmatch 'FOCUSDESK-BLOCK|SMARTDESK-BLOCK' } | Set-Content $h -Encoding ASCII" >nul 2>&1
ipconfig /flushdns >nul 2>&1
REM Hien lai taskbar neu dang bi an
taskkill /f /im explorer.exe >nul 2>&1
start explorer.exe
exit /b 0
