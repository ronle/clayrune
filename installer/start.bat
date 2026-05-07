@echo off
REM Clayrune launcher (Windows)
REM Activates the venv, starts the Flask server, opens the browser.
REM Invoked by the Clayrune.lnk shortcut on the Desktop / in the Start Menu.

setlocal

REM Resolve the install directory (parent of this script's directory).
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
for %%I in ("%SCRIPT_DIR%") do set "CLAYRUNE_DIR=%%~dpI"
set "CLAYRUNE_DIR=%CLAYRUNE_DIR:~0,-1%"

cd /d "%CLAYRUNE_DIR%"

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
