$root = Split-Path $PSScriptRoot -Parent
$lms = "$env:USERPROFILE\.lmstudio\bin\lms.exe"
$model = "google/gemma-4-e4b"

function Test-Url($u) { try { Invoke-RestMethod $u -TimeoutSec 3 | Out-Null; $true } catch { $false } }

if (-not (Test-Url "http://localhost:1234/v1/models")) { & $lms server start --port 1234 }
if (-not ((& $lms ps | Out-String) -match [regex]::Escape($model))) {
    & $lms load $model --gpu max --context-length 8192 -y
}
if (-not (Test-Url "http://127.0.0.1:9222/json/version")) {
    & "$PSScriptRoot\start_edge.ps1"
    Start-Sleep 6
}

Set-Location $root
New-Item -ItemType Directory -Force "data\logs" | Out-Null
$log = "data\logs\run_$(Get-Date -Format yyyyMMdd_HHmmss).log"
& "$root\.venv\Scripts\python.exe" -u -m jobbot @args 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $log
