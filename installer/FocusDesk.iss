; =====================================================================
;  BỘ CÀI MỘT FILE - BÀN HỌC THÔNG MINH (FocusDesk_Setup.exe)
;  Biên dịch: chạy TAO_BO_CAI.bat ở thư mục gốc dự án (cần Inno Setup 6).
;  Gửi DUY NHẤT file dist_installer\FocusDesk_Setup.exe sang máy khác -> nháy đúp -> Next -> xong.
;  Bộ cài: chép mã nguồn + mô hình AI vào C:\FocusDesk, ghi địa chỉ web phụ huynh, cài Python (nếu kèm),
;  cài thư viện (CAI_DAT.bat /tu_bo_cai), tạo lối tắt, tùy chọn tự khởi động + cloudflared.
;  Gỡ cài đặt: trả lại Task Manager, gỡ chặn DNS, xóa tác vụ tự khởi động, hiện lại taskbar.
; =====================================================================
#define AppName "FocusDesk"
#define AppVer "1.20.0"

[Setup]
AppId={{6F0B3A52-8C1E-4C5D-9E0A-1D08B4A70001}
AppName={#AppName}
AppVersion={#AppVer}
AppPublisher=Dự án D08 - FocusDesk
DefaultDirName={sd}\FocusDesk
DefaultGroupName={#AppName}
PrivilegesRequired=admin
OutputDir=..\dist_installer
OutputBaseFilename=FocusDesk_Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\assets\focusdesk.ico
SetupIconFile=..\assets\focusdesk.ico
DisableProgramGroupPage=yes
#ifdef SIGN
; Bat khi TAO_BO_CAI.bat goi ISCC voi /DSIGN /Sfdsign=... (xem bien FOCUSDESK_SIGN_SHA1)
SignTool=fdsign
SignedUninstaller=yes
#endif

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Tạo lối tắt trên Desktop"
Name: "autostart"; Description: "Tự mở FocusDesk mỗi khi đăng nhập Windows (khóa máy ngay khi bật)"; Flags: unchecked
Name: "cloudflared"; Description: "Cài cloudflared (phụ huynh xem camera từ xa ngoài mạng nhà)"; Flags: unchecked

[Dirs]
Name: "{app}"; Permissions: users-modify
Name: "{app}\logs"; Permissions: users-modify

[Files]
Source: "..\*.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\core\*.py"; DestDir: "{app}\core"; Flags: ignoreversion
Source: "..\services\*.py"; DestDir: "{app}\services"; Flags: ignoreversion
Source: "..\models\*"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "..\assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion
Source: "..\firmware\*"; DestDir: "{app}\firmware"; Flags: ignoreversion recursesubdirs
Source: "..\tests\*.py"; DestDir: "{app}\tests"; Flags: ignoreversion
Source: "..\CAI_DAT.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\MO_FOCUSDESK.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\KHOI_PHUC_KHAN_CAP.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\secrets.local.example.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\HUONG_DAN_CAI_DAT.md"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\DANH_MUC_FILE.md"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "KIEM_TRA.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "go_cai_dat.bat"; DestDir: "{app}"; Flags: ignoreversion
; Tùy chọn: TAO_BO_CAI.bat có thể tải sẵn bộ cài Python vào installer\redist\ để cài được cả khi máy đích chưa có Python
Source: "redist\python-installer.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall skipifsourcedoesntexist

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\MO_FOCUSDESK.bat"; WorkingDir: "{app}"; IconFilename: "{app}\assets\focusdesk.ico"
Name: "{group}\Kiểm tra cài đặt"; Filename: "{app}\KIEM_TRA.bat"; WorkingDir: "{app}"
Name: "{group}\Sửa lỗi cài đặt"; Filename: "{app}\KIEM_TRA.bat"; Parameters: "--sua"; WorkingDir: "{app}"; Comment: "Cài lại thư viện bị thiếu hoặc hỏng (OpenCV, pyserial...) và tải mô hình AI còn thiếu"
Name: "{group}\Khôi phục khẩn cấp"; Filename: "{app}\KHOI_PHUC_KHAN_CAP.bat"; WorkingDir: "{app}"
Name: "{group}\Gỡ cài đặt {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\MO_FOCUSDESK.bat"; WorkingDir: "{app}"; IconFilename: "{app}\assets\focusdesk.ico"; Tasks: desktopicon

[Run]
Filename: "{tmp}\python-installer.exe"; Parameters: "/quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_launcher=1"; StatusMsg: "Đang cài Python..."; Check: NeedPython and HasBundledPython; Flags: waituntilterminated
Filename: "{app}\CAI_DAT.bat"; Parameters: "/tu_bo_cai"; WorkingDir: "{app}"; StatusMsg: "Đang cài thư viện Python và mô hình AI (lần đầu 3-10 phút, cần Internet)..."; Flags: waituntilterminated
Filename: "{sys}\schtasks.exe"; Parameters: "/delete /tn ""SmartDesk Ban Hoc Thong Minh"" /f"; Flags: runhidden waituntilterminated
Filename: "{sys}\schtasks.exe"; Parameters: "/create /tn ""FocusDesk"" /tr ""\""{app}\MO_FOCUSDESK.bat\"""" /sc onlogon /rl highest /f"; Tasks: autostart; Flags: runhidden waituntilterminated
Filename: "{cmd}"; Parameters: "/c winget install --id Cloudflare.cloudflared -e --accept-source-agreements --accept-package-agreements"; Tasks: cloudflared; StatusMsg: "Đang cài cloudflared..."; Flags: runhidden waituntilterminated
Filename: "{app}\MO_FOCUSDESK.bat"; Description: "Mở {#AppName} ngay"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent shellexec unchecked

[UninstallRun]
Filename: "{app}\go_cai_dat.bat"; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; RunOnceId: "FocusDeskCleanup"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
var
  CloudPage: TInputQueryWizardPage;

function PythonOnPath(): Boolean;
begin
  Result := FileSearch('python.exe', GetEnv('PATH')) <> '';
end;

function NeedPython(): Boolean;
begin
  Result := not (FileExists(ExpandConstant('{localappdata}\Python\pythoncore-3.14-64\python.exe'))
    or PythonOnPath()
    or RegKeyExists(HKCU, 'Software\Python\PythonCore')
    or RegKeyExists(HKLM, 'Software\Python\PythonCore'));
end;

function HasBundledPython(): Boolean;
begin
  Result := FileExists(ExpandConstant('{tmp}\python-installer.exe'));
end;

procedure InitializeWizard;
begin
  CloudPage := CreateInputQueryPage(wpSelectTasks, 'Kết nối web phụ huynh',
    'Máy này gửi báo cáo tới web phụ huynh ở địa chỉ dưới đây.',
    'Giữ nguyên nếu dùng web chung của dự án. Sau khi cài: mở app, vào Phụ huynh -> Điện thoại phụ huynh để lấy mã ghép 6 số.');
  CloudPage.Add('Địa chỉ web phụ huynh:', False);
  CloudPage.Values[0] := 'https://banhocthongminh.vercel.app';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  F: String;
begin
  if CurStep = ssInstall then
  begin
    ForceDirectories(ExpandConstant('{app}'));
    F := ExpandConstant('{app}\secrets.local.json');
    if not FileExists(F) then
      SaveStringToFile(F, '{"cloud_url": "' + Trim(CloudPage.Values[0]) + '"}', False);
  end;
  // Ghi nhớ cloudflared do CHÍNH bộ cài này cài -> lúc gỡ FocusDesk hỏi gỡ luôn
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('cloudflared') then
    SaveStringToFile(ExpandConstant('{app}\cloudflared_by_focusdesk.txt'), 'winget Cloudflare.cloudflared', False);
end;

var
  CfByUs: Boolean;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  RC: Integer;
begin
  // Đọc dấu TRƯỚC khi thư mục {app} bị xóa
  if CurUninstallStep = usUninstall then
    CfByUs := FileExists(ExpandConstant('{app}\cloudflared_by_focusdesk.txt'));
  // go_cai_dat.bat (UninstallRun) đã tắt tiến trình cloudflared; ở đây hỏi gỡ hẳn chương trình cloudflared
  if (CurUninstallStep = usPostUninstall) and CfByUs then
    if UninstallSilent or (MsgBox('FocusDesk đã cài kèm cloudflared (đường truyền xem camera từ xa).' + #13#10 +
        'Gỡ luôn cloudflared khỏi máy?', mbConfirmation, MB_YESNO) = IDYES) then
      Exec(ExpandConstant('{cmd}'), '/c winget uninstall --id Cloudflare.cloudflared -e --silent --accept-source-agreements',
           '', SW_HIDE, ewWaitUntilTerminated, RC);
end;
