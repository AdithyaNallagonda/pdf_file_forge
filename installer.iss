; Inno Setup script — compile with: iscc installer.iss
; Produces dist\FileForge-Setup.exe : a normal double-click Windows installer.

#define AppName    "FileForge"
#define AppVersion "1.0.0"
#define AppPublish "FileForge"
#define AppExe     "FileForge.exe"

[Setup]
AppId={{8C4B1F42-2E7A-4E96-9E1B-3F5D6A2C8B10}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublish}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=dist
OutputBaseFilename={#AppName}-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Per-user install => no UAC prompt, no admin password needed.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DisableProgramGroupPage=yes
; "x64" (not "x64compatible") so this compiles on Inno Setup 6.0-6.2 as well as 6.3+.
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayIcon={app}\{#AppExe}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut (you can drag PDFs onto it)"; \
    GroupDescription: "Shortcuts:"
Name: "contextmenu"; Description: "Add ""Convert to Markdown"" to the right-click menu for PDFs"; \
    GroupDescription: "Explorer integration:"

[Files]
Source: "dist\FileForge\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "README.md";        DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
Name: "{group}\{#AppName}";              Filename: "{app}\{#AppExe}"
Name: "{group}\Results folder";          Filename: "{code:ResultsDir}"
Name: "{group}\Uninstall {#AppName}";    Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";        Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; Right-click a PDF -> "Convert to Markdown (FileForge)" : converts and opens the app.
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\FileForgeMd"; \
    ValueType: string; ValueName: ""; ValueData: "Convert to Markdown (FileForge)"; \
    Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\FileForgeMd"; \
    ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#AppExe},0"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\FileForgeMd\command"; \
    ValueType: string; ValueName: ""; \
    ValueData: """{app}\{#AppExe}"" --op pdf_to_md ""%1"""; Tasks: contextmenu

[Dirs]
; Workspace. D: when it exists, else the user profile — matches the app's own logic.
Name: "{code:WorkRoot}\result_files"
Name: "{code:WorkRoot}\temp"
Name: "{code:WorkRoot}\logs"

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName} now"; \
    Flags: nowait postinstall skipifsilent

[Code]
function WorkRoot(Param: String): String;
begin
  if DirExists('D:\') then
    Result := 'D:\{#AppName}'
  else
    Result := ExpandConstant('{localappdata}\{#AppName}');
end;

function ResultsDir(Param: String): String;
begin
  Result := WorkRoot('') + '\result_files';
end;
