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
if exist "assets\clayrune.ico" (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%CLAYRUNE_DIR%\installer\set-console-icon.ps1" -IconPath "%CLAYRUNE_DIR%\assets\clayrune.ico" >nul 2>&1
)

if not exist ".venv\Scripts\activate.bat" (
    echo [Clayrune] No .venv found at %CLAYRUNE_DIR%\.venv
    echo [Clayrune] Re-run the installer in PowerShell:
    echo [Clayrune]   iwr https://clayrune.io/install.ps1 -useb ^| iex
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"

echo [Clayrune] Starting server on http://localhost:5199

REM Open the browser — server bind takes a beat. Browsers retry connection-refused.
start "" "http://localhost:5199"

REM Run the server in the foreground. Closing this window stops the server.
python server.py
