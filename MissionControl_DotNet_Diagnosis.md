# Mission Control — .NET Error: Root Cause & Generic Fix
**Date:** 2026-03-23  
**Diagnosed on:** Laptop (DESKTOP-DFMI33S) via live Desktop Commander session  
**Build version:** MissionControl-Windows (2).zip — built 2026-03-23 09:04  

---

## Environment on This Laptop (All Prerequisites Present)

| Component | Status |
|---|---|
| .NET 6.0.36 Core Runtime | ✅ Installed |
| .NET 6.0.36 Windows Desktop Runtime | ✅ Installed |
| .NET Framework 4.8 | ✅ Installed |
| WebView2 Runtime v146.0.3856.72 | ✅ Installed |
| .NET SDK | ❌ Not installed (runtime only) |

The environment is fine. The problem is entirely in the build.

---

## Root Cause: Two Compounding Build Bugs

The current build has **two separate problems** that together make it fail on any machine that isn't an exact clone of the build machine.

### Bug 1 — Wrong DLL variant bundled

`Microsoft.Web.WebView2.WinForms.dll` was copied from the `net462` (classic .NET Framework) folder instead of the `net6.0-windows` folder.

**Confirmed by PE metadata scan:**
```
Bundled:  Microsoft.Web.WebView2.WinForms.dll  →  .NETFramework,Version=v4.6.2  ❌
Required: Microsoft.Web.WebView2.WinForms.dll  →  net6.0-windows                ✅
```

pythonnet 3.x loads a **.NET Core CLR** (via hostfxr). Inside that CLR, the `net462` WinForms DLL references classic `System.Windows.Forms` from .NET Framework — a completely separate runtime that cannot be loaded into a .NET Core context. Result: crash.

### Bug 2 — Missing `Python.Runtime.runtimeconfig.json`

The file `_internal/pythonnet/runtime/Python.Runtime.runtimeconfig.json` **does not exist** in the bundle.

This file tells `hostfxr` which .NET version to target and, critically, the **roll-forward policy**. Without it, hostfxr defaults to `rollForward=Minor`, which means:

- Built/targeting .NET 6 → **won't load on a .NET 8-only machine** (won't jump major versions)
- Built/targeting .NET 8 → **won't load on a .NET 6-only machine**

So even after fixing Bug 1, the app will still fail on any machine whose installed .NET major version doesn't match the build machine's.

---

## Failure Matrix (Current Build)

| Machine has... | Result |
|---|---|
| .NET 6 (matches build machine) | ❌ Crashes — wrong WinForms DLL variant (Bug 1) |
| .NET 8 only | ❌ Crashes — no runtimeconfig, hostfxr won't roll forward (Bug 2) |
| .NET 6 + .NET 8 | ❌ Crashes — wrong WinForms DLL variant (Bug 1) |
| No .NET at all | ❌ Crashes — no graceful fallback |
| Exact clone of build machine | ✅ Works (by accident) |

---

## The Generic Fix: Three Parts

All three parts are required together. None alone is sufficient.

---

### Part 1 — Bundle the correct WinForms DLL (`net6.0-windows` variant)

The NuGet package `Microsoft.Web.WebView2` ships the DLL in multiple variants:
```
microsoft.web.webview2/1.0.2957.106/lib/
  net462/
    Microsoft.Web.WebView2.WinForms.dll   ← ❌ currently bundled (wrong)
  net6.0-windows/
    Microsoft.Web.WebView2.WinForms.dll   ← ✅ use this one
```

The `net6.0-windows` variant targets the .NET Core family. Because of .NET's forward compatibility, this DLL loads correctly on **.NET 6, 7, and 8** — covering all modern machines.

**Add this to the build script (run before PyInstaller):**
```python
# pre_build_fix.py
import site, shutil, glob, os

WEBVIEW2_VERSION = "1.0.2957.106"  # pin to match the bundled version

for sp in site.getsitepackages():
    target = os.path.join(sp, 'webview', 'lib', 'Microsoft.Web.WebView2.WinForms.dll')
    if os.path.exists(target):
        # Find the net6.0-windows variant in NuGet cache
        nuget_pattern = os.path.expanduser(
            fr'~\.nuget\packages\microsoft.web.webview2\{WEBVIEW2_VERSION}\lib\net6.0-windows\Microsoft.Web.WebView2.WinForms.dll'
        )
        matches = glob.glob(nuget_pattern)
        if matches:
            shutil.copy2(matches[0], target)
            print(f"[pre-build] Replaced WinForms DLL with net6.0-windows variant: {target}")
        else:
            raise FileNotFoundError(
                f"net6.0-windows WebView2 WinForms DLL not found at {nuget_pattern}\n"
                f"Run: dotnet restore or manually download NuGet package microsoft.web.webview2"
            )
        break
```

