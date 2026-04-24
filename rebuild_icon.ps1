# Rebuild Mission Control to pick up the new taskbar icon.
# Run this from a separate PowerShell AFTER closing the Mission Control window.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "=== Step 1: Verify no app.exe is running ===" -ForegroundColor Cyan
$running = Get-Process -Name "app" -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -and $_.Path.StartsWith($PSScriptRoot)
}
if ($running) {
    Write-Host "app.exe is still running (PID $($running.Id)). Close Mission Control window first." -ForegroundColor Red
    exit 1
}
Write-Host "OK" -ForegroundColor Green

Write-Host "`n=== Step 2: Touch build.rs to force tauri-build to regenerate resource.rc ===" -ForegroundColor Cyan
(Get-Item "src-tauri\build.rs").LastWriteTime = Get-Date
Write-Host "OK" -ForegroundColor Green

Write-Host "`n=== Step 3: Clear any stale compiled resource.rc files ===" -ForegroundColor Cyan
Get-ChildItem -Path "src-tauri\target\debug\build" -Filter "resource.rc" -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
    Remove-Item $_.FullName -Force
    Write-Host "  removed $($_.FullName)"
}
Write-Host "OK" -ForegroundColor Green

Write-Host "`n=== Step 4: Clear Windows icon cache so taskbar shows the new icon ===" -ForegroundColor Cyan
Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
$iconCache = "$env:LOCALAPPDATA\IconCache.db"
if (Test-Path $iconCache) { Remove-Item $iconCache -Force -ErrorAction SilentlyContinue }
Get-ChildItem "$env:LOCALAPPDATA\Microsoft\Windows\Explorer" -Filter "iconcache*" -ErrorAction SilentlyContinue | ForEach-Object {
    Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
}
Get-ChildItem "$env:LOCALAPPDATA\Microsoft\Windows\Explorer" -Filter "thumbcache*" -ErrorAction SilentlyContinue | ForEach-Object {
    Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
}
Start-Process explorer.exe
Write-Host "OK (explorer restarted)" -ForegroundColor Green

Write-Host "`n=== Step 5: Rebuild + launch Tauri dev ===" -ForegroundColor Cyan
Write-Host "Running: npx tauri dev" -ForegroundColor Yellow
npx tauri dev
