"""Shared fixtures for the Clayrune main-app test suite.

Mirrors the shape of control_plane/tests/conftest.py (env-isolation +
in-memory stubs + a call recorder) but for the Flask app and github_sync.

Scope (deliberately small — IMPROVEMENT_PLAN_V2.md P1-5):
  - `repo_root`        : path to the repo, also put on sys.path
  - `tmp_data_dir`     : isolates MC_DATA_DIR so importing `server` and any
                         filesystem writes land in a throwaway temp dir
  - `fake_gh`          : a programmable, recording stand-in for the `gh` CLI
                         injected into github_sync.subprocess.run
  - `gs`               : the github_sync module, register()-wired to an
                         in-memory project store, with fake_gh active
  - `project_store`    : dict-backed {project_id: project} the gs fixture
                         loads/saves through

These work standalone-ish but pytest is the supported runner.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent


# Make the app modules importable when running `pytest` from anywhere.
sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return _REPO_ROOT


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    """Point MC_DATA_DIR at a throwaway dir.

    server.py does `DATA_DIR.mkdir(...)` etc. at import time off _DATA_ROOT,
    which is `os.environ['MC_DATA_DIR']` when set. Setting this BEFORE server
    is imported keeps the real ./data tree untouched.
    """
    d = tmp_path / "mc_data"
    d.mkdir()
    monkeypatch.setenv("MC_DATA_DIR", str(d))
    # Avoid colliding with a running instance's port if anything reads it.
    monkeypatch.setenv("MC_PORT", "0")
    return d


# ─── Fake gh CLI ─────────────────────────────────────────────────────────────


class _CompletedProcess:
    """Minimal stand-in for subprocess.CompletedProcess."""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeGh:
    """Programmable, recording replacement for `gh` via subprocess.run.

    Usage in a test::

        fake_gh.on(["issue", "list"], stdout=json.dumps([...]))
        fake_gh.on(["issue", "create"], stdout="https://github.com/o/r/issues/7")
        ...
        assert fake_gh.count(["issue", "close"]) == 0

    Matching: a handler registered with prefix tokens matches if those tokens
    appear in order anywhere in the gh argv (after the leading 'gh'). Last
    registered matching handler wins, so tests can override defaults.
    """

    def __init__(self):
        self.calls: list[list[str]] = []          # full argv incl. 'gh'
        self._handlers: list[tuple[list[str], object]] = []

    # -- configuration -------------------------------------------------------

    def on(self, match: list[str], *, returncode: int = 0,
           stdout: str = "", stderr: str = "", callback=None):
        """Register a response for argv containing `match` tokens in order.

        `callback(argv) -> (returncode, stdout, stderr)` takes precedence and
        lets a test vary the response per call (e.g. unique issue numbers).
        """
        self._handlers.append((list(match), callback or (returncode, stdout, stderr)))
        return self

    # -- query ---------------------------------------------------------------

    def _argv_matches(self, match: list[str], argv: list[str]) -> bool:
        i = 0
        for tok in argv:
            if i < len(match) and tok == match[i]:
                i += 1
        return i == len(match)

    def count(self, match: list[str]) -> int:
        return sum(1 for c in self.calls if self._argv_matches(match, c[1:]))

    def calls_matching(self, match: list[str]) -> list[list[str]]:
        return [c for c in self.calls if self._argv_matches(match, c[1:])]

    # -- the subprocess.run shim --------------------------------------------

    def run(self, cmd, **kwargs):
        argv = list(cmd)
        self.calls.append(argv)
        gh_args = argv[1:] if argv and argv[0] == "gh" else argv
        # Last matching handler wins.
        for match, resp in reversed(self._handlers):
            if self._argv_matches(match, gh_args):
                if callable(resp):
                    rc, out, err = resp(argv)
                else:
                    rc, out, err = resp
                return _CompletedProcess(rc, out, err)
        # Unconfigured gh call → empty success (mirrors `gh` with no output).
        return _CompletedProcess(0, "", "")


@pytest.fixture
def fake_gh(monkeypatch):
    import github_sync
    fg = FakeGh()
    monkeypatch.setattr(github_sync.subprocess, "run", fg.run)
    return fg


@pytest.fixture
def project_store() -> dict:
    return {}


@pytest.fixture
def gs(fake_gh, project_store, monkeypatch):
    """github_sync, register()-wired to the in-memory project_store.

    Rate limit + per-project lock state are module globals; clear them so
    tests don't interfere with each other.
    """
    import github_sync
    importlib.reload(github_sync)
    # fake_gh patched the pre-reload module; re-patch the fresh one.
    fg = fake_gh
    monkeypatch.setattr(github_sync.subprocess, "run", fg.run)

    activity_log: list[tuple[str, str]] = []

    def _log_activity(pid, msg):
        activity_log.append((pid, msg))

    def _load_project(pid):
        p = project_store.get(pid)
        return json.loads(json.dumps(p)) if p is not None else None

    def _save_project(pid, project):
        project_store[pid] = json.loads(json.dumps(project))

    _now = ["2026-05-17T00:00:00Z"]

    def _now_iso():
        return _now[0]

    github_sync.register(
        popen_flags=0,
        startupinfo=None,
        log_activity=_log_activity,
        load_project=_load_project,
        save_project=_save_project,
        now_iso=_now_iso,
    )
    github_sync._activity_log = activity_log   # test introspection
    github_sync._set_now = lambda s: _now.__setitem__(0, s)
    return github_sync
