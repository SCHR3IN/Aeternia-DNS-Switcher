; Установщик Aeternia DNS Switcher для Windows 10/11 (собирается в GitHub Actions).
#define AppVersion GetEnv("APP_VERSION")

[Setup]
AppId={{6B3E2C8E-7E9A-4D7B-9E3B-AE7E2D0D5A11}
AppName=Aeternia DNS Switcher
AppVersion={#AppVersion}
AppPublisher=SCHR3IN
AppPublisherURL=https://github.com/SCHR3IN/Aeternia-DNS-Switcher
DefaultDirName={autopf}\Aeternia DNS
DefaultGroupName=Aeternia DNS
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist
OutputBaseFilename=AeterniaDNS-Setup
SetupIconFile=..\..\build\icon.ico
UninstallDisplayIcon={app}\AeterniaDNS.exe
UninstallDisplayName=Aeternia DNS Switcher
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\..\dist\AeterniaDNS\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\build\dnscrypt\dnscrypt-proxy.exe"; DestDir: "{app}\dnscrypt-proxy"; Flags: ignoreversion
Source: "..\..\build\dnscrypt\LICENSE"; DestDir: "{app}\dnscrypt-proxy"; Flags: ignoreversion

[Icons]
Name: "{group}\Aeternia DNS"; Filename: "{app}\AeterniaDNS.exe"
Name: "{group}\Удалить Aeternia DNS"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Aeternia DNS"; Filename: "{app}\AeterniaDNS.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AeterniaDNS.exe"; Description: "Запустить Aeternia DNS"; Flags: postinstall nowait skipifsilent shellexec

[UninstallRun]
; Сначала вернуть исходные DNS и удалить службу, потом удалять файлы.
Filename: "{app}\AeterniaDNS.exe"; Parameters: "--uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "RestoreDns"

[Code]
// Обновление поверх старой версии: служба держит dnscrypt-proxy.exe открытым.
// Старая версия возвращает DNS и снимает службу, после установки сервер выбирается заново.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  if FileExists(ExpandConstant('{app}\AeterniaDNS.exe')) then
    Exec(ExpandConstant('{app}\AeterniaDNS.exe'), '--uninstall', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := '';
end;
