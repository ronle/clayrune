# Build installer/Clayrune-Installer.exe from ClayruneInstaller.cs.
#
# Uses the .NET Framework C# compiler (csc.exe) that ships with every
# Windows 10/11 install — no SDK, no NuGet, no build pipeline. Run from
# anywhere:  powershell -ExecutionPolicy Bypass -File installer\win-exe\build.ps1
#
# Output is written to installer\Clayrune-Installer.exe (the path the
# clayrune.io landing page links to and Cloudflare Pages serves directly).

$ErrorActionPreference = 'Stop'

$here    = Split-Path -Parent $MyInvocation.MyCommand.Path
$src     = Join-Path $here 'ClayruneInstaller.cs'
$repo    = Resolve-Path (Join-Path $here '..\..')
$outExe  = Join-Path $repo 'installer\Clayrune-Installer.exe'

# Icon. assets\clayrune.ico is the TRACKED one and the only reliable choice —
# src-tauri\icons\ is untracked build scaffolding that is absent from a fresh
# clone (and from this repo since the Tauri experiment was dropped), which made
# this script unrunnable: it hard-failed on a missing icon before compiling.
$iconCandidates = @(
    (Join-Path $repo 'assets\clayrune.ico'),
    (Join-Path $repo 'src-tauri\icons\icon.ico')
)
$icon = $iconCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

# Locate csc.exe — prefer 64-bit Framework, fall back to 32-bit.
$cscCandidates = @(
    "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
    "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe"
)
$csc = $cscCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $csc) {
    Write-Host 'ERROR: csc.exe (.NET Framework 4.x) not found.' -ForegroundColor Red
    Write-Host 'Expected at one of:' -ForegroundColor Red
    $cscCandidates | ForEach-Object { Write-Host "  $_" }
    exit 1
}
if (-not (Test-Path $src))  { Write-Host "ERROR: missing $src"  -ForegroundColor Red; exit 1 }

Write-Host "csc : $csc"
Write-Host "src : $src"
Write-Host "icon: $(if ($icon) { $icon } else { '(none found - building without one)' })"
Write-Host "out : $outExe"
Write-Host ''

$args = @(
    '/nologo',
    '/target:exe',
    '/platform:anycpu',
    '/optimize+'
)
# A missing icon is cosmetic — never let it block a build of the installer.
if ($icon) { $args += "/win32icon:$icon" } else {
    Write-Host 'WARN: no .ico found; the exe will use the default icon.' -ForegroundColor Yellow
}
$args += @("/out:$outExe", $src)
& $csc @args
if ($LASTEXITCODE -ne 0) {
    Write-Host "csc failed (exit $LASTEXITCODE)" -ForegroundColor Red
    exit $LASTEXITCODE
}

$sz = [math]::Round((Get-Item $outExe).Length / 1KB, 1)
Write-Host ''
Write-Host "Built $outExe ($sz KB)" -ForegroundColor Green
Write-Host 'Commit it alongside the source — Cloudflare Pages serves it as'
Write-Host 'https://clayrune.io/Clayrune-Installer.exe'
