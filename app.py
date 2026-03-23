#!/usr/bin/env python3
"""Mission Control — Desktop entry point.

Starts the Flask server in a daemon thread and opens a native pywebview window.
Works in both dev mode (python app.py) and frozen mode (PyInstaller exe).

IMPORTANT: webview.start() MUST be the last blocking call on the main thread.
Flask runs in a daemon thread so it dies when the main thread exits.
"""

import os
import sys

# Force pythonnet to use CoreCLR (.NET Core / .NET 5+) instead of .NET Framework.
# MUST be set before ANY pythonnet/clr/webview import — pythonnet reads these at
# import time. Without this, pythonnet defaults to .NET Framework 4.8, but the
# bundled WebView2 WinForms DLL targets .NETCoreApp — types fail to resolve
# silently, causing webview.start() to return immediately with no window.
if sys.platform == 'win32':
    _internal = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    _rc = os.path.join(_internal, 'pythonnet', 'runtime',
                       'Python.Runtime.runtimeconfig.json')
    if not os.path.exists(_rc):
        # Dev mode: check site-packages
        try:
            import site as _site
            for _sp in _site.getsitepackages():
                _candidate = os.path.join(_sp, 'pythonnet', 'runtime',
                                          'Python.Runtime.runtimeconfig.json')
                if os.path.exists(_candidate):
                    _rc = _candidate
                    break
        except Exception:
            pass
    if os.path.exists(_rc):
        os.environ.setdefault('PYTHONNET_RUNTIME', 'coreclr')
        os.environ.setdefault('PYTHONNET_CORECLR_RUNTIME_CONFIG', _rc)

import json
import subprocess
import threading
import time
import socket
import webbrowser
from pathlib import Path


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def _resolve_data_root():
    """Return the user-data directory (same logic as server.py _resolve_dirs)."""
    if getattr(sys, 'frozen', False):
        return Path(os.environ.get(
            'MC_DATA_DIR',
            str(Path(os.environ.get('APPDATA', str(Path.home()))) / 'MissionControl')
        ))
    else:
        return Path(__file__).parent  # Dev: repo root


DATA_ROOT = _resolve_data_root()


# ---------------------------------------------------------------------------
# First-run setup
# ---------------------------------------------------------------------------

def _ensure_data_dirs():
    """Create data directories and default config on first launch."""
    (DATA_ROOT / 'data' / 'projects').mkdir(parents=True, exist_ok=True)
    (DATA_ROOT / 'data' / 'uploads').mkdir(parents=True, exist_ok=True)

    config_path = DATA_ROOT / 'config.json'
    if not config_path.exists():
        defaults = {
            'port': 5199,
            'shared_rules_path': str(DATA_ROOT / 'data' / 'SHARED_RULES.md'),
            'projects_base': str(Path.home()),
            'agent_model': '',
            'agent_max_turns': 0,
            'agent_permission_mode': '',
            'desktop_mode': True,
            'user_name': '',
            'agent_name': '',
            'use_streaming_agent': False,
        }
        config_path.write_text(json.dumps(defaults, indent=2), encoding='utf-8')


def _load_port():
    """Read the configured port from config.json, default 5199."""
    config_path = DATA_ROOT / 'config.json'
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text(encoding='utf-8'))
            return int(cfg.get('port', 5199))
        except Exception:
            pass
    return 5199


# ---------------------------------------------------------------------------
# Subprocess helpers (silent on Windows)
# ---------------------------------------------------------------------------

_POPEN_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
_STARTUPINFO = None
if sys.platform == 'win32':
    _STARTUPINFO = subprocess.STARTUPINFO()
    _STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _STARTUPINFO.wShowWindow = 0


