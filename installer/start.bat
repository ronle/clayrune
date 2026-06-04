@echo off
REM Clayrune launcher (Windows)
REM Activates the venv, starts the Flask server, opens the browser.
REM Invoked by the Clayrune.lnk shortcut on the Desktop / in the Start Menu.

REM Set the cmd window's title so the taskbar entry shows "Clayrune" instead
REM of the path of the bat file.
title Clayrune

setlocal

REM Resolve the install directory (parent of this script's directory).
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
for %%I in ("%SCRIPT_DIR%") do set "CLAYRUNE_DIR=%%~dpI"
set "CLAYRUNE_DIR=%CLAYRUNE_DIR:~0,-1%"

cd /d "%CLAYRUNE_DIR%"

REM Replace cmd.exe's default icon on this console window with clayrune.ico.
REM Without this, the running window's taskbar entry uses cmd.exe's icon
REM (looks like a generic black box) — even though the .lnk we click was
REM correctly labeled with the Clayrune icon. The .ps1 helper sends
REM WM_SETICON via Win32 to swap it in-place. Failure is silent: worst case
REM the cmd window keeps cmd.exe's default icon.
REM Only set the console icon when there's actually a visible console
REM (foreground / dev launch). When started windowless via start-hidden.vbs
REM (CLAYRUNE_HIDDEN=1) there's no console to icon, and spawning powershell
REM would briefly flash a window — which defeats the point — so skip it.
if not defined CLAYRUNE_HIDDEN (
    if exist "assets\clayrune.ico" (
        powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%CLAYRUNE_DIR%\installer\set-console-icon.ps1" -IconPath "%CLAYRUNE_DIR%\assets\clayrune.ico" >nul 2>&1
    )
)

if not exist ".venv\Scripts\activate.bat" (
    echo [Clayrune] No .venv found at %CLAYRUNE_DIR%\.venv
    echo [Clayrune] Re-run the installer in PowerShell:
    echo [Clayrune]   iwr https://clayrune.io/install.ps1 -useb ^| iex
    REM Don't pause when windowless — there's no console to show the prompt,
    REM so a pause would hang invisibly forever.
    if not defined CLAYRUNE_HIDDEN pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"

echo [Clayrune] Starting server on http://localhost:5199

REM Open the browser — server bind takes a beat. Browsers retry connection-refused.
start "" "http://localhost:5199"

REM Always keep a log directory available for the windowless launch path.
if not exist "%CLAYRUNE_DIR%\data\logs" mkdir "%CLAYRUNE_DIR%\data\logs"

if defined CLAYRUNE_HIDDEN (
    REM Windowless launch (end users, via start-hidden.vbs): no console exists
    REM to show logs, so persist them to a file for support / debugging.
    python server.py >> "%CLAYRUNE_DIR%\data\logs\clayrune.log" 2>&1
) else (
    REM Foreground / developer launch: stream logs to this console.
    REM Closing this window stops the server.
    python server.py
)
