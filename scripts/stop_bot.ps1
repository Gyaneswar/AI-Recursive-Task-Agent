Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='powershell.exe'" |
    Where-Object { ($_.CommandLine -like '*-m jobbot*' -or $_.CommandLine -like '*run_bot.ps1*') -and $_.ProcessId -ne $PID } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; "stopped $($_.ProcessId)" }
