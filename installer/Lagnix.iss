; Інсталятор Lagnix. Збірка: build.ps1 (ISCC /DAppVersion=0.9.1 installer\Lagnix.iss)
; Картинки майстра: python tools/gen_installer_images.py (installer\img\*.bmp).
; Тексти робота — installer\strings.inc (12 мов); Inno-мова лише для стандартних кнопок.
#ifndef AppVersion
  #define AppVersion "0.9.1"
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
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}.0
VersionInfoCompany=Lagnix dev
VersionInfoDescription=Lagnix Setup
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
; Темна тема як у програмі; картинки — набори під 100-250% масштабу (Inno вибирає за DPI)
WizardStyle=modern dark
WizardImageBackColor=$21130D
WizardImageFile=img\welcome-164.bmp,img\welcome-192.bmp,img\welcome-246.bmp,img\welcome-273.bmp,img\welcome-328.bmp,img\welcome-355.bmp,img\welcome-410.bmp
WizardSmallImageFile=img\small-55.bmp,img\small-64.bmp,img\small-83.bmp,img\small-92.bmp,img\small-110.bmp,img\small-119.bmp,img\small-138.bmp
DisableWelcomePage=no
DisableDirPage=no
; Мову питаємо завжди; типова — мова Windows
ShowLanguageDialog=yes
UsePreviousLanguage=no
LanguageDetectionMethod=uilanguage
AppMutex=Local\Lagnix.SingleInstance
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "ukrainian"; MessagesFile: "compiler:Languages\Ukrainian.isl"
Name: "polish"; MessagesFile: "compiler:Languages\Polish.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "chinesesimplified"; MessagesFile: "lang\ChineseSimplified.isl"
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Files]
Source: "..\dist\Lagnix\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; сумний робот для вікна видалення (деінсталятор бере його з теки програми)
Source: "img\sad-*.bmp"; DestDir: "{app}\unins-img"; Flags: ignoreversion
; картинки, що міняються під час встановлення (читаються з розпакованих тимчасових файлів)
Source: "img\finish-*.bmp"; Flags: dontcopy
Source: "img\installing-*.bmp"; Flags: dontcopy
; PawnIO — лише якщо користувач сам поставив галочку на сторінці вибору
Source: "redist\PawnIO_setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: WantPawnIO

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Check: WantDesktop

[Registry]
; мова встановлення — запасний варіант мови деінсталятора
Root: HKLM; Subkey: "Software\{#AppName}"; ValueType: string; ValueName: "InstallLanguage"; ValueData: "{code:CurLang}"; Flags: uninsdeletekey

[Run]
Filename: "{tmp}\PawnIO_setup.exe"; StatusMsg: "{code:CodeS|inst_run_pawnio}"; Flags: waituntilterminated; Check: WantPawnIO
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent runascurrentuser

[Code]
var
  Tbl: TStringList;
  Lang: String;
  OptPage: TWizardPage;
  ChkDesktop, ChkPawn: TNewCheckBox;
  InstImage: TBitmapImage;
  { деінсталятор }
  UnState: Integer;
  UnRevert, UnWipe: Boolean;

procedure A(const L, K, V: String);
begin
  Tbl.Add(L + '.' + K + '=' + V);
end;

#include "strings.inc"

{ TStringList.Values у Pascal Script недоступний — шукаємо рядок «ключ=текст» самі. }
function Lookup(const Key: String): String;
var
  I, N: Integer;
begin
  Result := '';
  N := Length(Key) + 1;
  for I := 0 to Tbl.Count - 1 do
    if Copy(Tbl[I], 1, N) = Key + '=' then
    begin
      Result := Copy(Tbl[I], N + 1, MaxInt);
      Exit;
    end;
end;

