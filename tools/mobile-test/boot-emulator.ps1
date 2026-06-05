<#
  boot-emulator.ps1 — create (if missing) and boot a headless Android emulator
  for the mobile chat-switching release test. Idempotent.

  Requires the Android cmdline-tools + an accepted system image. On this machine
  ANDROID_HOME = E:\Android and WHPX acceleration is available. If the emulator/
  system image aren't installed yet, run once:
    $sdk = "E:\Android\cmdline-tools\latest\bin\sdkmanager.bat"
    & $sdk --licenses
    & $sdk "emulator" "system-images;android-34;google_apis;x86_64" "platforms;android-34"
#>
param(
  [string]$AndroidHome = 'E:\Android',
  [string]$JavaHome    = 'E:\JDK\jdk-21',
  [string]$Avd         = 'clayrune_test',
  [string]$Image       = 'system-images;android-34;google_apis;x86_64',
  [int]$Port           = 5554
)
$ErrorActionPreference = 'Stop'
$env:JAVA_HOME = $JavaHome
$env:ANDROID_AVD_HOME = "$env:USERPROFILE\.android\avd"
$adb = Join-Path $AndroidHome 'platform-tools\adb.exe'
$emu = Join-Path $AndroidHome 'emulator\emulator.exe'
$serial = "emulator-$Port"

# Already booted?
$booted = (& $adb -s $serial shell getprop sys.boot_completed 2>$null)
if (("$booted").Trim() -eq '1') { Write-Host "$serial already booted."; exit 0 }

# Create AVD if missing.
$avds = & $emu -list-avds
if ($avds -notcontains $Avd) {
  Write-Host "creating AVD $Avd ..."
  $avdmgr = Join-Path $AndroidHome 'cmdline-tools\latest\bin\avdmanager.bat'
  cmd /c "echo no| `"$avdmgr`" create avd -n $Avd -k `"$Image`" -d pixel_6 -f"
  $cfg = "$env:ANDROID_AVD_HOME\$Avd.avd\config.ini"
  $lines = Get-Content $cfg
  if ($lines -notmatch '^hw\.keyboard=') { Add-Content $cfg 'hw.keyboard=yes' }
}

Write-Host "booting $Avd headless on port $Port ..."
Start-Process -FilePath $emu -ArgumentList @(
  '-avd', $Avd, '-no-window', '-no-snapshot', '-no-boot-anim',
  '-gpu', 'swiftshader_indirect', '-no-audio', '-port', "$Port"
) -WindowStyle Hidden

& $adb -s $serial wait-for-device
for ($i = 0; $i -lt 90; $i++) {
  Start-Sleep -Seconds 3
  if ((("$(& $adb -s $serial shell getprop sys.boot_completed 2>$null)").Trim()) -eq '1') {
    Write-Host "$serial booted (~$($i*3)s)."; exit 0
  }
}
Write-Error "emulator did not finish booting in time"; exit 1
