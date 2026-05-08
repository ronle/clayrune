@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM  Clayrune Installer (Windows)
REM
REM  Double-click this file to install Clayrune on your PC.
REM  Wraps install.ps1 so users don't need to type PowerShell
REM  commands manually.
REM ============================================================

title Clayrune Installer

echo.
echo ============================================================
echo   Clayrune Installer
echo ============================================================
echo.
echo This will install Clayrune on this computer.
echo.
echo It will:
echo   1. Install Node.js LTS (if missing)
echo   2. Install Git for Windows (needed by Claude Code)
echo   3. Install Claude CLI
echo   4. Ask you to log in once (browser opens for OAuth)
echo   5. Clone Clayrune to %%USERPROFILE%%\Clayrune
echo   6. Set up Python dependencies + a Desktop shortcut
echo   7. Open the dashboard in your browser
echo.
echo Estimated time: 5-10 minutes.
echo Disk space: about 500 MB.
echo.
echo You can audit what runs by reading:
echo   https://raw.githubusercontent.com/ronle/mission-control/master/installer/install-prompt.md
echo.
pause

:run_installer
echo.
echo Starting installer...
echo.

REM Hand off to PowerShell with execution policy bypass for THIS session only.
REM We pre-set CLAYRUNE_PROMPT_URL so the bootstrap fetches the install prompt
REM from the GitHub raw URL until clayrune.io DNS is configured. Once the
REM domain is live, the inner default URL takes over and this line can be
REM dropped.
powershell.exe -ExecutionPolicy Bypass -NoProfile -Command ^
  "$env:CLAYRUNE_PROMPT_URL = 'https://raw.githubusercontent.com/ronle/mission-control/master/installer/install-prompt.md'; iwr https://raw.githubusercontent.com/ronle/mission-control/master/installer/install.ps1 -useb | iex"

set "PSEXIT=%ERRORLEVEL%"

echo.
echo ============================================================
if "%PSEXIT%"=="0" goto :success

echo   Installer exited with error code %PSEXIT%.
echo.
echo   The full output is above this line. Common cases:
echo     - "Claude CLI is installed but not authenticated":
echo       run  claude /login  in another window, log in, type exit,
echo       then come back here and pick R below to retry.
echo     - Network or winget hiccup: pick R to retry.
echo.
echo ============================================================
echo.
echo   What now?
echo     [R] Retry the installer ^(after you've fixed the issue^)
echo     [Q] Quit and close this window
echo.

:choice_loop
set "choice="
set /p choice="Press R or Q then Enter: "
if /i "%choice%"=="R" goto :run_installer
if /i "%choice%"=="Q" goto :end
echo Please enter R or Q.
goto :choice_loop

:success
echo   Done.
echo.
echo   You'll find a "Clayrune" shortcut on your Desktop and in
echo   your Start Menu. Double-click it any time to launch.
echo ============================================================
echo.
echo Press any key to close this window . . .
pause >nul

:end
endlocal
