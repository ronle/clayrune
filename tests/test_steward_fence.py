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


# ── Self-gating: fence enforces ONLY for steward-cycle sessions ───────────────
def _transcript(tmp_path, first_user_text):
    p = tmp_path / 'transcript.jsonl'
    p.write_text(json.dumps({'type': 'user',
                             'message': {'role': 'user', 'content': first_user_text}}) + '\n',
                 encoding='utf-8')
    return str(p)


def test_steward_session_is_fenced(tmp_path):
    tp = _transcript(tmp_path, '[Steward cycle] You are the steward...')
    r = _run_hook({'tool_name': 'Bash', 'tool_input': {'command': 'git push'},
                   'transcript_path': tp})
    assert r.returncode == 2  # steward cycle → enforced


def test_dev_session_is_NOT_fenced(tmp_path):
    # A normal dev/manual session (no steward marker) must run unfenced.
    tp = _transcript(tmp_path, 'hey can you push this branch for me')
    r = _run_hook({'tool_name': 'Bash', 'tool_input': {'command': 'git push --force'},
                   'transcript_path': tp})
    assert r.returncode == 0  # confirmed non-steward → allowed


def test_steward_session_allows_reversible(tmp_path):
    tp = _transcript(tmp_path, '[Steward cycle] run one cycle')
    r = _run_hook({'tool_name': 'Bash', 'tool_input': {'command': 'git status'},
                   'transcript_path': tp})
    assert r.returncode == 0


def test_unknown_session_fails_closed(tmp_path):
    # No transcript → can't confirm dev → enforce (fail-closed; fence only lives
    # in steward-enabled projects anyway).
    r = _run_hook({'tool_name': 'Bash', 'tool_input': {'command': 'git push'}})
    assert r.returncode == 2


# ── Inert-prose masking (2026-07-16 precision fix) ────────────────────────────
# Real incident (2026-07-13 DECISION NEEDED note): the steward could not
# commit the wake-lock feature because the commit MESSAGE contained OS power
# vocabulary. Prose is data; only command-position text may block.

PROSE_ALLOW_CASES = [
    # The literal incident shape.
    'git commit -m "feat(wake-lock): keep machine awake, prevent system shutdown while agents run"',
    "git commit -m 'fix: handle reboot and shutdown power events in wake_lock'",
    # Repo heredoc commit convention (quoted delimiter), prose mentions blocked verbs.
    'git commit -m "$(cat <<\'EOF\'\nfeat: wake lock\n\nPrevents host shutdown/reboot while agents run.\nAlso documents the gcloud run deploy steps.\nEOF\n)"',
    # PowerShell literal here-string commit (repo convention for pwsh).
    "git commit -m @'\nfix: power handling\n\nCovers shutdown, reboot, and git push docs.\n'@",
    # Cloud words in prose.
    'git commit -m "docs: explain why terraform apply is human-gated"',
    'git commit --message="chore: note that aws s3 rm needs approval"',
]


@pytest.mark.parametrize('cmd', PROSE_ALLOW_CASES)
def test_prose_in_commit_messages_is_not_a_command(cmd):
    d = classify_bash(cmd)
    assert not d.blocked, f"prose false-positive: {d.reason!r} for {cmd!r}"


PROSE_STILL_BLOCK_CASES = [
    # Unmasked command tail after a masked message.
    'git commit -m "safe words" && shutdown /s',
    'git commit -m "safe words" && git push',
    "git commit -m 'safe' ; reboot",
    # Substitution inside a double-quoted message CAN execute — never masked.
    'git commit -m "$(shutdown /s)"',
    'git commit -m "`reboot`"',
    # Heredoc fed to an interpreter executes its body — header check catches it.
    "bash <<'EOF'\nshutdown /s\nEOF",
    "pwsh <<'EOF'\ngit push\nEOF",
    # Unquoted heredoc delimiter (expansion live) is never masked.
    'git commit -m "$(cat <<EOF\n$(git push)\nEOF\n)"',
]


@pytest.mark.parametrize('cmd', PROSE_STILL_BLOCK_CASES)
def test_masking_introduces_no_false_negatives(cmd):
    assert classify_bash(cmd).blocked, f"false NEGATIVE: {cmd!r}"


# ── Learning-loop supply-chain fence (committee M3, 2026-07-16) ───────────────
# Proposal frontmatter + skill-stats records are what decides loadout entry;
# an unattended agent must not be able to edit its own provenance.

SUPPLY_CHAIN_BLOCK_PATHS = [
    r'C:\repo\data\skills\_proposed\proj\2026-07-16T00-00-00-abcd-x\SKILL.md',
    'data/skills/_proposed/global/2026-07-16T00-00-00-abcd-x/EXPLORATION.md',
    'data/skills/_rejected/old/PREFERENCE.md',
    'data/projects/mission_control_skill_stats.json',
    r'C:\repo\data\projects\proj_skill_stats_archive.jsonl',
]


@pytest.mark.parametrize('path', SUPPLY_CHAIN_BLOCK_PATHS)
def test_fence_blocks_learning_supply_chain_writes(path):
    for tool in ('Write', 'Edit', 'MultiEdit'):
        d = classify_action(tool, {'file_path': path})
        assert d.blocked, f"{tool} to {path} must be fenced"


def test_fence_still_allows_ordinary_project_writes():
    for path in (r'C:\repo\server.py', 'docs/PLAN.md',
                 'data/projects/notes.md', 'data/uploads/x.png'):
        d = classify_action('Write', {'file_path': path})
        assert not d.blocked, f"ordinary write false-positive: {path}"
