# MC full-restart helper — invoked as a one-shot Scheduled Task so it runs
# OUTSIDE the old server's process tree. That isolation matters: the script
# hard-kills the wedged server (and its whole child tree, which includes the
# agent that scheduled this), then relaunches a fresh MC.
#
# Triggered 2026-05-21 because both the in-app restart and a manual restart
# failed to replace PID 53860 (stale process kept holding port 5199).

$ErrorActionPreference = 'Continue'
$dir = 'C:\Users\levir\Documents\_claude\mission-control'
$py  = 'C:\Users\levir\AppData\Local\Python\bin\python.exe'
$log = Join-Path $dir 'data\_mc_restart.log'

function Log($m) {
  "$(Get-Date -Format o)  $m" | Out-File -FilePath $log -Append -Encoding utf8
}

Log '=== MC restart script started ==='

# Grace period so the scheduling agent can finish its reply before it is killed.
Start-Sleep -Seconds 12

# 1. Kill every MC server process + its child tree (agents, tunnel, etc.).
$killed = @()
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -and $_.CommandLine -like '*server.py*' } |
  ForEach-Object {
    $procId = $_.ProcessId
    try {
      & taskkill /F /T /PID $procId 2>&1 | Out-Null
      $killed += $procId
      Log "killed MC server tree PID $procId"
    } catch { Log "kill failed for PID ${procId}: $_" }
  }
if ($killed.Count -eq 0) { Log 'no running server.py process found' }

# 2. Wait for port 5199 to be released.
$freed = $false
for ($i = 0; $i -lt 30; $i++) {
  Start-Sleep -Seconds 1
  $conn = Get-NetTCPConnection -LocalPort 5199 -State Listen -ErrorAction SilentlyContinue
  if (-not $conn) { $freed = $true; break }
}
Log "port 5199 free: $freed"

# 3. Relaunch MC, detached. Start-Process gives it its own lifetime so it
#    survives this script (and the Task Scheduler job) exiting.
$proc = Start-Process -FilePath $py -ArgumentList 'server.py' `
                      -WorkingDirectory $dir -WindowStyle Hidden -PassThru
Log "relaunched MC -> new PID $($proc.Id)"

# 4. Confirm it is actually serving.
$up = $false
for ($i = 0; $i -lt 30; $i++) {
  Start-Sleep -Seconds 2
  try {
    $r = Invoke-WebRequest -Uri 'http://localhost:5199/api/system/heartbeat' `
                           -UseBasicParsing -TimeoutSec 3
    if ($r.StatusCode -eq 200) { $up = $true; break }
  } catch { }
}
Log "MC heartbeat OK: $up"
Log '=== MC restart script done ==='
