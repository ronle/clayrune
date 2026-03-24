"""
pre_build_fix.py — Run before PyInstaller to fix .NET compatibility issues.

Fixes four build bugs that cause pywebview to crash on target machines:
1. Replaces net462 WinForms DLL with netcoreapp3.0 variant (compatible with .NET 6+)
2. Writes Python.Runtime.runtimeconfig.json with LatestMajor roll-forward policy
3. Patches winforms.py to add clr.AddReference('Microsoft.Win32.SystemEvents')
   (split from WinForms assembly in .NET 6)
4. Patches OpenFolderDialog class to handle missing internal .NET types in .NET 6+

Usage:  python pre_build_fix.py && pyinstaller build.spec --noconfirm
"""

import site
import shutil
import os
import json
import sys
import urllib.request
import zipfile
import io

WEBVIEW2_NUGET_URL = "https://www.nuget.org/api/v2/package/Microsoft.Web.WebView2/1.0.2957.106"

site_packages = site.getsitepackages()


# ── Part 1: Fix WebView2 WinForms DLL ────────────────────────────────────────

def fix_winforms_dll():
    """Replace net462 WinForms DLL with netcoreapp3.0 variant."""
    for sp in site_packages:
        target = os.path.join(sp, 'webview', 'lib', 'Microsoft.Web.WebView2.WinForms.dll')
        if not os.path.exists(target):
            continue

        # Check if already the correct variant
        with open(target, 'rb') as f:
            data = f.read()
        if b'.NETCoreApp' in data:
            print("[pre-build] WinForms DLL already targets .NETCoreApp — OK")
            return True

        print("[pre-build] WinForms DLL targets .NETFramework (wrong) — replacing...")

        # Try NuGet cache first — prefer net6.0-windows, fall back to netcoreapp3.0
        nuget_base = os.path.expanduser(
            r'~\.nuget\packages\microsoft.web.webview2'
        )
        netcore_dll = None
        if os.path.isdir(nuget_base):
            # First pass: net6.0-windows (preferred)
            for root, dirs, files in os.walk(nuget_base):
                if 'net6.0-windows' in root and 'Microsoft.Web.WebView2.WinForms.dll' in files:
                    netcore_dll = os.path.join(root, 'Microsoft.Web.WebView2.WinForms.dll')
                    break
            # Second pass: netcoreapp3.0 (also compatible)
            if not netcore_dll:
                for root, dirs, files in os.walk(nuget_base):
                    if 'netcoreapp' in root and 'Microsoft.Web.WebView2.WinForms.dll' in files:
                        netcore_dll = os.path.join(root, 'Microsoft.Web.WebView2.WinForms.dll')
                        break

        if not netcore_dll:
            # Download from NuGet
            print("[pre-build] NuGet cache miss — downloading from NuGet.org...")
            try:
                req = urllib.request.Request(
                    WEBVIEW2_NUGET_URL,
                    headers={'User-Agent': 'MissionControl-Build/1.0'}
                )
                pkg_data = urllib.request.urlopen(req, timeout=30).read()
                z = zipfile.ZipFile(io.BytesIO(pkg_data))

                # Prefer net6.0-windows, fall back to netcoreapp3.0
                for prefix in ('net6.0-windows', 'netcoreapp'):
                    for name in z.namelist():
                        if prefix in name and 'WinForms.dll' in name:
                            tmp_dir = os.path.join(os.environ.get('TEMP', '/tmp'), 'webview2_fix')
                            os.makedirs(tmp_dir, exist_ok=True)
                            z.extract(name, tmp_dir)
                            netcore_dll = os.path.join(tmp_dir, name)
                            break
                    if netcore_dll:
                        break
            except Exception as e:
                print(f"[pre-build] Download failed: {e}")
                return False

        if netcore_dll and os.path.exists(netcore_dll):
            # Verify it's actually the .NETCoreApp variant
            with open(netcore_dll, 'rb') as f:
                verify = f.read()
            if b'.NETFramework' in verify:
                print("[pre-build] ERROR: Downloaded DLL is still net462!")
                return False

            shutil.copy2(netcore_dll, target)
            print(f"[pre-build] Replaced WinForms DLL with .NETCoreApp variant")
            return True
        else:
            print("[pre-build] ERROR: Could not find netcoreapp WinForms DLL")
            return False

    print("[pre-build] ERROR: Could not find pywebview in site-packages")
    return False


# ── Part 2: Write Python.Runtime.runtimeconfig.json ──────────────────────────

def write_runtimeconfig():
    """Write runtimeconfig.json next to Python.Runtime.dll."""
    for sp in site_packages:
        rt_dir = os.path.join(sp, 'pythonnet', 'runtime')
        if not os.path.isdir(rt_dir):
            continue

        config = {
            "runtimeOptions": {
                "tfm": "net6.0-windows",
                "rollForward": "LatestMajor",
                "framework": {
                    "name": "Microsoft.WindowsDesktop.App",
                    "version": "6.0.0"
                }
            }
        }
        config_path = os.path.join(rt_dir, 'Python.Runtime.runtimeconfig.json')
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
        print(f"[pre-build] Wrote {config_path}")
        return True

    print("[pre-build] ERROR: Could not find pythonnet/runtime in site-packages")
    return False


