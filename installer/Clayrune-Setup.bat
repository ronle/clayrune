@echo off
setlocal enabledelayedexpansion

REM Switch the current cmd window to UTF-8 codepage so any non-ASCII text
REM in echo statements (em-dashes, smart quotes, accented chars in package
REM names, etc.) renders correctly. Default Windows cmd uses OEM codepages
REM (CP437 / CP850 / etc.) that mangle UTF-8 bytes into garbage characters.
REM Suppressing chcp's "Active code page" stdout line - it would just be noise.
chcp 65001 >nul

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
REM
REM IMPORTANT: this is a single line — no `^` continuation. Multi-line cmd
REM commands with `^` silently break when the .bat has Unix line endings
REM (which can happen if downloaded raw and the file's EOLs get mangled).
REM Keeping it on one line is more verbose but bulletproof.
powershell.exe -ExecutionPolicy Bypass -NoProfile -Command "$env:CLAYRUNE_PROMPT_URL = 'https://raw.githubusercontent.com/ronle/mission-control/master/installer/install-prompt.md'; iwr https://raw.githubusercontent.com/ronle/mission-control/master/installer/install.ps1 -useb | iex"

set "PSEXIT=%ERRORLEVEL%"

echo.
echo ============================================================
if "%PSEXIT%"=="0" goto :success

echo   Installer paused.
echo.
echo   Most often this means Claude CLI isn't logged in yet. The full
echo   output above shows what happened. We can handle the login for
echo   you - just pick L below.
echo.
echo ============================================================
echo.
echo   What now?
echo     [L] Log me in to Claude now ^(opens browser, then re-runs installer^)
echo     [R] Retry the installer ^(if you've already fixed the issue^)
echo     [Q] Quit and close this window
echo.

:choice_loop
set "choice="
set /p choice="Press L, R, or Q then Enter: "
if /i "%choice%"=="L" goto :do_login
if /i "%choice%"=="R" goto :run_installer
if /i "%choice%"=="Q" goto :end
echo Please enter L, R, or Q.
goto :choice_loop

:do_login
echo.
echo ============================================================
echo   Launching Claude login in a new window
echo ============================================================
echo.
echo A second cmd window is about to open with `claude /login` running.
echo Inside that window:
echo   1. A browser will open. Sign in with your Anthropic account
echo      ^(Claude Pro/Max OAuth^), or paste an API key when prompted.
echo   2. When you see "Logged in successfully", type:  exit
echo   3. The login window will close on its own.
echo.
echo This window will keep running and pick up where you left off.
echo.
pause

REM Spawn claude /login in a SEPARATE cmd window with start /WAIT. We block
REM here until that window closes. Crucially: our window can't be affected
REM by anything claude does — if claude crashes / detaches / closes its own
REM parent cmd, only the spawned window dies, and start /WAIT returns control
REM to us cleanly. Calling `call claude /login` directly was vulnerable to
REM the spawned process terminating our cmd in some edge cases.
start "Clayrune - Claude Login" /WAIT cmd /c "claude /login"

echo.
echo ============================================================
echo Login window closed. Press any key to retry the installer ^(or
echo Ctrl+C if you want to abort instead^).
echo ============================================================
pause >nul
goto :run_installer

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
