[Setup]
AppId={{D3E83C56-B1A4-4A68-9A7B-9A3E4562B2F4}
AppName=Estoke Ao Cubo
AppVersion=1.0
AppPublisher=Estoke Ao Cubo
DefaultDirName={autopf}\Estoke Ao Cubo
DefaultGroupName=Estoke Ao Cubo
AllowNoIcons=yes
OutputDir=g:\Meu Drive\100\EstokeAoCubo\Output
OutputBaseFilename=EstokeSetup Completo
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "g:\Meu Drive\100\EstokeAoCubo\dist\EstokeAoCubo\EstokeAoCubo.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "g:\Meu Drive\100\EstokeAoCubo\dist\EstokeAoCubo\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Estoke Ao Cubo"; Filename: "{app}\EstokeAoCubo.exe"
Name: "{autodesktop}\Estoke Ao Cubo"; Filename: "{app}\EstokeAoCubo.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\EstokeAoCubo.exe"; Description: "{cm:LaunchProgram,Estoke Ao Cubo}"; Flags: nowait postinstall skipifsilent
