"""Mission Control TTY shim (auto-loaded via sitecustomize).

Makes Python processes inside MC terminal pop-outs behave as if connected
to a real terminal. Activated by MC_FORCE_TTY=1 environment variable.
"""
import os, sys

if os.environ.get('MC_FORCE_TTY') == '1':
    class _FakeBuffer:
        """Wraps a raw buffer so isatty() returns True.

        This prevents code like ``io.TextIOWrapper(sys.stdout.buffer)``
        from losing the TTY override.  Also forces write-through so
        Rich Live display (which uses \\r without \\n) isn't buffered.
        """
        __slots__ = ('_buf',)

        def __init__(self, buf):
            object.__setattr__(self, '_buf', buf)

        def __getattr__(self, name):
            return getattr(self._buf, name)

        def isatty(self):
            return True

        def write(self, data):
            n = self._buf.write(data)
            try:
                self._buf.flush()
            except Exception:
                pass
            return n

    class _FakeTTY:
        """Wraps a stream so isatty() returns True."""
        __slots__ = ('_stream', '_fake_buffer')

        def __init__(self, stream):
            object.__setattr__(self, '_stream', stream)
            raw_buf = getattr(stream, 'buffer', None)
            object.__setattr__(self, '_fake_buffer',
                               _FakeBuffer(raw_buf) if raw_buf is not None else None)

        def __getattr__(self, name):
            if name == 'buffer' and self._fake_buffer is not None:
                return self._fake_buffer
            return getattr(self._stream, name)

        def isatty(self):
            return True

        def write(self, data):
            return self._stream.write(data)

        def flush(self):
            return self._stream.flush()

        def fileno(self):
            return self._stream.fileno()

        def writable(self):
            return self._stream.writable()

        def readable(self):
            return self._stream.readable()

    sys.stdout = _FakeTTY(sys.stdout)
    sys.stderr = _FakeTTY(sys.stderr)

    # Patch os.get_terminal_size() to return COLUMNS/LINES from env
    # (the real call fails on pipe fds, which breaks Rich TUI rendering)
    _orig_get_terminal_size = os.get_terminal_size

    def _patched_get_terminal_size(fd=None):
        try:
            return _orig_get_terminal_size() if fd is None else _orig_get_terminal_size(fd)
        except (OSError, ValueError):
            pass
        cols = int(os.environ.get('COLUMNS', '120'))
        lines = int(os.environ.get('LINES', '30'))
        return os.terminal_size((cols, lines))

    os.get_terminal_size = _patched_get_terminal_size

    # Also patch shutil.get_terminal_size for libraries that call it directly
    try:
        import shutil as _shutil
        _orig_shutil_gts = _shutil.get_terminal_size

        def _patched_shutil_gts(fallback=None):
            cols = int(os.environ.get('COLUMNS', '120'))
            lines = int(os.environ.get('LINES', '30'))
            return os.terminal_size((cols, lines))

        _shutil.get_terminal_size = _patched_shutil_gts
    except Exception:
        pass

    # Patch Rich's Windows console feature detection to emit ANSI escapes
    try:
        import rich.console
        from rich._windows import WindowsConsoleFeatures
        rich.console._windows_console_features = WindowsConsoleFeatures(vt=True, truecolor=True)
    except (ImportError, AttributeError):
        pass