def _run_silent(cmd, **kwargs):
    """Run a command silently (no console window on Windows)."""
    return subprocess.run(
        cmd, capture_output=True, text=True,
        creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Claude CLI check + auto-install
# ---------------------------------------------------------------------------

def _check_claude_cli():
    """Return True if `claude --version` succeeds."""
    try:
        r = _run_silent(['claude', '--version'])
        return r.returncode == 0
    except FileNotFoundError:
        return False


def _check_npm():
    """Return True if npm is available."""
    try:
        r = _run_silent(['npm', '--version'])
        return r.returncode == 0
    except FileNotFoundError:
        return False


def _refresh_path():
    """Refresh PATH from the Windows registry so newly-installed tools are found."""
    if sys.platform != 'win32':
        return
    try:
        import winreg
        paths = []
        for root, subkey in [
            (winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'),
            (winreg.HKEY_CURRENT_USER, r'Environment'),
        ]:
            try:
                with winreg.OpenKey(root, subkey) as key:
                    val, _ = winreg.QueryValueEx(key, 'Path')
                    paths.extend(val.split(';'))
            except OSError:
                pass
        if paths:
            os.environ['PATH'] = ';'.join(p for p in paths if p)
    except Exception:
        pass


def _install_claude_cli(status_callback=None):
    """Attempt to install Claude CLI. Returns (success: bool, message: str)."""
    def _status(msg):
        if status_callback:
            status_callback(msg)

    if not _check_npm():
        _status('Installing Node.js via winget...')
        try:
            r = _run_silent([
                'winget', 'install', 'OpenJS.NodeJS.LTS',
                '--accept-package-agreements', '--accept-source-agreements',
            ], timeout=300)
            if r.returncode != 0:
                return False, (
                    'Could not install Node.js automatically.\n\n'
                    'Please install Node.js from https://nodejs.org and restart Mission Control.'
                )
        except FileNotFoundError:
            return False, (
                'winget not found. Please install Node.js from https://nodejs.org\n'
                'then run: npm install -g @anthropic-ai/claude-code'
            )
        except subprocess.TimeoutExpired:
            return False, 'Node.js installation timed out. Please install manually from https://nodejs.org'

        _refresh_path()
        if not _check_npm():
            return False, (
                'Node.js was installed but npm is not yet on PATH.\n'
                'Please restart your computer, then relaunch Mission Control.'
            )

    _status('Installing Claude CLI via npm...')
    try:
        r = _run_silent(
            ['npm', 'install', '-g', '@anthropic-ai/claude-code'],
            timeout=120,
        )
        if r.returncode != 0:
            stderr = r.stderr or r.stdout or ''
            return False, f'npm install failed:\n{stderr[:500]}'
    except subprocess.TimeoutExpired:
        return False, 'Claude CLI installation timed out.'

    _refresh_path()
    if _check_claude_cli():
        return True, 'Claude CLI installed successfully.'
    else:
        return False, (
            'Claude CLI was installed but is not on PATH.\n'
            'Please restart your computer, then relaunch Mission Control.'
        )


# ---------------------------------------------------------------------------
# .NET Desktop Runtime detection + guided install
# ---------------------------------------------------------------------------

def _msgbox(text, title='Mission Control', style=0x40):
    """Show a Windows MessageBox. Returns button ID."""
    try:
        import ctypes
        return ctypes.windll.user32.MessageBoxW(0, text, title, style)
    except Exception:
        print(f'{title}: {text}')
        return 0


def _check_dotnet_desktop_runtime():
    """Check if .NET Desktop Runtime is installed (required by pywebview)."""
    if sys.platform != 'win32':
        return True

    try:
        r = _run_silent(['dotnet', '--list-runtimes'], timeout=10)
        if r.returncode == 0 and 'Microsoft.WindowsDesktop.App' in r.stdout:
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: check registry for .NET Desktop Runtime
    try:
        import winreg
        base = r'SOFTWARE\dotnet\Setup\InstalledVersions\x64\sharedfx\Microsoft.WindowsDesktop.App'
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as key:
            if winreg.EnumValue(key, 0):
                return True
    except (OSError, ImportError):
        pass

    return False


def _install_dotnet_desktop_runtime():
    """Try to install .NET Desktop Runtime via winget. Returns (success, message)."""
    try:
        r = _run_silent([
            'winget', 'install', 'Microsoft.DotNet.DesktopRuntime.8',
            '--accept-package-agreements', '--accept-source-agreements',
        ], timeout=300)
        if r.returncode == 0:
            _refresh_path()
            return True, '.NET Desktop Runtime installed successfully.'
        else:
            stderr = r.stderr or r.stdout or ''
            return False, f'winget install failed:\n{stderr[:500]}'
    except FileNotFoundError:
        return False, 'winget not found.'
    except subprocess.TimeoutExpired:
        return False, 'Installation timed out.'


def _ensure_dotnet_runtime():
    """Check for .NET Desktop Runtime and guide user through install if missing.

    Returns True if runtime is available.
    Returns False to signal 'use browser mode instead'.
    """
    if _check_dotnet_desktop_runtime():
        return True

    if sys.platform != 'win32':
        return False

    result = _msgbox(
        'Mission Control requires the .NET Desktop Runtime to display its native window.\n\n'
        'Would you like to install it now?\n\n'
        '  Yes  = Auto-install via winget (recommended)\n'
        '  No   = Open download page in browser\n'
        '  Cancel = Skip and use browser mode instead\n',
        'Mission Control - Setup Required',
        0x33,  # MB_YESNOCANCEL | MB_ICONWARNING
    )

    if result == 6:  # Yes — auto-install
        _msgbox(
            'Installing .NET Desktop Runtime...\n\n'
            'This may take a minute. Click OK to begin.',
            'Mission Control - Installing',
            0x40,
        )
        success, message = _install_dotnet_desktop_runtime()
        if success:
            _msgbox(
                '.NET Desktop Runtime installed successfully!\n\n'
                'Mission Control will now open.',
                'Mission Control - Setup Complete',
                0x40,
            )
            return True
        else:
            _msgbox(
                f'Auto-install failed: {message}\n\n'
                'Please install manually:\n'
                '1. Open https://dotnet.microsoft.com/download/dotnet/8.0\n'
                '2. Download ".NET Desktop Runtime" (not just Runtime)\n'
                '3. Run the installer\n'
                '4. Restart Mission Control\n\n'
                'For now, opening in browser mode.',
                'Mission Control - Install Failed',
                0x30,
            )
            return False

    elif result == 7:  # No — open download page
        webbrowser.open('https://dotnet.microsoft.com/download/dotnet/8.0')
        _msgbox(
            'Download page opened in your browser.\n\n'
            'Please install the ".NET Desktop Runtime" (not just Runtime).\n'
            'Then restart Mission Control.\n\n'
            'For now, opening in browser mode.',
            'Mission Control - Manual Install',
            0x40,
        )
        return False

    else:  # Cancel — browser mode
        return False


# ---------------------------------------------------------------------------
# Flask server (daemon thread)
# ---------------------------------------------------------------------------

def _wait_for_port(port, timeout=15):
    """Block until the Flask server is accepting connections."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _start_flask(port):
    """Import and start the Flask server. Runs in a daemon thread."""
    os.environ['MC_DATA_DIR'] = str(DATA_ROOT)
    os.environ['MC_PORT'] = str(port)

    repo_root = str(Path(__file__).parent)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    from server import app, PORT, _start_scheduler
    _start_scheduler()
    app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False)


# ---------------------------------------------------------------------------
# Browser fallback
# ---------------------------------------------------------------------------

def _open_browser_and_wait(port):
    """Open the default browser and keep the process alive for Flask."""
    webbrowser.open(f'http://127.0.0.1:{port}')
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------------------
# Entry point — webview.start() MUST be on main thread at top level
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    # Hide console window in frozen builds
    if sys.platform == 'win32' and getattr(sys, 'frozen', False):
        try:
            import ctypes
            ctypes.windll.user32.ShowWindow(
                ctypes.windll.kernel32.GetConsoleWindow(), 0)  # SW_HIDE
        except Exception:
            pass

    # Ensure UTF-8 output (Windows console fix)
    if sys.platform == 'win32':
        os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

    _ensure_data_dirs()
    _port = _load_port()

    # --- .NET Desktop Runtime pre-check ---
    _use_webview = _ensure_dotnet_runtime()

    # --- Claude CLI check + auto-install ---
    _cli_warning = None
    if not _check_claude_cli():
        print('Claude CLI not found — attempting auto-install...')
        _ok, _msg = _install_claude_cli(status_callback=print)
        if _ok:
            print(_msg)
        else:
            _cli_warning = _msg
            print(f'Claude CLI setup: {_msg}')

    # --- Start Flask in daemon thread ---
    _flask_thread = threading.Thread(target=_start_flask, args=(_port,), daemon=True)
    _flask_thread.start()

    if not _wait_for_port(_port):
        print(f'ERROR: Flask server did not start on port {_port}')
        sys.exit(1)

    print(f'Flask server running on http://127.0.0.1:{_port}')

    # --- Browser mode (user chose skip, or .NET missing) ---
    if not _use_webview:
        _open_browser_and_wait(_port)
        sys.exit(0)

    # --- Native window via pywebview ---
    # webview.start() MUST be the last blocking call on the main thread.
    # It runs the Win32 message loop — returns only when the window is closed.
    _webview_ok = False
    try:
        import webview
        import clr  # triggers CoreCLR + .NET load — fail fast if broken
        _webview_ok = True
    except Exception as _e:
        print(f'[MissionControl] Native window unavailable ({type(_e).__name__}: {_e})')
        print('[MissionControl] Falling back to browser.')

    if _webview_ok:
        try:
            _window = webview.create_window(
                'Mission Control',
                url=f'http://127.0.0.1:{_port}',
                width=1400,
                height=900,
                min_size=(900, 600),
            )

            if _cli_warning:
                def _show_warning():
                    time.sleep(2)
                    try:
                        _window.evaluate_js(
                            f'alert({json.dumps("Claude CLI not found:\\n\\n" + _cli_warning)})'
                        )
                    except Exception:
                        pass
                threading.Thread(target=_show_warning, daemon=True).start()

            # start() may return immediately (GUI on background thread)
            # so keep main thread alive until all windows are closed
            webview.start()
            while webview.windows:
                time.sleep(0.5)
        except Exception as _e:
            print(f'[MissionControl] Window creation failed ({_e}), opening browser.')
            _open_browser_and_wait(_port)
    else:
        _open_browser_and_wait(_port)
