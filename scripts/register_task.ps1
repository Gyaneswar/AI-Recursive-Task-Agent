param(
    [string]$AgentArgs = "--loop",
    [string]$Name = "TaskAgent"
)
$root = Split-Path $PSScriptRoot -Parent
$runner = Join-Path $PSScriptRoot "run_agent.ps1"
$argLine = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$runner`" $AgentArgs"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argLine -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
"registered '$Name': powershell.exe $argLine"
"start now : Start-ScheduledTask -TaskName $Name"
"stop      : powershell -File scripts\stop_agent.ps1"
"remove    : Unregister-ScheduledTask -TaskName $Name -Confirm:`$false"
