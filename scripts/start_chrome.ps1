$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
if (-not (Test-Path $chrome)) { $chrome = "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe" }
$profileDir = "$env:LOCALAPPDATA\JobBotChrome"
& $chrome --remote-debugging-port=9222 --user-data-dir="$profileDir" --no-first-run
