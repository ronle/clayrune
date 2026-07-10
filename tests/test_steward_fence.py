"""Tests for the steward reversibility fence (steward/fence.py).

The fence is a hard backstop for an unattended agent: the critical property is
ZERO false-negatives on the catastrophic/irreversible set. False-positives
(blocking a safe command) are acceptable — they just make the steward ask.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from steward.fence import classify_action, classify_bash

REPO = Path(__file__).resolve().parents[1]
FENCE = REPO / 'steward' / 'fence.py'


# ── Must BLOCK — irreversible / mutating ──────────────────────────────────────
BLOCK_CASES = [
    'git push',
    'git push origin master',
    'git push --force origin main',
    'cd /repo && git push -f',
    'git reset --hard HEAD~3',
    'git clean -fd',
    'git branch -D feature',
    'git push origin --delete oldbranch',
    'npm publish',
    'twine upload dist/*',
    'docker push myrepo/img:latest',
    'gh release create v1.2.3',
    'gh pr create --title x --body y',
    'gh pr merge 42',
    'gh api -X POST /repos/o/r/issues',
    'gcloud run deploy svc --image x',
    'aws s3 rm s3://bucket/key',
    'az vm delete --name x',
    'terraform apply -auto-approve',
    'kubectl delete pod x',
    'alembic upgrade head',
    'psql -c "DROP TABLE users"',
    'psql -c "TRUNCATE TABLE events"',
    'curl -X POST https://api.example.com/send -d @payload.json',
    'curl -X DELETE https://api.stripe.com/v1/x',
    'curl -T bigfile.zip https://upload.example.com/',
    'rm -rf /home/user/project/src',
    'rm -f important.txt',
    'rmdir /s C:\\data',
    'Remove-Item -Recurse -Force C:\\Users\\me\\docs',
    'curl -s -X POST http://localhost:5199/api/system/restart',
    'dd if=/dev/zero of=/dev/sda',
    'shutdown -h now',
]


@pytest.mark.parametrize('cmd', BLOCK_CASES)
def test_blocks_irreversible(cmd):
    d = classify_bash(cmd)
    assert d.blocked, f"fence FAILED to block catastrophic command: {cmd!r}"
    assert d.reason


# ── Must ALLOW — reversible / local ───────────────────────────────────────────
ALLOW_CASES = [
    'ls -la',
    'git status',
    'git add -A',
    'git commit -m "wip"',
    'git diff HEAD',
    'git log --oneline -20',
    'pytest -q',
    'grep -rn foo src/',
    'cat file.py',
    'python script.py',
    'npm install',
    'npm run build',
    'echo hello',
    # steward's own reporting — localhost API calls must pass
    'curl -s -X POST http://localhost:5199/api/project/abc/backlog/123/note -d \'{"text":"FYI"}\'',
    'curl -s http://localhost:5199/api/skills/search?q=x',
    'curl -s https://example.com/data.json',            # plain GET read, non-local
    'rm -rf _scratch/tmpdir',                            # scratch-scoped delete
    'rm /tmp/throwaway.log',
]


@pytest.mark.parametrize('cmd', ALLOW_CASES)
def test_allows_reversible(cmd):
    d = classify_bash(cmd)
    assert not d.blocked, f"fence WRONGLY blocked safe command {cmd!r}: {d.reason}"


def test_scratch_delete_with_escape_is_blocked():
    # scratch marker present but a `..` escape → still blocked (no false-allow)
    assert classify_bash('rm -rf _scratch/../secrets').blocked


def test_classify_action_bash():
    assert classify_action('Bash', {'command': 'git push'}).blocked
    assert not classify_action('Bash', {'command': 'ls'}).blocked


def test_classify_action_non_bash_default_allow():
    assert not classify_action('Read', {'file_path': '/x'}).blocked
    assert not classify_action('Write', {'file_path': '/repo/src/x.py'}).blocked


def test_classify_action_blocks_global_config_edit():
    assert classify_action('Write', {'file_path': '/home/me/.claude/settings.json'}).blocked
    assert classify_action('Edit', {'file_path': 'C:\\Users\\me\\.claude\\skills\\x\\SKILL.md'}).blocked


def test_empty_and_none_safe():
    assert not classify_bash('').blocked
    assert not classify_bash('   ').blocked
    assert not classify_action('Bash', {}).blocked


# ── Hook entrypoint (subprocess) — real stdin/stdout/exit-code contract ────────
def _run_hook(payload: dict):
    return subprocess.run(
        [sys.executable, str(FENCE)],
        input=json.dumps(payload), capture_output=True, text=True,
    )


def test_hook_blocks_with_exit_2_and_stderr():
    # Fail-closed contract: exit 2 + stderr reason, nothing on stdout.
    r = _run_hook({'tool_name': 'Bash', 'tool_input': {'command': 'git push'}})
    assert r.returncode == 2
    assert 'STEWARD FENCE blocked' in r.stderr
    assert r.stdout.strip() == ''


def test_hook_allows_with_exit_0():
    r = _run_hook({'tool_name': 'Bash', 'tool_input': {'command': 'git status'}})
    assert r.returncode == 0


def test_hook_fails_open_on_garbage():
    r = subprocess.run([sys.executable, str(FENCE)], input='not json',
                       capture_output=True, text=True)
    assert r.returncode == 0