# ── Part 3: Patch winforms.py for .NET 6 SystemEvents assembly split ─────────

def patch_winforms_systemevents():
    """Add clr.AddReference('Microsoft.Win32.SystemEvents') before the import.

    In .NET 6, SystemEvents was split from System.Windows.Forms into its own
    assembly. pywebview's winforms.py doesn't reference it, so the import fails
    under CoreCLR.
    """
    for sp in site_packages:
        wf = os.path.join(sp, 'webview', 'platforms', 'winforms.py')
        if not os.path.exists(wf):
            continue

        with open(wf, 'r', encoding='utf-8') as f:
            content = f.read()

        marker = "clr.AddReference('Microsoft.Win32.SystemEvents')"
        if marker in content:
            print("[pre-build] winforms.py already patched for SystemEvents — OK")
            return True

        old = "from Microsoft.Win32 import SystemEvents"
        if old not in content:
            print("[pre-build] WARNING: could not find SystemEvents import in winforms.py")
            return False

        new = f"{marker}  # split from WinForms in .NET 6\n{old}"
        content = content.replace(old, new, 1)

        with open(wf, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"[pre-build] Patched {wf} — added SystemEvents assembly reference")
        return True

    print("[pre-build] ERROR: Could not find webview/platforms/winforms.py")
    return False


# ── Part 4: Patch OpenFolderDialog for .NET 6+ ────────────────────────────────

def patch_openfolderdialog():
    """Wrap OpenFolderDialog class in try/except to handle missing .NET types.

    In .NET 6+, the internal FileDialogNative+IFileDialog type doesn't exist,
    causing the class-level attribute access to fail with AttributeError.
    This patch wraps the class definition and provides a fallback implementation
    using FolderBrowserDialog.
    """
    for sp in site_packages:
        wf = os.path.join(sp, 'webview', 'platforms', 'winforms.py')
        if not os.path.exists(wf):
            continue

        with open(wf, 'r', encoding='utf-8') as f:
            content = f.read()

        # Check if already patched
        marker = "_OpenFolderDialog_available = False"
        if marker in content:
            print("[pre-build] winforms.py already patched for OpenFolderDialog — OK")
            return True

        # Find the OpenFolderDialog class definition
        old_class_start = "class OpenFolderDialog:"
        if old_class_start not in content:
            print("[pre-build] WARNING: could not find OpenFolderDialog class in winforms.py")
            return False

        # Find where the class ends (next class or module-level code)
        # The class ends at "_main_window_created = Event()"
        old_class_end_marker = "_main_window_created = Event()"
        if old_class_end_marker not in content:
            print("[pre-build] WARNING: could not find end of OpenFolderDialog class")
            return False

        # Extract the class definition
        class_start_idx = content.index(old_class_start)
        class_end_idx = content.index(old_class_end_marker)
        old_class = content[class_start_idx:class_end_idx]

        # Create the patched version with try/except wrapper
        patched_class = '''# OpenFolderDialog uses internal .NET Framework types that may not exist in .NET 6+
# Wrap in try/except to allow module import to succeed even if these types are missing
_OpenFolderDialog_available = False
try:
    ''' + old_class.replace('\n', '\n    ').rstrip() + '''
    _OpenFolderDialog_available = True
except (TypeError, AttributeError) as _e:
    # Provide a fallback class that uses standard FolderBrowserDialog
    class OpenFolderDialog:
        @classmethod
        def show(cls, parent=None, initialDirectory=None, allow_multiple=False, title=None):
            dialog = WinForms.FolderBrowserDialog()
            if initialDirectory:
                dialog.SelectedPath = initialDirectory
            if title:
                dialog.Description = title
            result = dialog.ShowDialog()
            if result == WinForms.DialogResult.OK:
                return (dialog.SelectedPath,)
            return None


'''
        # Replace old class with patched version
        new_content = content[:class_start_idx] + patched_class + content[class_end_idx:]

        with open(wf, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"[pre-build] Patched {wf} — wrapped OpenFolderDialog with try/except")
        return True

    print("[pre-build] ERROR: Could not find webview/platforms/winforms.py")
    return False


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    ok1 = fix_winforms_dll()
    ok2 = write_runtimeconfig()
    ok3 = patch_winforms_systemevents()
    ok4 = patch_openfolderdialog()

    if ok1 and ok2 and ok3 and ok4:
        print("[pre-build] All fixes applied. Safe to run PyInstaller.")
    else:
        print("[pre-build] Some fixes failed — build may not work on all machines.")
        sys.exit(1)
