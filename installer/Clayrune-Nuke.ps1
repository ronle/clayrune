# ============================================================
#  Clayrune-Nuke.ps1
#
#  Removes Clayrune install + Claude CLI + related artifacts on Windows.
#  Use this to start fresh before re-running the installer.
#
#  Run via:
#    iwr https://raw.githubusercontent.com/ronle/mission-control/master/installer/Clayrune-Nuke.ps1 -useb | iex
#
#  Default (always removes):
#    - $env:USERPROFILE\Clayrune (install dir)
#    - Desktop + Start Menu Clayrune.lnk shortcuts
#    - npm-installed Claude CLI
#    - $env:USERPROFILE\.claude (auth + transcripts) — RE-LOGIN NEEDED
#    - Any process listening on :5199 (the local server)
#
#  Does NOT remove by default:
#    - Node.js
#    - Git for Windows
#
#  To also remove Node + Git, follow up with:
#    winget uninstall --id OpenJS.NodeJS.LTS -e --silent
#    winget uninstall --id Git.Git -e --silent
# ============================================================

Write-Host ''
Write-Host '== BEFORE ==' -ForegroundColor Cyan
Write-Host "claude:        $((Get-Command claude -ErrorAction SilentlyContinue).Path)"
Write-Host "node:          $((Get-Command node -ErrorAction SilentlyContinue).Path)"
Write-Host "git:           $((Get-Command git -ErrorAction SilentlyContinue).Path)"
Write-Host "Clayrune dir:  $($env:USERPROFILE)\Clayrune $(if (Test-Path "$env:USERPROFILE\Clayrune") { '(exists)' } else { '(none)' })"
Write-Host "~/.claude:     $($env:USERPROFILE)\.claude $(if (Test-Path "$env:USERPROFILE\.claude") { '(exists)' } else { '(none)' })"
Write-Host ''

# 1) Stop the server if running on :5199
Write-Host '-- 1. Stop server on :5199 (if running)' -ForegroundColor Yellow
$conns = Get-NetTCPConnection -LocalPort 5199 -State Listen -ErrorAction SilentlyContinue
if ($conns) {
    foreach ($c in $conns) {
        try {
            Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
            Write-Host "  killed PID $($c.OwningProcess)"
        } catch {}
    }
} else {
    Write-Host '  (nothing listening on :5199)'
}

# 2) Clayrune install dir
Write-Host ''
Write-Host '-- 2. Remove ~\Clayrune' -ForegroundColor Yellow
if (Test-Path "$env:USERPROFILE\Clayrune") {
    try {
        Remove-Item "$env:USERPROFILE\Clayrune" -Recurse -Force
        Write-Host "  removed $env:USERPROFILE\Clayrune"
    } catch {
        Write-Host "  could not remove (in use?): $_" -ForegroundColor Red
    }
} else {
    Write-Host '  (no install dir)'
}

# 3) Shortcuts
Write-Host ''
Write-Host '-- 3. Remove Clayrune shortcuts' -ForegroundColor Yellow
$shortcuts = @(
    "$env:USERPROFILE\Desktop\Clayrune.lnk",
    "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Clayrune.lnk"
)
foreach ($p in $shortcuts) {
    if (Test-Path $p) {
        Remove-Item $p -Force -ErrorAction SilentlyContinue
        Write-Host "  removed $p"
    }
}

# 4) Claude CLI (npm uninstall + scrub binary)
Write-Host ''
Write-Host '-- 4. Uninstall Claude CLI' -ForegroundColor Yellow
if (Get-Command npm -ErrorAction SilentlyContinue) {
    npm uninstall -g '@anthropic-ai/claude-code' 2>&1 | Out-Null
    Write-Host '  npm uninstall -g @anthropic-ai/claude-code'
}
$cl = Get-Command claude -ErrorAction SilentlyContinue
if ($cl -and $cl.Path -and (Test-Path $cl.Path)) {
    try {
        Remove-Item $cl.Path -Force -ErrorAction SilentlyContinue
        Write-Host "  removed $($cl.Path)"
    } catch {}
}

# 5) ~/.claude (auth + transcripts)
Write-Host ''
Write-Host '-- 5. ~\.claude (auth + transcripts)' -ForegroundColor Yellow
if (Test-Path "$env:USERPROFILE\.claude") {
    Write-Host '  Removing in 5s. Ctrl+C to KEEP — re-login needed if removed.'
    Start-Sleep -Seconds 5
    Remove-Item "$env:USERPROFILE\.claude" -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "  removed $env:USERPROFILE\.claude"
} else {
    Write-Host '  (no ~\.claude)'
}

# Refresh PATH so the AFTER report reflects what's gone
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
            [Environment]::GetEnvironmentVariable('Path', 'User')

Write-Host ''
Write-Host '== AFTER ==' -ForegroundColor Cyan
Write-Host "claude:        $((Get-Command claude -ErrorAction SilentlyContinue).Path)"
Write-Host "node:          $((Get-Command node -ErrorAction SilentlyContinue).Path)"
Write-Host "git:           $((Get-Command git -ErrorAction SilentlyContinue).Path)"
Write-Host "Clayrune dir:  $(if (Test-Path "$env:USERPROFILE\Clayrune") { 'exists' } else { '(none)' })"
Write-Host "~/.claude:     $(if (Test-Path "$env:USERPROFILE\.claude") { 'exists' } else { '(none)' })"
Write-Host ''
Write-Host 'Done. Open a NEW PowerShell window so PATH refreshes, then run the installer.'
Write-Host ''
Write-Host 'To also remove Node + Git (test the bootstrap from full virgin state):' -ForegroundColor Yellow
Write-Host '  winget uninstall --id OpenJS.NodeJS.LTS -e --silent'
Write-Host '  winget uninstall --id Git.Git -e --silent'
