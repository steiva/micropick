; Inno Setup script for the Windows installer: packaging/build.py compiles
; it after PyInstaller, passing the version and where things are:
;   ISCC /DAppVersion=0.1.0 /DSourceDir=dist\micropick /DOutputDir=dist
;        /DSetupName=micropick-0.1.0-win64-setup packaging\micropick.iss
;
; It installs the one-folder build as it is. By default for the current
; user, into %LOCALAPPDATA%\Programs\micropick, which needs no
; administrator; the first page offers all users (Program Files) to someone
; who has the rights. The program's folder holds nothing but the program:
; profiles, settings, logs and images are in Documents\micropick
; (paths.root), so updating or uninstalling never touches them.
;
; The AppId below is what makes a newer installer an update of the same
; program rather than a second copy. Never change it.

#ifndef AppVersion
  #error Pass /DAppVersion=<version> (packaging/build.py does)
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\micropick"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif
#ifndef SetupName
  #define SetupName "micropick-" + AppVersion + "-win64-setup"
#endif
; The longest file path inside the build, relative to its folder (build.py
; measures it; an Opentrons labware definition, about 150 characters).
#ifndef LongestPath
  #define LongestPath 160
#endif

[Setup]
AppId={{51C91FB4-25C3-4163-B4AD-559F71B74E23}
AppName=micropick
AppVersion={#AppVersion}
AppVerName=micropick {#AppVersion}
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\micropick
DefaultGroupName=micropick
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename={#SetupName}
; About a gigabyte, mostly torch: solid LZMA2 makes the installer roughly a
; third of that, at the cost of a few minutes of compiling.
Compression=lzma2
SolidCompression=yes
LZMAUseSeparateProcess=yes
UninstallDisplayIcon={app}\micropick.exe
UninstallDisplayName=micropick {#AppVersion}
; A running micropick holds its DLLs open; the installer asks to close it.
CloseApplications=yes
RestartApplications=no
WizardStyle=modern

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[CustomMessages]
en.DirTooDeep=The folder%n%n%1%n%nis too deep: some of micropick's files would end up with paths longer than Windows allows. Choose a folder at most %2 characters long, such as%n%n%3
ru.DirTooDeep=Папка%n%n%1%n%nслишком глубоко: пути к некоторым файлам micropick получатся длиннее, чем допускает Windows. Выберите папку не длиннее %2 символов, например%n%n%3

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; An update replaces the libraries whole: a module the new version no longer
; ships must not stay behind and be imported in place of the right one.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\micropick"; Filename: "{app}\micropick.exe"
Name: "{autodesktop}\micropick"; Filename: "{app}\micropick.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\micropick.exe"; Description: "{cm:LaunchProgram,micropick}"; Flags: nowait postinstall skipifsilent

[Code]
// A path past MAX_PATH (259 characters) cannot be created, and the
// installer would stop half-way with "the system cannot find the path
// specified". Checked when the folder is chosen and again before
// installing, which also covers a silent install given /DIR.
function LongestDir(): Integer;
begin
  Result := 259 - {#LongestPath};
end;

function DirTooDeep(Dir: String): String;
begin
  Result := '';
  // The arguments stay on the call's line: a line starting with "[" is
  // a section tag to the compiler, even here.
  if Length(AddBackslash(Dir)) > LongestDir() then
    Result := FmtMessage(CustomMessage('DirTooDeep'), [Dir,
      IntToStr(LongestDir() - 1), ExpandConstant('{autopf}\micropick')]);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Problem: String;
begin
  Result := True;
  // Silent, the page is passed without anyone to answer a message box;
  // PrepareToInstall refuses then.
  if (CurPageID = wpSelectDir) and not WizardSilent() then
  begin
    Problem := DirTooDeep(WizardDirValue());
    if Problem <> '' then
    begin
      MsgBox(Problem, mbError, MB_OK);
      Result := False;
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := DirTooDeep(WizardDirValue());
end;
