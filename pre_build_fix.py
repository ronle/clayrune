"""
pre_build_fix.py — Run before PyInstaller to fix .NET compatibility issues.

Fixes two build bugs that cause pywebview to crash on target machines:
1. Replaces net462 WinForms DLL with netcoreapp3.0 variant (compatible with .NET 6+)
2. Writes Python.Runtime.runtimeconfig.json with LatestMajor roll-forward policy

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

        # Try NuGet cache first
        nuget_pattern = os.path.expanduser(
            r'~\.nuget\packages\microsoft.web.webview2'
        )
        netcore_dll = None
        if os.path.isdir(nuget_pattern):
            for root, dirs, files in os.walk(nuget_pattern):
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

                for name in z.namelist():
                    if 'netcoreapp' in name and 'WinForms.dll' in name:
                        tmp_dir = os.path.join(os.environ.get('TEMP', '/tmp'), 'webview2_fix')
                        os.makedirs(tmp_dir, exist_ok=True)
                        z.extract(name, tmp_dir)
                        netcore_dll = os.path.join(tmp_dir, name)
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
                "tfm": "net6.0",
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


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    ok1 = fix_winforms_dll()
    ok2 = write_runtimeconfig()

    if ok1 and ok2:
        print("[pre-build] All fixes applied. Safe to run PyInstaller.")
    else:
        print("[pre-build] Some fixes failed — build may not work on all machines.")
        sys.exit(1)
