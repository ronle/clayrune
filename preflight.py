"""Clayrune — Python interpreter preflight.

Imported FIRST by every entry point (app.py, server.py) so that running Clayrune
on a too-old Python fails with one clear, actionable line — instead of a cryptic

    TypeError: unsupported operand type(s) for |: 'type' and 'type'

thrown deep inside the import chain. Many modules (e.g. mc_remote/attestation.py)
use PEP 604 ``X | Y`` unions at *runtime* (module-level assignments, not just
annotations, so ``from __future__ import annotations`` does not save them), and
those only exist on Python >= 3.10. The project as a whole targets 3.11+.

The installer (installer/install.sh) already provisions Python 3.11+, but a
manual ``git clone`` + ``python3 -m venv venv`` using a distro's *system* python3
bypasses that guard entirely:

    RHEL / Rocky / AlmaLinux 9   ship Python 3.9
    Ubuntu 22.04                 ships Python 3.10

This module is the catch-all that turns that mistake into a fixable message.

Keep this file dependency-free and written in syntax valid on ANY Python 3 — no
f-strings, no ``X | Y``, no walrus. It must import cleanly on the very
interpreters it exists to reject.
"""
import sys

# The single source of truth for the floor. Kept in sync with installer/
# install.sh (_find_python requires 3.11+), installer/install-prompt.md, and the
# download page (installer/index.html). Change it here and update those.
MIN_PYTHON = (3, 11)


def enforce(min_version=MIN_PYTHON):
    """Exit with a clear, actionable message if the interpreter is too old."""
    if sys.version_info[:2] >= tuple(min_version):
        return

    have = "%d.%d.%d" % (sys.version_info[0], sys.version_info[1],
                         sys.version_info[2])
    need = "%d.%d" % (min_version[0], min_version[1])
    pyx = "python%d.%d" % (min_version[0], min_version[1])

    lines = [
        "",
        "  Clayrune requires Python " + need + "+, but this interpreter is "
        + have + ".",
        "  Interpreter: " + (sys.executable or "(unknown)"),
        "",
        "  Your virtualenv was built with too old a Python. Recreate it with a",
        "  newer one (install it first if your distro's default is older):",
        "",
        "    # Debian / Ubuntu           sudo apt install " + pyx + " " + pyx
        + "-venv",
        "    # Fedora / RHEL / Rocky / Alma   sudo dnf install " + pyx,
        "    # macOS (Homebrew)          brew install python@" + need,
        "",
        "    rm -rf .venv venv",
        "    " + pyx + " -m venv .venv",
        "    . .venv/bin/activate",
        "    pip install -r requirements.txt",
        "",
    ]
    sys.stderr.write("\n".join(lines) + "\n")
    sys.stderr.flush()
    raise SystemExit(1)


enforce()
