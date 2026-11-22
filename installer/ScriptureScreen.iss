; Inno Setup script for Scripture Screen. Built by installer/build_installer.py, which passes:
;   /DAppVersion=2026.11.22  /DStage=<folder made by the build script>
; Installs for the current user only (no admin password), works with no internet.

#ifndef AppVersion
  #define AppVersion "1.0"
#endif
#ifndef Stage
  #define Stage "stage"
#endif
#define AppName "Scripture Screen"

[Setup]
AppId={{6F1D2C3B-7A84-4E1B-9C55-5C2F0E7A9B21}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=ScriptureScreen-Setup
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\python\pythonw.exe
CloseApplications=yes
; about 1.7 GB once installed
ExtraDiskSpaceRequired=200000000

[Messages]
WelcomeLabel2=This puts {#AppName} on this laptop.%n%nIt listens to the preacher and shows each Bible verse on the projector or TV by itself. No internet is needed, now or during service.%n%nSetting up takes about 5 minutes.

[Files]
Source: "{#Stage}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: "-E -s -m verse_screen"; WorkingDir: "{app}"; Comment: "Bible verses on screen as the pastor speaks"
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: "-E -s -m verse_screen"; WorkingDir: "{app}"
Name: "{autoprograms}\{#AppName} (show problems)"; Filename: "{app}\Start Scripture Screen (show problems).bat"; WorkingDir: "{app}"

[Run]
; Builds the Bible database and times this laptop to pick the best speech engine it can run.
Filename: "{app}\python\python.exe"; Parameters: "-E -s -m verse_screen.first_run"; WorkingDir: "{app}"; StatusMsg: "Getting ready for this laptop (about 2 minutes)..."; Flags: runhidden waituntilterminated
Filename: "{app}\python\pythonw.exe"; Parameters: "-E -s -m verse_screen"; WorkingDir: "{app}"; Description: "Open {#AppName} now"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; made on this laptop after installing; the church's own media folder is left in place
Type: files; Name: "{app}\church_bible.db"
Type: files; Name: "{app}\settings.json"
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\data\phone_mic"
Type: filesandordirs; Name: "{app}\verse_screen\__pycache__"
