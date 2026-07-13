<#
  build-dev-apk.ps1 -- build + install a HERMETIC debug Clayrune APK on the
  emulator for the mobile chat-switching release test.

  The shipping app loads the SPA from the Cloudflare tunnel and gates on stored
  CF-Access credentials (MainActivity -> SetupActivity). For an automated,
  credential-free emulator test we build a throwaway debug APK that:
    - loads the SPA from http://10.0.2.2:5199 (the emulator's alias for this
      host's Mission Control server) over cleartext, no Cloudflare, and
    - skips the credential gate so the dashboard loads directly.

  The three edits are applied to a CLEAN checkout, the APK is built + installed,
  then the edits are reverted with `git checkout` so the mobile repo stays clean.
  NEVER ship the resulting APK -- it is debug-only and bypasses auth.

  Prereqs: JDK + Android SDK, the mobile repo (a separate checkout -- see
  CLAUDE.md), a booted emulator (see boot-emulator.ps1), and MC running on :5199.

  Paths are per-machine, so they come from the environment rather than being
  hardcoded. Set CLAYRUNE_MOBILE_REPO, JAVA_HOME, and ANDROID_HOME (or pass
  -MobileRepo / -JavaHome / -Adb explicitly).
#>
param(
  [string]$MobileRepo = $(if ($env:CLAYRUNE_MOBILE_REPO) { $env:CLAYRUNE_MOBILE_REPO } else { '' }),
  [string]$JavaHome   = $(if ($env:JAVA_HOME) { $env:JAVA_HOME } else { '' }),
  [string]$Adb        = $(if ($env:ANDROID_HOME) { Join-Path $env:ANDROID_HOME 'platform-tools\adb.exe' } else { 'adb' }),
  [string]$Serial     = 'emulator-5554',
  [string]$DevUrl     = 'http://10.0.2.2:5199'
)

if (-not $MobileRepo) {
  throw "Mobile repo path not set. Set CLAYRUNE_MOBILE_REPO or pass -MobileRepo <path>."
}
if (-not $JavaHome) {
  throw "JDK path not set. Set JAVA_HOME or pass -JavaHome <path>."
}
$ErrorActionPreference = 'Stop'
$env:JAVA_HOME = $JavaHome

$cfg      = Join-Path $MobileRepo 'capacitor.config.json'
$manifest = Join-Path $MobileRepo 'android\app\src\main\AndroidManifest.xml'
$mainAct  = Join-Path $MobileRepo 'android\app\src\main\java\io\clayrune\app\MainActivity.java'

Write-Host "== 1/5 applying dev edits (transient) =="

# capacitor.config.json -- point at the host server, allow cleartext, keep the
# WebView debuggable (captureInput must stay false -- Gboard IME gotcha).
@"
{
  "appId": "io.clayrune.app",
  "appName": "Clayrune",
  "webDir": "www",
  "server": {
    "url": "$DevUrl",
    "cleartext": true,
    "androidScheme": "http"
  },
  "android": {
    "allowMixedContent": true,
    "captureInput": false,
    "webContentsDebuggingEnabled": true
  }
}
"@ | Set-Content -Path $cfg -Encoding utf8

# AndroidManifest -- permit cleartext to 10.0.2.2.
$m = Get-Content $manifest -Raw
if ($m -notmatch 'usesCleartextTraffic') {
  $m = $m.Replace(
    "    <application`r`n        android:allowBackup=`"true`"`r`n        android:icon=",
    "    <application`r`n        android:allowBackup=`"true`"`r`n        android:usesCleartextTraffic=`"true`"`r`n        android:icon=")
  Set-Content -Path $manifest -Value $m -Encoding utf8
}

# MainActivity -- load server.url natively + skip the CF credential gate.
$j = Get-Content $mainAct -Raw
$needle = "    public void onCreate(Bundle savedInstanceState) {`r`n        CredentialStore.Creds creds = CredentialStore.load(this);"
$repl   = "    public void onCreate(Bundle savedInstanceState) {`r`n" +
          "        // [DEV BUILD ONLY -- reverted after build] Hermetic emulator testing.`r`n" +
          "        super.onCreate(savedInstanceState);`r`n" +
          "        if (Boolean.TRUE) { return; }`r`n`r`n" +
          "        CredentialStore.Creds creds = CredentialStore.load(this);"
if ($j -notmatch 'DEV BUILD ONLY') { $j = $j.Replace($needle, $repl); Set-Content -Path $mainAct -Value $j -Encoding utf8 }

try {
  Write-Host "== 2/5 cap copy =="
  Push-Location $MobileRepo
  & npx cap copy android
  if ($LASTEXITCODE -ne 0) { throw "cap copy failed ($LASTEXITCODE)" }
  Pop-Location

  Write-Host "== 3/5 gradle assembleDebug =="
  Push-Location (Join-Path $MobileRepo 'android')
  & .\gradlew.bat assembleDebug --console=plain
  if ($LASTEXITCODE -ne 0) { throw "gradle failed ($LASTEXITCODE)" }
  Pop-Location
}
finally {
  Write-Host "== 4/5 reverting dev edits (git checkout) =="
  & git -C $MobileRepo checkout -- `
    capacitor.config.json `
    android/app/src/main/AndroidManifest.xml `
    android/app/src/main/java/io/clayrune/app/MainActivity.java
}

Write-Host "== 5/5 install + launch =="
$apk = Join-Path $MobileRepo 'android\app\build\outputs\apk\debug\app-debug.apk'
& $Adb -s $Serial install -r -t $apk
& $Adb -s $Serial shell monkey -p io.clayrune.app -c android.intent.category.LAUNCHER 1 | Out-Null
Write-Host "DONE -- dev APK installed. SPA loads from $DevUrl ; mobile repo restored to clean."
