# Mission Control — Installation Diagnosis: Full History
**Last updated:** 2026-03-23  
**Diagnosed on:** Laptop (DESKTOP-DFMI33S) via live Desktop Commander session  

---

## Status Summary

| Build | .NET Error | Native Window | Root Cause |
|---|---|---|---|
| v1 (MissionControl-Windows (2).zip) | ❌ Yes | ❌ No | Wrong WinForms DLL variant + missing runtimeconfig |
| v2 (MissionControl-Windows.zip) | ✅ Fixed | ❌ Still missing | webview.start() called from wrong thread |

---

## Environment on This Laptop (All Prerequisites Present)

| Component | Status |
|---|---|
| .NET 6.0.36 Core + Windows Desktop Runtime | ✅ |
| .NET Framework 4.8 | ✅ |
| WebView2 Runtime v146.0.3856.72 | ✅ |

The environment is fine across all builds. All problems are in the build.

---

## Build v1 Root Cause (Fixed in v2)

Two compounding bugs caused the `.NET Runtime Missing` error dialog:

**Bug 1 — Wrong WebView2 WinForms DLL variant**
`Microsoft.Web.WebView2.WinForms.dll` was the `net462` variant.
pythonnet 3.x loads a .NET Core CLR — `net462` assemblies reference classic .NET Framework and cannot load in that context.

**Bug 2 — Missing `Python.Runtime.runtimeconfig.json`**
Without this file, `hostfxr` uses `rollForward=Minor` and won't cross major .NET versions.
A net6-targeting app fails on net8-only machines, and vice versa.

Both were confirmed via live PE metadata scans and dotnet CLI inspection.

---

## Build v2 Root Cause: webview.start() Threading Violation

### What was fixed correctly in v2
- ✅ WinForms DLL TFM changed from `net462` → `netcoreapp3.0` (partial improvement, see note below)
- ✅ `Python.Runtime.runtimeconfig.json` now present with `rollForward: LatestMajor`
- ✅ No more .NET error dialog

### What is still broken in v2
**`webview.start()` is being called from a non-main thread.**

**Evidence from live diagnostic:**
- Flask starts and serves requests ✅
- No `[MissionControl] Native window unavailable` printed — `import webview` and `import clr` both pass ✅
- Browser opens (fallback `webbrowser.open()` ran) — this means the main thread fell through past webview
- No native window appears — `webview.start()` returned immediately without creating a window

**Why this causes silent failure:**
`webview.start()` on Windows runs the Win32 message loop. This **must execute on the main thread**. If called from any other thread, it returns immediately with no exception and no window. The process then falls through to `webbrowser.open()`, which is why the browser opens instead.

---

## The Correct Entry Script Structure

The rule is simple: **`webview.start()` must be the last blocking call on the main thread.**

```python
# main.py — correct structure
import threading
import time
import webbrowser

def start_server():
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)

# Flask MUST run on a daemon thread (dies when main thread exits)
t = threading.Thread(target=start_server, daemon=True)
t.start()
time.sleep(1.0)  # give Flask time to bind before UI opens

_webview_ok = False
try:
    import webview
    import clr          # triggers .NET CLR load — raises here if .NET is broken
    _webview_ok = True
except Exception as e:
    print(f"[MissionControl] Native window unavailable ({type(e).__name__}: {e})")
    print("[MissionControl] Falling back to browser.")

if _webview_ok:
    # PRIMARY PATH: native app window
    # webview.create_window and webview.start() MUST be on the main thread
    try:
        win = webview.create_window(
            'Mission Control',
            f'http://localhost:{PORT}',
            width=1400,
            height=900,
            resizable=True,
        )
        webview.start()     # BLOCKS here — runs Win32 message loop on main thread
                            # Returns only when the window is closed
        # Process exits naturally here; Flask thread is daemon so it dies too
    except Exception as e:
        print(f"[MissionControl] Window creation failed ({type(e).__name__}: {e})")
        print("[MissionControl] Falling back to browser.")
        webbrowser.open(f'http://localhost:{PORT}')
        t.join()
else:
    # FALLBACK PATH: browser only
    webbrowser.open(f'http://localhost:{PORT}')
    t.join()    # keep process alive — Flask thread is daemon, needs main thread to stay up
```

### What NOT to do (common mistakes)

