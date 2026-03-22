#!/usr/bin/env python3
"""Mission Control — Desktop entry point.

Starts the Flask server in a daemon thread and opens a native pywebview window.
Works in both dev mode (python app.py) and frozen mode (PyInstaller exe).
"""

import os
import sys
import json
import shutil
import subprocess
import threading
import time
import socket
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
# Claude CLI check + auto-install
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

    # Step 1: check npm
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

    # Step 2: install Claude CLI via npm
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
    # Set MC_DATA_DIR so server.py picks up the same data root
    os.environ['MC_DATA_DIR'] = str(DATA_ROOT)
    os.environ['MC_PORT'] = str(port)

    # Ensure server.py can be imported
    repo_root = str(Path(__file__).parent)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    from server import app, PORT, _start_scheduler
    _start_scheduler()
    app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False)


# ---------------------------------------------------------------------------
# .NET fallback
# ---------------------------------------------------------------------------

def _dotnet_error_fallback(port):
    """Show a friendly error + open browser when .NET runtime is missing."""
    import webbrowser
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0,
            'Mission Control requires the .NET Desktop Runtime to display its native window.\n\n'
            'The app will now open in your default browser instead.\n\n'
            'To fix this permanently, install the .NET Desktop Runtime from:\n'
            'https://dotnet.microsoft.com/download/dotnet\n\n'
            'Then restart Mission Control.',
            'Mission Control — .NET Runtime Missing',
            0x40,  # MB_ICONINFORMATION
        )
    except Exception:
        print('WARNING: .NET Desktop Runtime not found.')
        print('Opening Mission Control in your default browser instead.')
        print('Install .NET Desktop Runtime from: https://dotnet.microsoft.com/download/dotnet')

    webbrowser.open(f'http://127.0.0.1:{port}')

    # Keep the process alive so Flask stays running
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Ensure UTF-8 output (Windows console fix)
    if sys.platform == 'win32':
        os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

    _ensure_data_dirs()
    port = _load_port()

    # --- Claude CLI check + auto-install ---
    claude_available = _check_claude_cli()
    cli_warning = None

    if not claude_available:
        # Try auto-install
        print('Claude CLI not found — attempting auto-install...')
        success, message = _install_claude_cli(status_callback=print)
        if success:
            claude_available = True
            print(message)
        else:
            cli_warning = message
            print(f'Claude CLI setup: {message}')

    # --- Start Flask in daemon thread ---
    flask_thread = threading.Thread(target=_start_flask, args=(port,), daemon=True)
    flask_thread.start()

    if not _wait_for_port(port):
        print(f'ERROR: Flask server did not start on port {port}')
        sys.exit(1)

    print(f'Flask server running on http://127.0.0.1:{port}')

    # --- Open pywebview window ---
    # Pre-check: can pythonnet/clr actually initialize?
    # webview.start() crashes at the native level if .NET is missing,
    # which PyInstaller's runw bootloader shows as an unhandled exception
    # dialog before Python's try/except can catch it.
    dotnet_ok = True
    try:
        from clr_loader import get_coreclr
        get_coreclr()
    except Exception:
        dotnet_ok = False

    if not dotnet_ok:
        _dotnet_error_fallback(port)
        return

    import webview

    window = webview.create_window(
        'Mission Control',
        url=f'http://127.0.0.1:{port}',
        width=1400,
        height=900,
        min_size=(900, 600),
    )

    # Show CLI warning after window loads (non-blocking)
    if cli_warning:
        def _show_warning():
            time.sleep(2)  # let page render
            try:
                window.evaluate_js(
                    f'alert({json.dumps("Claude CLI not found:\\n\\n" + cli_warning)})'
                )
            except Exception:
                pass
        threading.Thread(target=_show_warning, daemon=True).start()

    # Blocking — runs the native window event loop
    webview.start()


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        err_str = str(e)
        if 'Python.Runtime' in err_str or 'clr_loader' in err_str or 'pythonnet' in err_str or '.NET' in err_str:
            # Last-resort fallback — dotnet error escaped inner handler
            import webbrowser
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0,
                    'Mission Control requires the .NET Desktop Runtime to display its native window.\n\n'
                    'The app will now open in your default browser instead.\n\n'
                    'To fix this permanently, install the .NET Desktop Runtime from:\n'
                    'https://dotnet.microsoft.com/download/dotnet\n\n'
                    'Then restart Mission Control.',
                    'Mission Control — .NET Runtime Missing',
                    0x40,
                )
            except Exception:
                pass
            webbrowser.open('http://127.0.0.1:5199')
            import time
            try:
                while True:
                    time.sleep(60)
            except KeyboardInterrupt:
                pass
        else:
            raise
