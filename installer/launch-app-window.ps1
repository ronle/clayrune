# Clayrune app-window launcher (Windows).
#
# Invoked hidden by installer\start.bat once a Chromium browser is detected.
# Does four things, all best-effort:
#   0. Ensures the Desktop / Start Menu shortcuts carry
#      System.AppUserModel.ID = io.clayrune.app. The Windows taskbar resolves
#      a window group's icon from a Start Menu shortcut with a matching
#      AppUserModelID — stamping the window alone is NOT enough (same reason
#      Electron's setAppUserModelId docs require a matching shortcut).
#      Idempotent, so existing installs self-heal on next launch.
#   1. Polls http://localhost:5199 until the server accepts connections
#      (an --app window shows a hard error page on connection-refused and
#      won't auto-retry, so we must not launch early).
#   2. Opens Clayrune as a standalone "--app=" window, maximized once found
#      (Chromium app windows default to ~half the work area on first run and
#      --start-maximized is ignored for --app= windows).
#   3. Stamps the new window's taskbar identity (AppUserModelID +
#      RelaunchIconResource + RelaunchDisplayNameResource), repeatedly for a
#      few seconds — Edge's first-run initialization can re-apply its own
#      relaunch properties after window creation, clobbering a one-shot stamp.
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
# exactly today's behaviour. Each step appends one line to
# data\logs\launcher.log so VM/user reports are diagnosable.
param(
    [Parameter(Mandatory = $true)][string]$Browser,
    [string]$Url = 'http://localhost:5199',
    [string]$IconPath = '',
    [string]$AppId = 'io.clayrune.app',   # matches the macOS bundle id
    [string]$DisplayName = 'Clayrune',
    [int]$PortTimeoutSec = 30,
    [int]$WindowTimeoutSec = 45,
    [int]$RestampSec = 15
)

$ErrorActionPreference = 'SilentlyContinue'

# Launcher log (best-effort). Lives next to the server's own log.
$script:LogPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'data\logs\launcher.log'
function Log([string]$msg) {
    try {
        $dir = Split-Path -Parent $script:LogPath
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
        Add-Content -Path $script:LogPath -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg" -Encoding utf8
    } catch { }
}

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
      if (iconRes != null && iconRes.Length > 0) {
        SetStr(ps, 2, iconRes);
        SetStr(ps, 4, displayName);
      }
      return ps.Commit() == 0;
    } finally { Marshal.ReleaseComObject(ps); }
  }

  [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h, int cmd);
  // SW_MAXIMIZE (3) on an already-maximized window is a no-op.
  public static void Maximize(long hwnd) { ShowWindow(new IntPtr(hwnd), 3); }

  // The taskbar resolves a button's identity + icon ONCE, when the window
  // first appears; later property-store changes regroup nothing (verified
  // live 2026-06-12: stamped window kept the Edge logo). Hiding retires the
  // button; re-showing creates a fresh one that re-reads the stamped AUMID
  // and resolves the icon from the matching shortcut / RelaunchIconResource.
  // SW_SHOWMAXIMIZED doubles as the first-run maximize.
  public static void RefreshTaskbarButton(long hwnd) {
    ShowWindow(new IntPtr(hwnd), 0);  // SW_HIDE
    System.Threading.Thread.Sleep(150);
    ShowWindow(new IntPtr(hwnd), 3);  // SW_MAXIMIZE (show + maximize)
  }

  // ── .lnk AppUserModelID patcher ──────────────────────────────────────────
  // The taskbar shows a group's icon from the Start Menu / Desktop shortcut
  // whose System.AppUserModel.ID matches the window's. The installer creates
  // the shortcuts with the right .ico but no AUMID; add it here.
  [ComImport, Guid("00021401-0000-0000-C000-000000000046")] class CShellLink { }
  [ComImport, Guid("0000010b-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IPersistFile {
    void GetClassID(out Guid pClassID);
    [PreserveSig] int IsDirty();
    void Load([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, uint dwMode);
    void Save([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, bool fRemember);
    void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string pszFileName);
    void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string ppszFileName);
  }
  public static bool PatchShortcut(string lnkPath, string aumid) {
    var link = new CShellLink();
    try {
      var pf = (IPersistFile)link;
      pf.Load(lnkPath, 2 /* STGM_READWRITE */);
      var ps = (IPropertyStore)link;
      if (SetStr(ps, 5, aumid) != 0) return false;
      if (ps.Commit() != 0) return false;
      pf.Save(lnkPath, true);
      return true;
    } finally { Marshal.ReleaseComObject(link); }
  }
}
'@
} catch { Log "Add-Type failed: $_"; exit 0 }

