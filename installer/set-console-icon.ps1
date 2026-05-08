# set-console-icon.ps1 — change the icon of the calling cmd console window.
#
# Usage (from start.bat):
#   powershell -NoProfile -ExecutionPolicy Bypass -File "<dir>\installer\set-console-icon.ps1" -IconPath "<dir>\assets\clayrune.ico"
#
# Why: cmd.exe always shows its own default icon on the console title bar
# AND on the Windows taskbar entry of the running window. The .lnk shortcut
# we create has its own IconLocation and that's what shows when pinned, but
# the moment the .bat opens a cmd window, the *running* taskbar entry uses
# cmd's icon — which looks unrelated to Clayrune. Sending WM_SETICON to the
# console window with our .ico replaces it for the lifetime of the window.
#
# The icon is owned by the window, not by the powershell process that sets
# it, so it persists after this script exits.

param(
    [Parameter(Mandatory=$true)] [string]$IconPath
)

if (-not (Test-Path $IconPath)) { exit 0 }

try {
    Add-Type -AssemblyName System.Drawing -ErrorAction Stop
    Add-Type -MemberDefinition @'
[DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow();
[DllImport("user32.dll")] public static extern int SendMessage(IntPtr hWnd, int Msg, int wParam, IntPtr lParam);
'@ -Name W32 -Namespace MC -ErrorAction Stop

    $hwnd = [MC.W32]::GetConsoleWindow()
    if ($hwnd -eq [IntPtr]::Zero) { exit 0 }

    $icon = New-Object System.Drawing.Icon($IconPath)
    # WM_SETICON = 0x80, ICON_SMALL = 0 (taskbar / title bar small),
    # ICON_BIG = 1 (Alt+Tab / hi-DPI taskbar). Set both for consistency.
    [MC.W32]::SendMessage($hwnd, 0x80, 0, $icon.Handle) | Out-Null
    [MC.W32]::SendMessage($hwnd, 0x80, 1, $icon.Handle) | Out-Null
} catch {
    # Non-fatal — the cmd window keeps cmd.exe's default icon. Don't print
    # anything (would clutter the user-visible output).
    exit 0
}
