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
$icon    = Join-Path $repo 'src-tauri\icons\icon.ico'
$outExe  = Join-Path $repo 'installer\Clayrune-Installer.exe'

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
if (-not (Test-Path $icon)) { Write-Host "ERROR: missing $icon" -ForegroundColor Red; exit 1 }

Write-Host "csc : $csc"
Write-Host "src : $src"
Write-Host "icon: $icon"
Write-Host "out : $outExe"
Write-Host ''

$args = @(
    '/nologo',
    '/target:exe',
    '/platform:anycpu',
    '/optimize+',
    "/win32icon:$icon",
    "/out:$outExe",
    $src
)
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
