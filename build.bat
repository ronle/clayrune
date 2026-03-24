@echo off
setlocal
echo ========================================
echo  Mission Control — Build Script
echo ========================================
echo.

:: Install build dependencies
echo [1/3] Installing build dependencies...
pip install pyinstaller pywebview flask pythonnet
if %ERRORLEVEL% neq 0 (
    echo ERROR: pip install failed.
    exit /b 1
)
echo.

:: Run pre-build fixes (DLL variants + runtimeconfig)
echo [2/3] Running pre-build fixes...
python pre_build_fix.py
if %ERRORLEVEL% neq 0 (
    echo ERROR: Pre-build fixes failed.
    exit /b 1
)
echo.

:: Run PyInstaller
echo [3/3] Building with PyInstaller...
pyinstaller build.spec --noconfirm
if %ERRORLEVEL% neq 0 (
    echo ERROR: PyInstaller build failed.
    exit /b 1
)
echo.

echo ========================================
echo  Build complete!
echo ========================================
echo.
echo Output: dist\MissionControl\MissionControl.exe
echo.
echo To create the installer:
echo   1. Install Inno Setup from https://jrsoftware.org/isinfo.php
echo   2. Run: iscc installer.iss
echo   3. Installer will be at: installer_output\MissionControlSetup.exe
echo.
