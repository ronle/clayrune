; Mission Control — Inno Setup installer script
; Compile with: iscc installer.iss
; Requires: dist\MissionControl\ from PyInstaller build

#define MyAppName "Mission Control"
#define MyAppVersion "1.2.0"
#define MyAppPublisher "Mission Control"
#define MyAppExeName "MissionControl.exe"

[Setup]
AppId={{8F3C2A1D-5B7E-4D9F-A6C8-1E2F3D4A5B6C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\MissionControl
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=installer_output
OutputBaseFilename=MissionControlSetup
SetupIconFile=src-tauri\icons\icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\MissionControl\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Mission Control"; Flags: nowait postinstall skipifsilent