# -- 0. Ensure shortcuts carry the AUMID (idempotent self-heal) ---------------
$shortcuts = @(
    "$env:USERPROFILE\Desktop\Clayrune.lnk",
    "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Clayrune.lnk"
)
foreach ($lnk in $shortcuts) {
    if (Test-Path $lnk) {
        try {
            $ok = [ClayruneAppWindow]::PatchShortcut($lnk, $AppId)
            Log "shortcut $lnk aumid=$AppId ok=$ok"
        } catch { Log "shortcut patch failed ${lnk}: $_" }
    }
}

# -- 1. Wait for the server port ----------------------------------------------
$uri = [Uri]$Url
$deadline = (Get-Date).AddSeconds($PortTimeoutSec)
$portUp = $false
while ((Get-Date) -lt $deadline) {
    try {
        $c = New-Object Net.Sockets.TcpClient
        $c.Connect($uri.Host, $uri.Port)
        $c.Close()
        $portUp = $true
        break
    } catch { Start-Sleep -Milliseconds 200 }
}
Log "port up=$portUp browser=$Browser"

# -- 2. Open the app window -----------------------------------------------------
try {
    Start-Process -FilePath $Browser -ArgumentList "--app=$Url", '--no-first-run', '--no-default-browser-check'
} catch { Log "browser launch failed: $_"; exit 0 }

# -- 3. Find the window: maximize once, stamp identity repeatedly --------------
$haveIcon = $IconPath -and (Test-Path $IconPath)
$iconRes = if ($haveIcon) { (Resolve-Path $IconPath).Path + ',0' } else { '' }
try {
    $deadline = (Get-Date).AddSeconds($WindowTimeoutSec)
    $hwnd = 0
    while ((Get-Date) -lt $deadline) {
        $hwnd = [ClayruneAppWindow]::Find($DisplayName)
        if ($hwnd -ne 0) { break }
        Start-Sleep -Milliseconds 250
    }
    if ($hwnd -eq 0) { Log "window not found within ${WindowTimeoutSec}s"; exit 0 }

    $ok = [ClayruneAppWindow]::Stamp($hwnd, $AppId, $iconRes, $DisplayName)
    [ClayruneAppWindow]::RefreshTaskbarButton($hwnd)
    Log "window $hwnd stamped ok=$ok icon=$haveIcon, taskbar button refreshed"

    # Re-stamp for a few seconds: Edge's first-run init (fresh profile) can
    # re-apply its own relaunch properties after creation; last write wins.
    # Re-find each pass — first-run flows can recreate the window. A window
    # we haven't refreshed yet (new hwnd) gets the hide/re-show too, so its
    # taskbar button is born against the stamped identity.
    $refreshed = $hwnd
    $deadline = (Get-Date).AddSeconds($RestampSec)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 2500
        $h = [ClayruneAppWindow]::Find($DisplayName)
        if ($h -ne 0) {
            [ClayruneAppWindow]::Stamp($h, $AppId, $iconRes, $DisplayName) | Out-Null
            if ($h -ne $refreshed) {
                [ClayruneAppWindow]::RefreshTaskbarButton($h)
                $refreshed = $h
                Log "new window $h appeared, re-stamped + refreshed"
            }
        }
    }
} catch { Log "stamp phase failed: $_" }
exit 0
