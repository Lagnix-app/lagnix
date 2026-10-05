; Інсталятор Lagnix. Збірка: build.ps1 (ISCC /DAppVersion=1.0.0 installer\Lagnix.iss)
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName "Lagnix"
#define AppExe "Lagnix.exe"

[Setup]
AppId={{6F1B2C7E-4D1A-4A8B-9E53-2C1D7A0B91F4}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Lagnix dev
AppCopyright=Copyright (C) 2026 Lagnix dev
VersionInfoVersion={#AppVersion}.0
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=..\assets\lagnix.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
OutputDir=..\dist
OutputBaseFilename=Lagnix-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
AppMutex=Local\Lagnix.SingleInstance
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "ukrainian"; MessagesFile: "compiler:Languages\Ukrainian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
ukrainian.InstallPawnIO=Встановити драйвер датчиків PawnIO (рекомендовано)
english.InstallPawnIO=Install the PawnIO sensor driver (recommended)
ukrainian.PawnIOHint=Потрібен для температури процесора. Це офіційний підписаний драйвер (pawnio.eu).
english.PawnIOHint=Needed for CPU temperature. This is the official signed driver (pawnio.eu).
ukrainian.RunPawnIO=Встановлення драйвера датчиків PawnIO...
english.RunPawnIO=Installing the PawnIO sensor driver...
ukrainian.RevertTweaks=Lagnix змінював налаштування Windows (твіки).%n%nПовернути їх до початкового стану перед видаленням?
english.RevertTweaks=Lagnix has changed Windows settings (tweaks).%n%nRestore them to their original state before uninstalling?
ukrainian.RevertFailed=Деякі твіки не вдалося повернути. Їх можна повернути вручну з резервних копій у %APPDATA%\Lagnix\backups.
english.RevertFailed=Some tweaks could not be restored. You can restore them manually from the backups in %APPDATA%\Lagnix\backups.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "pawnio"; Description: "{cm:InstallPawnIO}"; GroupDescription: "{cm:PawnIOHint}"; Check: not PawnIOInstalled

[Files]
Source: "..\dist\Lagnix\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "redist\PawnIO_setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Tasks: pawnio

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{tmp}\PawnIO_setup.exe"; StatusMsg: "{cm:RunPawnIO}"; Tasks: pawnio; Flags: waituntilterminated
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent runascurrentuser

[Code]
function PawnIOInstalled: Boolean;
begin
  Result := RegKeyExists(HKLM64, 'SYSTEM\CurrentControlSet\Services\PawnIO');
end;

// Видалення: драйвер PawnIO і бекапи твіків (%APPDATA%\Lagnix) не чіпаємо навмисно;
// але пропонуємо повернути зміни твіків, зроблені Lagnix.
function InitializeUninstall: Boolean;
var
  Exe: String;
  Code: Integer;
begin
  Result := True;
  Exe := ExpandConstant('{app}\{#AppExe}');
  if not FileExists(Exe) then Exit;
  if Exec(Exe, '--tweaks-pending', '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 10) then
    if MsgBox(CustomMessage('RevertTweaks'), mbConfirmation, MB_YESNO) = IDYES then
      if Exec(Exe, '--restore-tweaks', '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code <> 0) then
        MsgBox(CustomMessage('RevertFailed'), mbError, MB_OK);
end;
