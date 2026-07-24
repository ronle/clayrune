"""Regression tests for /api/system/update's force-push recovery.

`/api/system/update` is the ONLY update channel most users have. Before
2026-07-23 it ran a bare `git pull --ff-only`, which aborts outright when the
release branch has been force-pushed upstream:

    fatal: Not possible to fast-forward, aborting.

That bricks every existing install permanently and silently — observed on a
clean-VM smoke test. The endpoint now falls back to
`fetch` + `reset --hard origin/<branch>`.

The two properties worth pinning, and why:

1. **It recovers from a force-push.** `test_force_push_is_recovered` builds a
   real upstream, clones it, rewrites upstream history, and asserts the
   endpoint lands the checkout on the new tip. `test_control_ff_only_fails`
   proves the scenario genuinely breaks a bare ff-only pull, so the test can't
   silently stop testing anything.

2. **It never eats user data.** All Clayrune user state lives INSIDE the
   checkout but untracked/gitignored (data/projects/, config.json, .venv/).
   `reset --hard` rewrites tracked files only, so it survives — but a future
   edit adding `git clean` here would delete all of it. That is what
   `test_user_data_survives_resync` guards.
"""
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _run(args, cwd):
    r = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True)
    assert r.returncode == 0, f"{args} failed in {cwd}: {r.stdout}{r.stderr}"
    return r.stdout.strip()


def _sha(repo, rev='HEAD'):
    return _run(['git', 'rev-parse', rev], repo)


@pytest.fixture()
def lab(tmp_path):
    """A bare upstream + a clone of it, with the clone carrying user data.

    Returns (upstream, checkout). Upstream has NOT been rewritten yet — each
    test calls `force_push_rewrite` when it wants the divergence.
    """
    upstream = tmp_path / 'upstream.git'
    work = tmp_path / 'work'
    checkout = tmp_path / 'Clayrune'

    _run(['git', 'init', '--bare', '-b', 'master', str(upstream)], tmp_path)
    _run(['git', 'init', '-b', 'master', str(work)], tmp_path)
    _run(['git', 'config', 'user.email', 'test@example.com'], work)
    _run(['git', 'config', 'user.name', 'Test'], work)
    (work / 'server.py').write_text('v1\n')
    _run(['git', 'add', 'server.py'], work)
    _run(['git', 'commit', '-m', 'v1'], work)
    _run(['git', 'remote', 'add', 'origin', str(upstream)], work)
    _run(['git', 'push', 'origin', 'master'], work)

    _run(['git', 'clone', str(upstream), str(checkout)], tmp_path)

    # User data lives inside the checkout, untracked — exactly like a real install.
    (checkout / 'data' / 'projects').mkdir(parents=True)
    (checkout / 'data' / 'projects' / 'p1.json').write_text('MY PROJECT DATA')
    (checkout / 'config.json').write_text('{"port": 5199}')
    (checkout / '.venv' / 'Scripts').mkdir(parents=True)
    (checkout / '.venv' / 'Scripts' / 'python.exe').write_text('venv-marker')

    return {'upstream': upstream, 'work': work, 'checkout': checkout}


def force_push_rewrite(lab):
    """Rewrite upstream history so a fast-forward is impossible."""
    work = lab['work']
    _run(['git', 'checkout', '--orphan', 'rewritten'], work)
    subprocess.run(['git', 'rm', '-rf', '.'], cwd=str(work), capture_output=True, text=True)
    (work / 'server.py').write_text('v2-rewritten\n')
    (work / 'NEWFILE.md').write_text('shipped in the rewrite\n')
    _run(['git', 'add', '-A'], work)
    _run(['git', 'commit', '-m', 'v2 rewritten history'], work)
    _run(['git', 'branch', '-M', 'master'], work)
    _run(['git', 'push', '--force', 'origin', 'master'], work)
    return _sha(lab['upstream'], 'master')


@pytest.fixture()
def client(lab, monkeypatch):
    """Test client with the update endpoints pointed at the lab checkout."""
    import server
    from mc.blueprints import system_routes as sr
    monkeypatch.setattr(sr, '_APP_DIR', lab['checkout'])
    server.app.config['TESTING'] = True
    return server.app.test_client()


class TestControl:
    def test_control_ff_only_fails(self, lab):
        """The scenario must genuinely break a bare ff-only pull.

        Without this, a future refactor could make the recovery test pass
        trivially (e.g. by never producing a divergence at all).
        """
        force_push_rewrite(lab)
        r = subprocess.run(['git', 'pull', '--ff-only'],
                           cwd=str(lab['checkout']), capture_output=True, text=True)
        assert r.returncode != 0
        assert 'fast-forward' in (r.stdout + r.stderr).lower()


class TestForcePushRecovery:
    def test_force_push_is_recovered(self, client, lab):
        new_tip = force_push_rewrite(lab)

        resp = client.post('/api/system/update')
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()

        assert body['ok'] is True
        # The ff-only attempt must have failed and triggered the hard re-sync.
        assert body['resynced'] is True
        assert body['previous_commit']
        assert body['new_commit'] != body['previous_commit']

        assert _sha(lab['checkout']) == new_tip
        assert (lab['checkout'] / 'server.py').read_text().strip() == 'v2-rewritten'
        assert (lab['checkout'] / 'NEWFILE.md').exists()

    def test_user_data_survives_resync(self, client, lab):
        """reset --hard touches tracked files only. Adding `git clean` here
        would delete every user project — that is what this pins."""
        force_push_rewrite(lab)
        assert client.post('/api/system/update').status_code == 200

        co = lab['checkout']
        assert (co / 'data' / 'projects' / 'p1.json').read_text() == 'MY PROJECT DATA'
        assert (co / 'config.json').read_text() == '{"port": 5199}'
        assert (co / '.venv' / 'Scripts' / 'python.exe').read_text() == 'venv-marker'


class TestNormalPath:
    def test_clean_fast_forward_does_not_resync(self, client, lab):
        """The happy path must stay a plain ff-only pull — no hard reset when
        one isn't needed, so local commits/history are preserved."""
        work = lab['work']
        (work / 'feature.md').write_text('added normally\n')
        _run(['git', 'add', 'feature.md'], work)
        _run(['git', 'commit', '-m', 'normal commit'], work)
        _run(['git', 'push', 'origin', 'master'], work)

        resp = client.post('/api/system/update')
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body['ok'] is True
        assert body['resynced'] is False
        assert (lab['checkout'] / 'feature.md').exists()

    def test_dirty_tree_still_refused(self, client, lab):
        """A dirty working tree must NOT be silently reset — the 409 guard
        predates this change and must survive it."""
        force_push_rewrite(lab)
        (lab['checkout'] / 'server.py').write_text('local edit\n')

        resp = client.post('/api/system/update')
        assert resp.status_code == 409
        assert 'local changes' in resp.get_json()['error']
        # Untouched.
        assert (lab['checkout'] / 'server.py').read_text().strip() == 'local edit'
