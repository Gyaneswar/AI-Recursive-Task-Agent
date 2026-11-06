$edge = "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
if (-not (Test-Path $edge)) { $edge = "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe" }
$profileDir = "$env:LOCALAPPDATA\JobBotEdge"
Start-Process -FilePath $edge -ArgumentList "--remote-debugging-port=9222", "--user-data-dir=$profileDir", "--no-first-run"
