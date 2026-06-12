# Clayrune app-window launcher (Windows).
#
# Invoked hidden by installer\start.bat once a Chromium browser is detected.
# Does three things, all best-effort:
#   1. Polls http://localhost:5199 until the server accepts connections
#      (an --app window shows a hard error page on connection-refused and
#      won't auto-retry, so we must not launch early).
#   2. Opens Clayrune as a standalone "--app=" window.
#   3. Stamps the new window's taskbar identity (AppUserModelID +
#      RelaunchIconResource + RelaunchDisplayNameResource) so the taskbar
#      button shows the Clayrune icon and relaunch name.
#
# Why step 3 exists: Chromium gives "--app=" windows a derived per-app
# AppUserModelID (own taskbar group), but the browsers differ in what icon
# that group advertises. Chrome leaves RelaunchIconResource empty, so the
# taskbar falls back to the window icon (= our favicon) — looks right.
# EDGE explicitly sets RelaunchIconResource to msedge.exe and the relaunch
# name to "Microsoft Edge", so on Edge-only machines (fresh Windows
# installs) the taskbar shows the Edge logo instead of Clayrune. Verified
# 2026-06-11 by reading both windows' property stores. Overwriting the
# window's property store from outside the browser process works and wins.
#
# Failure posture: identical to set-console-icon.ps1 — any error is
# swallowed; worst case the window opens with the browser's icon, which is
# exactly today's behaviour.
param(
    [Parameter(Mandatory = $true)][string]$Browser,
    [string]$Url = 'http://localhost:5199',
    [string]$IconPath = '',
    [string]$AppId = 'io.clayrune.app',   # matches the macOS bundle id
    [string]$DisplayName = 'Clayrune',
    [int]$PortTimeoutSec = 30,
    [int]$WindowTimeoutSec = 45
)

$ErrorActionPreference = 'SilentlyContinue'

# -- 1. Wait for the server port ---------------------------------------------
$uri = [Uri]$Url
$deadline = (Get-Date).AddSeconds($PortTimeoutSec)
while ((Get-Date) -lt $deadline) {
    try {
        $c = New-Object Net.Sockets.TcpClient
        $c.Connect($uri.Host, $uri.Port)
        $c.Close()
        break
    } catch { Start-Sleep -Milliseconds 200 }
}

# -- 2. Open the app window ---------------------------------------------------
try {
    Start-Process -FilePath $Browser -ArgumentList "--app=$Url", '--no-first-run', '--no-default-browser-check'
} catch { exit 0 }

# -- 3. Find the new window: stamp taskbar identity + maximize ----------------
$haveIcon = $IconPath -and (Test-Path $IconPath)
try {
    Add-Type @'
using System; using System.Text; using System.Collections.Generic; using System.Runtime.InteropServices;
public class ClayruneAppWindow {
  public delegate bool EnumProc(IntPtr h, IntPtr lp);
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc cb, IntPtr lp);
  [DllImport("user32.dll")] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);

  // Find a visible Chromium app window (class Chrome_WidgetWin_1) whose title
  // is exactly the app name (app windows have a bare title; normal browser
  // windows carry a " - Microsoft Edge" / " - Google Chrome" suffix).
  public static long Find(string title) {
    long found = 0;
    EnumWindows(delegate(IntPtr h, IntPtr lp) {
      if (!IsWindowVisible(h)) return true;
      var c = new StringBuilder(64); GetClassName(h, c, 64);
      if (c.ToString() != "Chrome_WidgetWin_1") return true;
      var t = new StringBuilder(256); GetWindowText(h, t, 256);
      if (t.ToString() != title) return true;
      found = h.ToInt64();
      return false;
    }, IntPtr.Zero);
    return found;
  }

  [DllImport("shell32.dll")] static extern int SHGetPropertyStoreForWindow(IntPtr hwnd, ref Guid iid, out IPropertyStore ps);
  [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IPropertyStore {
    int GetCount(out uint c); int GetAt(uint i, out PROPERTYKEY k);
    int GetValue(ref PROPERTYKEY k, out PROPVARIANT v);
    int SetValue(ref PROPERTYKEY k, ref PROPVARIANT v); int Commit();
  }
  [StructLayout(LayoutKind.Sequential)] struct PROPERTYKEY { public Guid fmtid; public uint pid; }
  // Only the VT_LPWSTR shape is needed for SetValue; the full PROPVARIANT
  // union is larger but SetValue only reads the bytes this layout covers.
  [StructLayout(LayoutKind.Sequential)] struct PROPVARIANT {
    public ushort vt; public ushort r1, r2, r3; public IntPtr p; public IntPtr p2;
  }
  static Guid IID = new Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99");
  static Guid FMTID = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3");
  // PKEY_AppUserModel_*: pid 5 = ID, 2 = RelaunchIconResource, 4 = RelaunchDisplayNameResource

  static int SetStr(IPropertyStore ps, uint pid, string val) {
    var k = new PROPERTYKEY { fmtid = FMTID, pid = pid };
    var v = new PROPVARIANT { vt = 31, p = Marshal.StringToCoTaskMemUni(val) }; // VT_LPWSTR
    int hr = ps.SetValue(ref k, ref v);
    Marshal.FreeCoTaskMem(v.p);
    return hr;
  }

  public static bool Stamp(long hwnd, string aumid, string iconRes, string displayName) {
    IPropertyStore ps;
    if (SHGetPropertyStoreForWindow(new IntPtr(hwnd), ref IID, out ps) != 0) return false;
    try {
      SetStr(ps, 5, aumid);
      SetStr(ps, 2, iconRes);
      SetStr(ps, 4, displayName);
      return ps.Commit() == 0;
    } finally { Marshal.ReleaseComObject(ps); }
  }

  [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h, int cmd);
  // Chromium app windows default to ~half the work area on first run, and
  // "--start-maximized" is ignored for "--app=" windows — so maximize from
  // outside. SW_MAXIMIZE (3) on an already-maximized window is a no-op.
  public static void Maximize(long hwnd) { ShowWindow(new IntPtr(hwnd), 3); }
}
'@

    $deadline = (Get-Date).AddSeconds($WindowTimeoutSec)
    while ((Get-Date) -lt $deadline) {
        $hwnd = [ClayruneAppWindow]::Find($DisplayName)
        if ($hwnd -ne 0) {
            if ($haveIcon) {
                $iconRes = (Resolve-Path $IconPath).Path + ',0'
                [ClayruneAppWindow]::Stamp($hwnd, $AppId, $iconRes, $DisplayName) | Out-Null
            }
            [ClayruneAppWindow]::Maximize($hwnd)
            break
        }
        Start-Sleep -Milliseconds 250
    }
} catch { }
exit 0