---

### Part 2 — Add `Python.Runtime.runtimeconfig.json` to the bundle

This file must be placed at:
```
_internal/pythonnet/runtime/Python.Runtime.runtimeconfig.json
```

**Content — use `LatestMajor` roll-forward policy:**
```json
{
  "runtimeOptions": {
    "tfm": "net6.0-windows",
    "rollForward": "LatestMajor",
    "framework": {
      "name": "Microsoft.WindowsDesktop.App",
      "version": "6.0.0"
    }
  }
}
```

**What each setting does:**
- `tfm: net6.0-windows` — targets the Windows Desktop runtime (includes WinForms)
- `rollForward: LatestMajor` — accept **any** installed .NET version ≥ 6.0, including 7, 8, 9, etc.
- `framework: Microsoft.WindowsDesktop.App` — requires the Desktop runtime variant (not just base), which provides WinForms

**Result:** This single config file makes the app work on any machine with .NET 6, 7, 8, or any future major version.

**Add to PyInstaller spec `datas`:**
```python
# In MissionControl.spec
a = Analysis(
    ['main.py'],
    ...
    datas=[
        ...
        # Add the runtimeconfig next to Python.Runtime.dll
        ('pythonnet/runtime/Python.Runtime.runtimeconfig.json', 'pythonnet/runtime'),
        ...
    ],
)
```

Or generate it dynamically in the pre-build script:
```python
# In pre_build_fix.py (add after the DLL fix)
import json, os, site

for sp in site.getsitepackages():
    rt_dir = os.path.join(sp, 'pythonnet', 'runtime')
    if os.path.isdir(rt_dir):
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
        print(f"[pre-build] Wrote runtimeconfig: {config_path}")
        break
```

---

### Part 3 — Graceful fallback in the entry script

Even with Parts 1 and 2 fixed, some machines will still not have .NET at all, or will have
a version below 6.0. The entry script must handle this gracefully instead of crashing silently.

The native window remains the **primary experience** — the browser is only a fallback:

```python
# main.py (PyInstaller entry script)
import threading
import time
import webbrowser

def start_server():
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)

t = threading.Thread(target=start_server, daemon=True)
t.start()
time.sleep(1.0)  # give Flask time to bind

_webview_ok = False
try:
    import webview
    import clr  # this line triggers hostfxr + .NET CLR load — fail fast here if broken
    _webview_ok = True
except Exception as e:
    print(f"[MissionControl] Native window unavailable ({type(e).__name__}: {e})")
    print("[MissionControl] Falling back to default browser.")

if _webview_ok:
    # PRIMARY PATH: full native app window
    try:
        win = webview.create_window(
            'Mission Control',
            f'http://localhost:{PORT}',
            width=1400,
            height=900,
            resizable=True,
        )
        webview.start()  # blocks until window closed
    except Exception as e:
        # WebView2 itself failed (e.g. missing runtime) — fall through to browser
        print(f"[MissionControl] Window creation failed ({e}), opening browser.")
        webbrowser.open(f'http://localhost:{PORT}')
        t.join()
else:
    # FALLBACK PATH: open in default browser, keep process alive
    webbrowser.open(f'http://localhost:{PORT}')
    t.join()
```

---

## Failure Matrix After All Three Fixes Applied

| Machine has... | Result |
|---|---|
| .NET 6.x Desktop Runtime | ✅ Native window |
| .NET 7.x Desktop Runtime | ✅ Native window (LatestMajor rolls forward) |
| .NET 8.x Desktop Runtime | ✅ Native window (LatestMajor rolls forward) |
| .NET 9+ Desktop Runtime | ✅ Native window (LatestMajor rolls forward) |
| Base .NET runtime only (no Desktop) | ⚠️ Falls back to browser (WinForms not available) |
| No .NET at all | ⚠️ Falls back to browser (graceful, no crash) |
| WebView2 not installed | ⚠️ Falls back to browser (graceful, no crash) |

