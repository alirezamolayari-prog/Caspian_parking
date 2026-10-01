; Inno Setup script (SPEC §7). Built by scripts\build.ps1, which passes the names and version:
;   ISCC.exe /DAppName=... /DAppFolder=... /DAppVersion=... /DRoot=... packaging\installer.iss
; The data root (default C:\<AppFolder>) is outside {app} and is never removed by the uninstaller.

#ifndef AppName
  #error AppName must be passed by scripts\build.ps1
#endif

[Setup]
AppId={{568F1088-BF12-4480-ADC3-8945FAA1C6D3}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
DefaultDirName={autopf}\{#AppFolder}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir={#Root}\dist\installer
OutputBaseFilename={#AppFolder}-Setup-{#AppVersion}
SetupIconFile={#Root}\build\app.ico
UninstallDisplayIcon={app}\parking.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
; /CURRENTUSER = per-user install without the service (used by the build's install test)
PrivilegesRequiredOverridesAllowed=commandline
CloseApplications=yes
RestartApplications=no
ShowLanguageDialog=auto

[Languages]
Name: "farsi"; MessagesFile: "{#Root}\packaging\Farsi.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
farsi.DesktopIcon=میانبر روی دسکتاپ
english.DesktopIcon=Desktop shortcut
farsi.AutoStart=اجرای خودکار برنامه هنگام روشن شدن ویندوز
english.AutoStart=Start the app when Windows starts
farsi.ServerService=نصب سرویس سرور (فقط روی رایانه سرور)
english.ServerService=Install the server service (server PC only)
farsi.Launch=اجرای برنامه
english.Launch=Start the app
farsi.NoOdbc=درایور «ODBC Driver 18 for SQL Server» نصب نیست. برای نقش «درب» و «سرور» لازم است؛ حالت تک‌دستگاهی بدون آن کار می‌کند. آن را از سایت مایکروسافت نصب کنید.
english.NoOdbc=“ODBC Driver 18 for SQL Server” is not installed. Gate and server PCs need it; standalone mode works without it. Install it from Microsoft.
farsi.NoVcRuntime=بسته Visual C++ 2015-2022 (x64) روی این رایانه نیست. فایل‌های لازم همراه برنامه نصب می‌شوند، اما برای درایور ODBC نصب آن توصیه می‌شود.
english.NoVcRuntime=Visual C++ 2015-2022 (x64) is not installed. The app carries its own copy, but the ODBC driver needs it.

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"
Name: "autostart"; Description: "{cm:AutoStart}"
Name: "service"; Description: "{cm:ServerService}"; Flags: unchecked

[Dirs]
; the first-run wizard writes the data root location here (as a normal user)
Name: "{commonappdata}\{#AppFolder}"; Permissions: users-modify; Check: IsAdminInstallMode

[Files]
Source: "{#Root}\dist\parking\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\parking.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\parking.exe"; Tasks: desktopicon
Name: "{commonstartup}\{#AppName}"; Filename: "{app}\parking.exe"; Tasks: autostart

[Run]
Filename: "{app}\parking.exe"; Parameters: "--service install"; Tasks: service; Flags: runhidden waituntilterminated
Filename: "{app}\parking.exe"; Parameters: "--service start"; Tasks: service; Flags: runhidden waituntilterminated
Filename: "{app}\parking.exe"; Description: "{cm:Launch}"; Flags: nowait postinstall skipifsilent
; silent auto-update (SPEC §7): start the app again for the logged-in user
Filename: "{app}\parking.exe"; Check: RelaunchAfterSilentUpdate; Flags: nowait runasoriginaluser

[UninstallRun]
Filename: "{app}\parking.exe"; Parameters: "--service stop"; Flags: runhidden; RunOnceId: "StopService"
Filename: "{app}\parking.exe"; Parameters: "--service remove"; Flags: runhidden; RunOnceId: "RemoveService"

[Code]
function RelaunchAfterSilentUpdate(): Boolean;
begin
  { /NOLAUNCH=1 is used by the automated install test }
  Result := WizardSilent and (ExpandConstant('{param:NOLAUNCH|0}') <> '1');
end;

function OdbcInstalled(): Boolean;
begin
  Result := RegKeyExists(HKLM, 'SOFTWARE\ODBC\ODBCINST.INI\ODBC Driver 18 for SQL Server');
end;

function VcRuntimeInstalled(): Boolean;
var
  Installed: Cardinal;
begin
  Result := RegQueryDWordValue(HKLM64, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', Installed)
    and (Installed = 1);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and not WizardSilent then
  begin
    if not OdbcInstalled() then
      MsgBox(CustomMessage('NoOdbc'), mbInformation, MB_OK);
    if not VcRuntimeInstalled() then
      MsgBox(CustomMessage('NoVcRuntime'), mbInformation, MB_OK);
  end;
end;