function S(const K: String): String;
begin
  Result := Lookup(Lang + '.' + K);
  if Result = '' then Result := Lookup('en.' + K);
  StringChangeEx(Result, '##', #13#10, True);
end;

function CodeS(Param: String): String;
begin
  Result := S(Param);
end;

function CurLang(Param: String): String;
begin
  Result := Lang;
end;

function LangFromInno(const Name: String): String;
begin
  if Name = 'ukrainian' then Result := 'uk'
  else if Name = 'polish' then Result := 'pl'
  else if Name = 'german' then Result := 'de'
  else if Name = 'spanish' then Result := 'es'
  else if Name = 'brazilianportuguese' then Result := 'pt-BR'
  else if Name = 'french' then Result := 'fr'
  else if Name = 'turkish' then Result := 'tr'
  else if Name = 'russian' then Result := 'ru'
  else if Name = 'chinesesimplified' then Result := 'zh-CN'
  else if Name = 'japanese' then Result := 'ja'
  else if Name = 'korean' then Result := 'ko'
  else Result := 'en';
end;

function KnownLang(const Code: String): Boolean;
begin
  Result := Lookup(Code + '.welcome_title') <> '';
end;

function WindowsLang: String;
begin
  case GetUILanguage and $3FF of
    $22: Result := 'uk';
    $15: Result := 'pl';
    $07: Result := 'de';
    $0A: Result := 'es';
    $16: Result := 'pt-BR';
    $0C: Result := 'fr';
    $1F: Result := 'tr';
    $19: Result := 'ru';
    $04: Result := 'zh-CN';
    $11: Result := 'ja';
    $12: Result := 'ko';
  else
    Result := 'en';
  end;
end;

function SettingsDir: String;
begin
  Result := ExpandConstant('{userappdata}\{#AppName}');
end;

function PawnIOInstalled: Boolean;
begin
  Result := RegKeyExists(HKLM64, 'SYSTEM\CurrentControlSet\Services\PawnIO');
end;

function WantPawnIO: Boolean;
begin
  Result := (ChkPawn <> nil) and ChkPawn.Checked and not PawnIOInstalled;
end;

function WantDesktop: Boolean;
begin
  Result := (ChkDesktop <> nil) and ChkDesktop.Checked;
end;

{ Найменший зі наборів картинок (за шириною у пікселях), що не менший за потрібний. }
function PickSize(const Sizes: array of Integer; Need: Integer): Integer;
var
  I: Integer;
begin
  Result := Sizes[High(Sizes)];
  for I := Low(Sizes) to High(Sizes) do
    if Sizes[I] >= Need then
    begin
      Result := Sizes[I];
      Exit;
    end;
end;

function FinishFile: String;
begin
  Result := 'finish-' + IntToStr(PickSize([164, 192, 246, 273, 328, 355, 410], ScaleX(164))) + '.bmp';
  ExtractTemporaryFile(Result);
  Result := ExpandConstant('{tmp}\' + Result);
end;

{ ------------------------------------------------------------------ встановлення }

procedure InitializeWizard;
var
  Hint: TNewStaticText;
begin
  Tbl := TStringList.Create;
  LoadStrings;
  Lang := LangFromInno(ActiveLanguage);

  WizardForm.WelcomeLabel1.Caption := S('welcome_title');
  WizardForm.WelcomeLabel2.Caption := S('welcome_text');

  PageFromID(wpSelectDir).Caption := S('dir_title');
  PageFromID(wpSelectDir).Description := S('dir_desc');
  PageFromID(wpReady).Caption := S('ready_title');
  PageFromID(wpReady).Description := S('ready_desc');
  PageFromID(wpInstalling).Caption := S('inst_title');
  PageFromID(wpInstalling).Description := S('inst_desc');

  { Сторінка вибору: нічого не ставимо мовчки }
  OptPage := CreateCustomPage(wpSelectDir, S('opt_title'), S('opt_desc'));

  ChkDesktop := TNewCheckBox.Create(OptPage);
  ChkDesktop.Parent := OptPage.Surface;
  ChkDesktop.Left := 0;
  ChkDesktop.Top := 0;
  ChkDesktop.Width := OptPage.SurfaceWidth;
  ChkDesktop.Caption := S('opt_desktop');
  ChkDesktop.Checked := False;

  ChkPawn := TNewCheckBox.Create(OptPage);
  ChkPawn.Parent := OptPage.Surface;
  ChkPawn.Left := 0;
  ChkPawn.Top := ChkDesktop.Top + ChkDesktop.Height + ScaleY(14);
  ChkPawn.Width := OptPage.SurfaceWidth;
  ChkPawn.Caption := S('opt_pawnio');
  ChkPawn.Checked := False;

  Hint := TNewStaticText.Create(OptPage);
  Hint.Parent := OptPage.Surface;
  Hint.Left := ScaleX(20);
  Hint.Top := ChkPawn.Top + ChkPawn.Height + ScaleY(4);
  Hint.Width := OptPage.SurfaceWidth - ScaleX(20);
  Hint.WordWrap := True;
  Hint.AutoSize := True;
  if PawnIOInstalled then
  begin
    ChkPawn.Visible := False;
    Hint.Left := 0;
    Hint.Top := ChkPawn.Top;
    Hint.Width := OptPage.SurfaceWidth;
    Hint.Caption := S('opt_pawnio_have');
  end
  else
    Hint.Caption := S('opt_pawnio_hint');

  { Робот із викруткою на сторінці встановлення }
  InstImage := nil;
end;

procedure PlaceInstallingRobot;
var
  Top, Side, Px: Integer;
  Page: TNewNotebookPage;
  Name: String;
begin
  if InstImage <> nil then Exit;
  Page := WizardForm.InstallingPage;
  Top := WizardForm.ProgressGauge.Top + WizardForm.ProgressGauge.Height + ScaleY(12);
  Side := Page.Height - Top;
  if Side > ScaleX(190) then Side := ScaleX(190);
  if Side < ScaleX(60) then Exit;
  Px := PickSize([160, 240, 320], Side);
  Name := 'installing-' + IntToStr(Px) + '.bmp';
  ExtractTemporaryFile(Name);
  InstImage := TBitmapImage.Create(WizardForm);
  InstImage.Parent := Page;
  InstImage.Stretch := True;
  InstImage.SetBounds((Page.Width - Side) div 2, Top, Side, Side);
  InstImage.Bitmap.LoadFromFile(ExpandConstant('{tmp}\' + Name));
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpInstalling then
    PlaceInstallingRobot
  else if CurPageID = wpFinished then
  begin
    WizardForm.FinishedHeadingLabel.Caption := S('finish_title');
    WizardForm.FinishedLabel.Caption := S('finish_text');
    try
      WizardForm.WizardBitmapImage2.Bitmap.LoadFromFile(FinishFile);
    except
      { лишається стандартна картинка }
    end;
  end;
end;

{ Мова інсталятора -> settings.json, лише якщо файлу ще немає: програма одразу
  відкриється цією мовою (інакше вона візьме мову Windows). Наявні налаштування не чіпаємо. }
{ Запис «Програми й компоненти» запускає деінсталятор із /SILENT /LAGNIXASK: Inno тоді не
  показує свої вікна підтвердження й «успішно видалено» (вони йшли б мовою встановлення, а не
  мовою програми), а питання ставить сам деінсталятор (InitializeUninstall). Прямий запуск
  unins000.exe теж працює, лише з додатковим стандартним підтвердженням. }
procedure TuneUninstallString;
var
  Key, Cmd: String;
begin
  Key := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{6F1B2C7E-4D1A-4A8B-9E53-2C1D7A0B91F4}_is1';
  if RegQueryStringValue(HKLM, Key, 'UninstallString', Cmd) and (Pos('/LAGNIXASK', Cmd) = 0) then
    RegWriteStringValue(HKLM, Key, 'UninstallString', Cmd + ' /SILENT /LAGNIXASK');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Path: String;
begin
  if CurStep = ssDone then TuneUninstallString;
  if CurStep = ssPostInstall then
  begin
    TuneUninstallString;
    Path := SettingsDir + '\settings.json';
    if not FileExists(Path) then
    begin
      ForceDirectories(SettingsDir);
      SaveStringToFile(Path, '{"language": "' + Lang + '"}' + #13#10, False);
    end;
  end;
end;

{ ------------------------------------------------------------------ видалення }

function ParamExists(const Name: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Name) = 0 then Result := True;
end;

{ Мова деінсталятора: settings.json "language" -> мова встановлення -> мова Windows. }
function UninstallLang: String;
var
  Json: AnsiString;
  P: Integer;
  Rest, Code: String;
begin
  Result := '';
  if LoadStringFromFile(SettingsDir + '\settings.json', Json) then
  begin
    P := Pos('"language"', Json);
    if P > 0 then
    begin
      Rest := Copy(Json, P + 10, 64);
      P := Pos(':', Rest);
      if P > 0 then
      begin
        Rest := Trim(Copy(Rest, P + 1, 60));
        if (Length(Rest) > 1) and (Rest[1] = '"') then
        begin
          Rest := Copy(Rest, 2, 20);
          P := Pos('"', Rest);
          if P > 0 then
          begin
            Code := Copy(Rest, 1, P - 1);
            if KnownLang(Code) then Result := Code;
          end;
        end;
      end;
    end;
  end;
  if Result = '' then
    if RegQueryStringValue(HKLM, 'Software\{#AppName}', 'InstallLanguage', Code) and KnownLang(Code) then
      Result := Code;
  if Result = '' then Result := WindowsLang;
end;

function SadFile(Need: Integer): String;
begin
  Result := ExpandConstant('{app}\unins-img\sad-' + IntToStr(PickSize([160, 240, 320], Need)) + '.bmp');
end;

function AddSadRobot(Form: TSetupForm; Side: Integer): TBitmapImage;
begin
  Result := TBitmapImage.Create(Form);
  Result.Parent := Form;
  Result.Stretch := True;
  Result.SetBounds(ScaleX(16), ScaleY(16), Side, Side);
  if FileExists(SadFile(Side)) then
    Result.Bitmap.LoadFromFile(SadFile(Side));
end;

function AddText(Form: TSetupForm; Left, Top, Width: Integer; const Text: String; Big: Boolean): TNewStaticText;
begin
  Result := TNewStaticText.Create(Form);
  Result.Parent := Form;
  Result.Left := Left;
  Result.Top := Top;
  Result.Width := Width;
  Result.WordWrap := True;
  Result.AutoSize := True;
  if Big then
  begin
    Result.Font.Size := Result.Font.Size + 3;
    Result.Font.Style := [fsBold];
  end;
  Result.Caption := Text;
end;

function AddButton(Form: TSetupForm; const Caption: String; Left, Width: Integer; Modal: TModalResult): TNewButton;
begin
  Result := TNewButton.Create(Form);
  Result.Parent := Form;
  Result.Caption := Caption;
  Result.SetBounds(Left, Form.ClientHeight - ScaleY(23 + 14), Width, ScaleY(23));
  Result.ModalResult := Modal;
end;

{ Вікно 1: сумний робот. True — «Так, видалити». }
function AskRemove: Boolean;
var
  F: TSetupForm;
  T1, T2: TNewStaticText;
  Yes, No: TNewButton;
  Side, X, W: Integer;
begin
  F := CreateCustomForm(ScaleX(500), ScaleY(214), False, True);
  try
    F.Caption := '{#AppName}';
    Side := ScaleX(128);
    AddSadRobot(F, Side);
    X := ScaleX(16) + Side + ScaleX(18);
    T1 := AddText(F, X, ScaleY(24), F.ClientWidth - X - ScaleX(16), S('un_title'), True);
    T2 := AddText(F, X, T1.Top + T1.Height + ScaleY(12), F.ClientWidth - X - ScaleX(16), S('un_text'), False);

    W := F.CalculateButtonWidth([S('un_yes'), S('un_no')]) + ScaleX(12);
    Yes := AddButton(F, S('un_yes'), F.ClientWidth - ScaleX(16) - W * 2 - ScaleX(8), W, mrYes);
    No := AddButton(F, S('un_no'), F.ClientWidth - ScaleX(16) - W, W, mrCancel);
    No.Default := True;
    No.Cancel := True;
    F.ActiveControl := No;
    Result := F.ShowModal = mrYes;
  finally
    F.Free;
  end;
end;

var
  BoxRevert, BoxWipe: TNewCheckBox;

procedure ClickRevert(Sender: TObject);
begin
  BoxRevert.Checked := not BoxRevert.Checked;
end;

procedure ClickWipe(Sender: TObject);
begin
  BoxWipe.Checked := not BoxWipe.Checked;
end;

{ Рядок «галочка + підпис, що переноситься». Повертає висоту рядка. }
function AddCheckRow(Form: TSetupForm; Left, Top, Width: Integer; const Text: String; Checked: Boolean;
  Click: TNotifyEvent; var Box: TNewCheckBox): Integer;
var
  Lbl: TNewStaticText;
begin
  Box := TNewCheckBox.Create(Form);
  Box.Parent := Form;
  Box.SetBounds(Left, Top, ScaleX(18), ScaleY(18));
  Box.Checked := Checked;
  Lbl := AddText(Form, Left + ScaleX(24), Top + ScaleY(1), Width - ScaleX(24), Text, False);
  Lbl.OnClick := Click;
  Result := Lbl.Height + ScaleY(2);
  if Result < Box.Height then Result := Box.Height;
end;

{ Вікно 2: відкат змін (якщо є що відкочувати) і питання про дані. Вибір -> UnRevert/UnWipe. }
function AskOptions: Boolean;
var
  F: TSetupForm;
  T: TNewStaticText;
  OK, Cancel: TNewButton;
  Side, X, Y, W, RowW: Integer;
  Info: String;
begin
  Result := False;
  BoxRevert := nil;
  BoxWipe := nil;
  F := CreateCustomForm(ScaleX(560), ScaleY(300), False, True);
  try
    F.Caption := '{#AppName}';
    Side := ScaleX(96);
    AddSadRobot(F, Side);
    X := ScaleX(16) + Side + ScaleX(18);
    RowW := F.ClientWidth - X - ScaleX(16);
    T := AddText(F, X, ScaleY(18), RowW, S('un2_title'), True);
    Y := T.Top + T.Height + ScaleY(10);

    if UnState <> 0 then
    begin
      Info := '';
      if UnState and 1 <> 0 then Info := S('un2_tweaks');
      if UnState and 2 <> 0 then
      begin
        if Info <> '' then Info := Info + #13#10;
        Info := Info + S('un2_ultra');
      end;
      T := AddText(F, X, Y, RowW, Info, False);
      Y := T.Top + T.Height + ScaleY(10);
      Y := Y + AddCheckRow(F, X, Y, RowW, S('un2_revert'), True, @ClickRevert, BoxRevert) + ScaleY(10);
    end;
    Y := Y + AddCheckRow(F, X, Y, RowW, S('un2_wipe'), False, @ClickWipe, BoxWipe) + ScaleY(14);

    if Y < ScaleY(16) + Side + ScaleY(14) then Y := ScaleY(16) + Side + ScaleY(14);
    F.ClientHeight := Y + ScaleY(23 + 14);
    W := F.CalculateButtonWidth([S('un2_go'), S('un_no')]) + ScaleX(12);
    OK := AddButton(F, S('un2_go'), F.ClientWidth - ScaleX(16) - W * 2 - ScaleX(8), W, mrOk);
    Cancel := AddButton(F, S('un_no'), F.ClientWidth - ScaleX(16) - W, W, mrCancel);
    Cancel.Default := True;
    Cancel.Cancel := True;
    F.ActiveControl := Cancel;
    Result := F.ShowModal = mrOk;
    if Result then
    begin
      UnRevert := (BoxRevert <> nil) and BoxRevert.Checked;
      UnWipe := BoxWipe.Checked;
    end;
  finally
    F.Free;
  end;
end;

function InitializeUninstall: Boolean;
var
  Exe: String;
  Code: Integer;
begin
  Result := True;
  Tbl := TStringList.Create;
  LoadStrings;
  Lang := UninstallLang;
  UnState := 0;
  UnRevert := False;
  UnWipe := False;
  { справді тихе видалення (/VERYSILENT тощо): нічого не питаємо, дані й твіки лишаємо }
  if UninstallSilent and not ParamExists('/LAGNIXASK') then Exit;

  { що Lagnix змінив: біт 1 — твіки, біт 2 — «Lagnix Ultra» / Ігровий режим }
  Exe := ExpandConstant('{app}\{#AppExe}');
  if FileExists(Exe) then
    if Exec(Exe, '--uninstall-state', '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code > 0) and (Code < 4) then
      UnState := Code;

  Result := AskRemove;
  if Result then Result := AskOptions;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Exe: String;
  Code: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    { відкат — поки Lagnix.exe ще на місці }
    if UnRevert then
    begin
      Exe := ExpandConstant('{app}\{#AppExe}');
      if not (Exec(Exe, '--restore-all', '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0)) then
        MsgBox(S('un_revert_fail'), mbError, MB_OK);
    end;
    { автозапуск у Планувальнику (і старе завдання/запис Run) видаляємо завжди }
    Exec(ExpandConstant('{sys}\schtasks.exe'), '/Delete /TN "{#AppName}" /F', '', SW_HIDE, ewWaitUntilTerminated, Code);
    Exec(ExpandConstant('{sys}\schtasks.exe'), '/Delete /TN "PulseFPS" /F', '', SW_HIDE, ewWaitUntilTerminated, Code);
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', '{#AppName}');
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'PulseFPS');
  end
  else if CurUninstallStep = usPostUninstall then
  begin
    if UnWipe then
      DelTree(SettingsDir, True, True, True);
  end;
end;