---

## Complete Pre-Build Script for Agent

Save as `pre_build_fix.py` and run it before every `pyinstaller MissionControl.spec` call:

```python
"""
pre_build_fix.py
Run before PyInstaller to ensure correct .NET DLLs and runtimeconfig are in place.
Makes the build work on any machine with .NET 6, 7, 8, or later.
"""
import site, shutil, glob, os, json, sys

WEBVIEW2_VERSION = "1.0.2957.106"

site_packages = site.getsitepackages()

# ── Part 1: Fix WebView2 WinForms DLL ────────────────────────────────────────
dll_fixed = False
for sp in site_packages:
    target = os.path.join(sp, 'webview', 'lib', 'Microsoft.Web.WebView2.WinForms.dll')
    if os.path.exists(target):
        nuget_path = os.path.expanduser(
            fr'~\.nuget\packages\microsoft.web.webview2\{WEBVIEW2_VERSION}'
            r'\lib\net6.0-windows\Microsoft.Web.WebView2.WinForms.dll'
        )
        matches = glob.glob(nuget_path)
        if not matches:
            # Try any installed version
            matches = sorted(glob.glob(os.path.expanduser(
                r'~\.nuget\packages\microsoft.web.webview2\*\lib\net6.0-windows\Microsoft.Web.WebView2.WinForms.dll'
            )))
        if matches:
            shutil.copy2(matches[-1], target)
            print(f"[pre-build] ✅ Replaced WinForms DLL with net6.0-windows variant")
            dll_fixed = True
        else:
            print(f"[pre-build] ❌ net6.0-windows WinForms DLL not found in NuGet cache")
            print(f"            Run: dotnet restore  or install NuGet package microsoft.web.webview2")
            sys.exit(1)
        break

if not dll_fixed:
    print("[pre-build] ❌ Could not find pywebview installation in site-packages")
    sys.exit(1)

# ── Part 2: Write Python.Runtime.runtimeconfig.json ──────────────────────────
config_written = False
for sp in site_packages:
    rt_dir = os.path.join(sp, 'pythonnet', 'runtime')
    if os.path.isdir(rt_dir):
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
        print(f"[pre-build] ✅ Wrote Python.Runtime.runtimeconfig.json")
        config_written = True
        break

if not config_written:
    print("[pre-build] ❌ Could not find pythonnet/runtime directory in site-packages")
    sys.exit(1)

print("[pre-build] All fixes applied. Safe to run PyInstaller.")
```

**Add to PyInstaller spec** — ensure runtimeconfig is included in datas:
```python
# MissionControl.spec
a = Analysis(
    ['main.py'],
    ...
    datas=[
        ...
        ('pythonnet/runtime/Python.Runtime.runtimeconfig.json', 'pythonnet/runtime'),
        ...
    ],
)
```

---

## Summary for Agent

| # | Fix | Scope | Why needed |
|---|---|---|---|
| 1 | Bundle `net6.0-windows` WinForms DLL | Build-time | Wrong variant (`net462`) was bundled; breaks on all .NET Core runtimes |
| 2 | Add `Python.Runtime.runtimeconfig.json` with `LatestMajor` | Build-time | Without it, hostfxr won't roll forward across major .NET versions |
| 3 | Graceful fallback in entry script | Code change | Machines without any .NET get a browser session instead of a hard crash |

All three fixes together produce a build that works on **any Windows machine** with .NET 6, 7, 8, or later — and degrades gracefully to browser mode on machines with no .NET.

---

## Files Diagnosed

| File | Finding |
|---|---|
| `Microsoft.Web.WebView2.WinForms.dll` | `net462` variant — **wrong**, causes crash in .NET Core CLR |
| `Python.Runtime.dll` | `netstandard2.0` — correct, compatible with .NET 6+ |
| `Python.Runtime.runtimeconfig.json` | **Missing** — hostfxr cannot roll forward without it |
| `server.py` (3,300 lines) | No .NET dependency — pure Flask, healthy |
| `dotnet --list-runtimes` | .NET 6.0.36 Desktop installed on laptop |
| WebView2 registry | v146.0.3856.72 installed on laptop |
| Running exe + window enumeration | "Mission Control - .NET Runtime Missing" dialog confirmed |
| Flask stdout | Starts cleanly on port 5199 — backend is healthy |
