# Mission Control - Windows Build Instructions

This document provides step-by-step instructions for building the Mission Control Windows executable bundle.

## Prerequisites

### Required Software
- **Python 3.11** (recommended) or Python 3.12
  - Python 3.14+ may have compatibility issues with pythonnet
- **.NET 6.0+ Desktop Runtime** on the build machine
- **WebView2 Runtime** (usually pre-installed on Windows 10/11)

### Required Python Packages
```
flask>=3.0.0
pywebview>=5.0
pythonnet>=3.0.0
pyinstaller
```

## Build Process

### Step 1: Install Dependencies

```batch
pip install flask pywebview pythonnet pyinstaller
```

Or install from requirements.txt:
```batch
pip install -r requirements.txt
pip install pyinstaller
```

### Step 2: Run Pre-Build Fixes

**CRITICAL:** This step must be run before every PyInstaller build. It patches pywebview for .NET 6+ compatibility.

```batch
python pre_build_fix.py
```

This script fixes four issues:
1. Replaces the .NET Framework WinForms DLL with .NET Core variant
2. Writes Python.Runtime.runtimeconfig.json for .NET version roll-forward
3. Patches SystemEvents assembly reference (split from WinForms in .NET 6)
4. Patches OpenFolderDialog class for missing internal .NET types

Expected output:
```
[pre-build] WinForms DLL already targets .NETCoreApp — OK
[pre-build] Wrote .../Python.Runtime.runtimeconfig.json
[pre-build] winforms.py already patched for SystemEvents — OK
[pre-build] winforms.py already patched for OpenFolderDialog — OK
[pre-build] All fixes applied. Safe to run PyInstaller.
```

### Step 3: Build with PyInstaller

```batch
pyinstaller build.spec --noconfirm
```

Or use the build script which does Steps 1-3:
```batch
build.bat
```

### Step 4: Verify Build Output

The build output is located at:
```
dist/MissionControl/MissionControl.exe
```

Test the executable:
```batch
cd dist\MissionControl
MissionControl.exe
```

A native window should appear (not a browser).

## Creating a GitHub Release

### 1. Build the zip

```batch
python pre_build_fix.py && pyinstaller build.spec --noconfirm

:: Create release zip (PowerShell)
powershell Compress-Archive -Path dist\MissionControl -DestinationPath dist\MissionControl-Windows.zip -Force
```

### 2. Tag and upload

```batch
git tag v<VERSION>
git push origin master --tags
gh release create v<VERSION> dist/MissionControl-Windows.zip --title "v<VERSION> — <title>" --notes "<notes>"
```

### 3. Verify the bundle before uploading

Confirm these exist in `dist/MissionControl/_internal/`:
- `pythonnet/runtime/Python.Runtime.dll`
- `pythonnet/runtime/Python.Runtime.runtimeconfig.json`
- `clr_loader/` directory with `hostfxr.py`, `ffi/`, etc.

And confirm these modules appear in `build/build/xref-build.html`:
- `webview.platforms.winforms`
- `webview.platforms.edgechromium`
- `webview.guilib`

If any are missing, the native window will silently fall back to browser mode.

### Windows SmartScreen

The exe is not code-signed, so Windows will show a "Windows protected your PC" warning on first launch. Users need to click **"More info" → "Run anyway"**. This is expected and noted in the release description.

## Creating the Installer (Optional)

If you want to create a Windows installer:

1. Install [Inno Setup](https://jrsoftware.org/isinfo.php)
2. Run: `iscc installer.iss`
3. Output: `installer_output/MissionControlSetup.exe`

## Troubleshooting

### Native window doesn't appear (opens browser instead)

1. **Check .NET Desktop Runtime:**
   ```batch
   dotnet --list-runtimes
   ```
   Should show `Microsoft.WindowsDesktop.App 6.x` or higher.

2. **Re-run pre_build_fix.py:** The patches may not have been applied.

3. **Verify webview.platforms.winforms is bundled:** Open `build/build/xref-build.html` and search for `webview.platforms.winforms`. If missing, the `build.spec` is not using `collect_submodules('webview')`. This is the most common cause — pywebview's `guilib.py` imports `webview.platforms.winforms` dynamically inside a function, which PyInstaller cannot detect via static analysis. The `build.spec` must use `collect_submodules()` (not a manual hiddenimports list) to catch this.

4. **Check WinForms DLL variant:**
   ```python
   import site
   for sp in site.getsitepackages():
       import os
       dll = os.path.join(sp, 'webview', 'lib', 'Microsoft.Web.WebView2.WinForms.dll')
       if os.path.exists(dll):
           with open(dll, 'rb') as f:
               data = f.read()
           if b'.NETCoreApp' in data:
               print('DLL is correct (.NETCoreApp)')
           else:
               print('DLL is wrong (.NETFramework) - run pre_build_fix.py')
           break
   ```

### pythonnet import fails

Ensure pythonnet is installed:
```batch
pip install pythonnet>=3.0.0
```

### Build fails with Python 3.14+

Use Python 3.11 or 3.12 instead. pythonnet may not have wheels for newer Python versions.

## Build Script Summary

For a CLI agent, the complete build sequence is:

```batch
cd /path/to/MissionControl

# Install dependencies
pip install -r requirements.txt
pip install pyinstaller

# Apply .NET compatibility patches
python pre_build_fix.py

# Build the executable
pyinstaller build.spec --noconfirm

# Output is at: dist/MissionControl/MissionControl.exe
```

## Critical: build.spec Hidden Imports

The `build.spec` uses `collect_submodules('webview')` and `collect_submodules('clr_loader')` to automatically discover and bundle all submodules. **Do not replace these with a manual hiddenimports list.** The key reason:

- `guilib.py` imports `webview.platforms.winforms` **dynamically inside a function** — PyInstaller's static analysis cannot detect it
- `winforms.py` conditionally imports `webview.platforms.edgechromium` at module level
- `clr_loader` has submodules (`ffi`, `hostfxr`) that are loaded dynamically by pythonnet

Without `collect_submodules`, the native window silently fails and the app falls back to browser mode with no visible error.

## What pre_build_fix.py Does

The script modifies files in Python's site-packages to fix .NET 6+ compatibility:

| Fix | File Modified | Problem Solved |
|-----|---------------|----------------|
| 1. WinForms DLL | `webview/lib/Microsoft.Web.WebView2.WinForms.dll` | Wrong DLL variant bundled |
| 2. RuntimeConfig | `pythonnet/runtime/Python.Runtime.runtimeconfig.json` | .NET version roll-forward |
| 3. SystemEvents | `webview/platforms/winforms.py` | Assembly split in .NET 6 |
| 4. OpenFolderDialog | `webview/platforms/winforms.py` | Internal types missing in .NET 6+ |

**Note:** These patches are applied to the installed packages, not the repo. They must be re-applied after:
- Reinstalling pywebview or pythonnet
- Creating a new virtual environment
- Upgrading Python

## Files Reference

| File | Purpose |
|------|---------|
| `requirements.txt` | Python dependencies |
| `build.spec` | PyInstaller configuration |
| `build.bat` | Windows build script |
| `pre_build_fix.py` | .NET compatibility patches |
| `installer.iss` | Inno Setup installer script |
| `app.py` | Desktop entry point (pywebview) |
| `server.py` | Flask backend |