```python
# ❌ WRONG — webview in a thread
threading.Thread(target=lambda: webview.start()).start()
# Result: returns immediately, no window, no error

# ❌ WRONG — webview.start() followed by other code on main thread
webview.start()
webbrowser.open(...)   # this runs immediately because webview.start() returned instantly
t.join()

# ❌ WRONG — webview called before server is ready
import webview
webview.create_window(...)
webview.start()        # Flask hasn't started yet, window loads blank page
t = threading.Thread(target=start_server, daemon=True)
t.start()

# ✅ CORRECT — main thread structure
t = threading.Thread(target=start_server, daemon=True)
t.start()
time.sleep(1.0)        # wait for Flask
# ... try/except for clr/webview import ...
webview.start()        # LAST thing on main thread, blocks until window closed
```

---

## Note on WinForms DLL Variant (Still Not Ideal in v2)

The v2 build uses `netcoreapp3.0` for `Microsoft.Web.WebView2.WinForms.dll`.
This is better than `net462` and may work, but the correct target is `net6.0-windows`.

The NuGet package `microsoft.web.webview2` provides three variants:
```
net462/                  ← classic .NET Framework  (wrong)
netcoreapp3.0/           ← .NET Core 3.0           (works but not ideal)
net6.0-windows/          ← .NET 6 Windows          (correct — use this)
```

The `net6.0-windows` variant explicitly targets the Windows Desktop App framework,
matches the `runtimeconfig.json` settings, and is the officially supported variant
for use with pythonnet 3.x on .NET 6+.

**Pre-build fix script** (run before PyInstaller on the build machine):

```python
# pre_build_fix.py
import site, shutil, glob, os, json, sys

site_packages = site.getsitepackages()

# ── Fix 1: WebView2 WinForms DLL → net6.0-windows variant ───────────────────
dll_fixed = False
for sp in site_packages:
    target = os.path.join(sp, 'webview', 'lib', 'Microsoft.Web.WebView2.WinForms.dll')
    if os.path.exists(target):
        # Find net6.0-windows variant in NuGet cache (any installed version)
        matches = sorted(glob.glob(os.path.expanduser(
            r'~\.nuget\packages\microsoft.web.webview2\*\lib\net6.0-windows\Microsoft.Web.WebView2.WinForms.dll'
        )))
        if matches:
            shutil.copy2(matches[-1], target)
            print(f"[pre-build] ✅ Replaced WinForms DLL with net6.0-windows variant")
            dll_fixed = True
        else:
            print("[pre-build] ❌ net6.0-windows WinForms DLL not found in NuGet cache")
            sys.exit(1)
        break

# ── Fix 2: Write Python.Runtime.runtimeconfig.json ──────────────────────────
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
        break

print("[pre-build] All fixes applied. Safe to run PyInstaller.")
```

---

## Compatibility Matrix (After All Fixes)

| Machine has... | Expected result |
|---|---|
| .NET 6.x Desktop Runtime | ✅ Native window |
| .NET 7.x Desktop Runtime | ✅ Native window (LatestMajor roll-forward) |
| .NET 8.x Desktop Runtime | ✅ Native window (LatestMajor roll-forward) |
| Base .NET only (no Desktop) | ⚠️ Falls back to browser gracefully |
| No .NET at all | ⚠️ Falls back to browser gracefully |
| WebView2 not installed | ⚠️ Falls back to browser gracefully |

---

## Files Inspected Across Both Diagnostic Sessions

| File | Finding |
|---|---|
| v1 `Microsoft.Web.WebView2.WinForms.dll` | `net462` — wrong, crashes in .NET Core CLR |
| v2 `Microsoft.Web.WebView2.WinForms.dll` | `netcoreapp3.0` — functional but not ideal; use `net6.0-windows` |
| v2 `Python.Runtime.runtimeconfig.json` | Present, `rollForward: LatestMajor` ✅ |
| v2 console output | Flask starts, no .NET error, browser opens — threading bug confirmed |
| `dotnet --list-runtimes` | .NET 6.0.36 Desktop installed on laptop |
| WebView2 registry | v146.0.3856.72 installed on laptop |
| v1 visible window enum | "Mission Control - .NET Runtime Missing" dialog |
| v2 visible window enum | Only "Mission Control - Google Chrome" — no native window |
