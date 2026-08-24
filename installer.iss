; OutlookAutomation Windows 安装包
;
; 编译：iscc installer.iss
; 前置：dist\OutlookAutomation\ 已由 PyInstaller 生成，且已把
;       build\Chromium 拷入 dist\OutlookAutomation\Chromium
;       （由 scripts\assemble_dist.py 完成）
;
; 关键设计：
; - 程序装到 Program Files（只读），用户数据在 %APPDATA%\OutlookAutomation
; - 卸载默认**不删**用户数据（账号、数据库、Profile），只在用户明确勾选时才删
; - 安装/升级前检查残留进程，chrome.exe 占着文件会导致复制失败

#define AppName "OutlookAutomation"
#define AppVersion GetEnv("OA_VERSION")
#if AppVersion == ""
  #define AppVersion "0.0.0-dev"
#endif
#define AppPublisher "OutlookAutomation"
#define AppExeName "OutlookAutomation.exe"
#define SourceDir "dist\OutlookAutomation"

[Setup]
AppId={{8F3A7C21-4E5B-4D9A-9C13-6A2E7B5D8F40}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename={#AppName}-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 内核体积大，装到 Program Files 需要管理员权限
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#AppExeName}
SetupLogging=yes

[Languages]
Name: "chinese"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
chinese.DataDirNote=用户数据（账号、数据库、Profile、日志）保存在 %AppData%\{#AppName}，卸载时默认保留。

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"
Name: "launchafter"; Description: "安装完成后启动"; GroupDescription: "附加任务:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\用户数据目录"; Filename: "{userappdata}\{#AppName}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "启动 {#AppName}"; Flags: nowait postinstall skipifsilent; Tasks: launchafter

[UninstallDelete]
; 只删程序目录里运行时产生的东西，不碰 %APPDATA%
Type: filesandordirs; Name: "{app}\_internal"
Type: dirifempty; Name: "{app}"

[Code]

{ ---------- 安装前：检查残留进程 ---------- }
function IsProcessRunning(const ExeName: string): Boolean;
var
  ResultCode: Integer;
begin
  { tasklist 过滤指定进程名，找到则 findstr 返回 0 }
  Result := Exec(ExpandConstant('{cmd}'),
    '/C tasklist /FI "IMAGENAME eq ' + ExeName + '" /NH | findstr /I "' + ExeName + '" >nul',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

function KillProcess(const ExeName: string): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'),
    '/C taskkill /F /IM ' + ExeName + ' /T >nul 2>nul',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function EnsureNotRunning(): Boolean;
var
  Msg: string;
begin
  Result := True;
  if IsProcessRunning('{#AppExeName}') or IsProcessRunning('chrome.exe') then
  begin
    Msg := '检测到 {#AppName} 或 Chromium 仍在运行。' + #13#10#13#10 +
           '这些进程占用着程序文件，继续安装会失败。' + #13#10 +
           '点「是」结束这些进程后继续，点「否」取消安装。' + #13#10#13#10 +
           '注意：正在执行的登录任务会被中断（已保存的断点不会丢失）。';
    if MsgBox(Msg, mbConfirmation, MB_YESNO) = IDYES then
    begin
      KillProcess('{#AppExeName}');
      KillProcess('chrome.exe');
      Sleep(1500);
      if IsProcessRunning('{#AppExeName}') then
      begin
        MsgBox('无法结束 {#AppName} 进程，请手动关闭后重试。', mbError, MB_OK);
        Result := False;
      end;
    end
    else
      Result := False;
  end;
end;

function InitializeSetup(): Boolean;
begin
  Result := EnsureNotRunning();
end;

{ ---------- 卸载：询问是否删除用户数据 ---------- }
function InitializeUninstall(): Boolean;
begin
  Result := True;
  if IsProcessRunning('{#AppExeName}') or IsProcessRunning('chrome.exe') then
  begin
    if MsgBox('{#AppName} 仍在运行，需要先结束进程才能卸载。' + #13#10#13#10 +
              '现在结束并继续卸载？', mbConfirmation, MB_YESNO) = IDYES then
    begin
      KillProcess('{#AppExeName}');
      KillProcess('chrome.exe');
      Sleep(1500);
    end
    else
      Result := False;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: string;
  Msg: string;
begin
  if CurUninstallStep = usUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\{#AppName}');
    if DirExists(DataDir) then
    begin
      Msg := '是否同时删除所有用户数据？' + #13#10#13#10 +
             DataDir + #13#10#13#10 +
             '包含：账号列表、数据库、浏览器 Profile（登录态）、日志。' + #13#10 +
             '默认保留，重新安装后可继续使用。' + #13#10#13#10 +
             '删除后无法恢复。确定要删除吗？';
      { 默认按钮是「否」，避免误删 }
      if MsgBox(Msg, mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      begin
        DelTree(DataDir, True, True, True);
      end;
    end;
  end;
end;
