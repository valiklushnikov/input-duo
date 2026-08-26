; Duo Input configurator installer.
;
; Per-user by default: the configurator needs no administrator rights to run,
; so it must not demand them to install. Nothing here installs a driver - the
; device is a plain USB HID plus a CDC serial port, both of which Windows
; already has drivers for. An installer that asks for administrator and
; installs drivers is exactly the shape of thing operators are told not to
; trust, and this program has no reason to look like one.
;
; Build with:
;   ISCC.exe configurator\packaging\duo-input.iss
; after configurator\packaging\nuitka-build.ps1 has produced dist\DuoInput.

#define AppName "Duo Input"
#define AppExeName "DuoInput.exe"
#define AppPublisher "Duo Input"

; The four-part version comes from the built executable, so the installer can
; never claim a version the program does not actually carry. The name on the
; file uses the SemVer the release is called by, which build_release.ps1 passes
; in; without it the four-part number stands in.
#define BuiltVersion GetVersionNumbersString("..\dist\DuoInput\DuoInput.exe")
#ifndef AppVersion
  #define AppVersion BuiltVersion
#endif

[Setup]
AppId={{8B2D9C41-6E3A-4F17-9C2B-DA51F0E7A3C9}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#BuiltVersion}

; Per-user install, no elevation.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes

ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

OutputDir=..\dist
OutputBaseFilename=DuoInput-Setup-{#AppVersion}-x64
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

; Russian first: it is the default interface language of the program itself.
ShowLanguageDialog=auto

; Uninstall entry, and the icon it shows in Apps & features.
UninstallDisplayName={#AppName} {#AppVersion}
UninstallDisplayIcon={app}\{#AppExeName}

LicenseFile=..\..\docs\release\third-party-licenses.md

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The whole standalone folder, exactly as the build produced it.
Source: "..\dist\DuoInput\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Only what this installer put there. %LOCALAPPDATA%\DuoInput holds the
; operator's autosave, logs and settings, and their .duoinput.json projects
; live wherever they chose to save them; uninstalling must not touch either.
Type: filesandordirs; Name: "{app}"
