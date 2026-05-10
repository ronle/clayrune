"""Skills filesystem layer for Mission Control.

Skills are Anthropic-format SKILL.md folders consumed natively by Claude Code:

  ~/.claude/skills/<name>/SKILL.md            ← global (visible everywhere)
  <project_path>/.claude/skills/<name>/SKILL.md ← project-local (only in that project)
  ~/.claude/skills.archive/<name>/SKILL.md    ← archived (hidden from CC, kept around)

MC does NOT teach CC about skills — CC already loads them natively at session
start. MC's job is purely management: list, read, write, archive, search.

Project skills shadow globals of the same name (CC's own resolution rule).
The list endpoint surfaces a `shadowed_by` field so the UI can badge it.

Frontmatter parser is intentionally tiny: handles `key: value` and folded
multi-line continuations between `---` fences. Avoids adding PyYAML as a dep.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── Paths ────────────────────────────────────────────────────────────────────

def _home() -> Path:
    return Path(os.environ.get('USERPROFILE') or os.environ.get('HOME') or str(Path.home()))


GLOBAL_SKILLS_DIR = _home() / '.claude' / 'skills'
ARCHIVE_SKILLS_DIR = _home() / '.claude' / 'skills.archive'


def project_skills_dir(project_path: str | os.PathLike) -> Path:
    return Path(project_path) / '.claude' / 'skills'


# ── Name validation ──────────────────────────────────────────────────────────

_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,63}$')


def validate_name(name: str) -> str | None:
    if not name:
        return 'Name is required'
    if not _NAME_RE.match(name):
        return 'Name must be kebab-case: lowercase letters, digits, hyphens; start with letter/digit; max 64 chars'
    return None


# ── Frontmatter parser/dumper ────────────────────────────────────────────────

_FENCE_RE = re.compile(r'^---\s*\n(.*?)\n---\s*(?:\n|$)', re.DOTALL)


def parse_skill_md(text: str) -> tuple[dict[str, Any], str]:
    """Split SKILL.md into (frontmatter_dict, body).

    If no frontmatter is present, returns ({}, full_text).
    """
    m = _FENCE_RE.match(text)
    if not m:
        return {}, text
    fm_raw = m.group(1)
    body = text[m.end():]
    return _parse_frontmatter(fm_raw), body


def _parse_frontmatter(raw: str) -> dict[str, Any]:
    """Tiny YAML-like parser. Supports:
      key: value
      key: |  (block scalar — preserves newlines)
      key: >  (folded scalar — joins with spaces)
      indented continuation lines.
    No nested maps, no flow-style. Sufficient for the SKILL.md schema.
    """
    out: dict[str, Any] = {}
    lines = raw.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith('#'):
            i += 1
            continue
        m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_-]*)\s*:\s*(.*)$', line)
        if not m:
            i += 1
            continue
        key, val = m.group(1), m.group(2).strip()
        if val in ('|', '>'):
            block_mode = val
            pieces: list[str] = []
            i += 1
            while i < len(lines):
                ln = lines[i]
                if ln.strip() == '' or ln.startswith(' ') or ln.startswith('\t'):
                    pieces.append(ln.lstrip())
                    i += 1
                else:
                    break
            joined = ('\n' if block_mode == '|' else ' ').join(pieces).strip()
            out[key] = joined
        else:
            # peek for indented continuations (folded plain scalar)
            cont: list[str] = [val]
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                if nxt.startswith(' ') or nxt.startswith('\t'):
                    cont.append(nxt.strip())
                    j += 1
                else:
                    break
            out[key] = _strip_quotes(' '.join(cont).strip())
            i = j
            continue
        # block scalar path already advanced i
    return out


def _strip_quotes(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


def dump_skill_md(meta: dict[str, Any], body: str) -> str:
    """Render SKILL.md with a frontmatter block.

    `name` and `description` come first if present (canonical order).
    """
    keys = list(meta.keys())
    ordered = []
    for canonical in ('name', 'description'):
        if canonical in keys:
            ordered.append(canonical)
            keys.remove(canonical)
    ordered.extend(keys)

    lines = ['---']
    for k in ordered:
        v = meta.get(k)
        if v is None:
            continue
        v_str = str(v)
        if '\n' in v_str:
            lines.append(f'{k}: |')
            for ln in v_str.split('\n'):
                lines.append(f'  {ln}')
        else:
            # Quote if value contains anything yaml-special
            if any(c in v_str for c in ':#&*!|>%@`') or v_str.strip() != v_str:
                v_str = '"' + v_str.replace('\\', '\\\\').replace('"', '\\"') + '"'
            lines.append(f'{k}: {v_str}')
    lines.append('---')
    lines.append('')
    return '\n'.join(lines) + body


# ── Skill IO ─────────────────────────────────────────────────────────────────

def _skill_dir(scope: str, name: str, project_path: str | None = None) -> Path:
    if scope == 'global':
        return GLOBAL_SKILLS_DIR / name
    if scope == 'archive':
        return ARCHIVE_SKILLS_DIR / name
    if scope == 'project':
        if not project_path:
            raise ValueError('project scope requires project_path')
        return project_skills_dir(project_path) / name
    raise ValueError(f'unknown scope: {scope}')


def _read_one(path: Path, scope: str, project_id: str | None = None) -> dict[str, Any] | None:
    skill_md = path / 'SKILL.md'
    if not skill_md.exists():
        return None
    try:
        text = skill_md.read_text(encoding='utf-8')
    except Exception:
        return None
    meta, body = parse_skill_md(text)
    name = meta.get('name') or path.name
    description = (meta.get('description') or '').strip()
    return {
        'name': name,
        'folder': path.name,
        'scope': scope,
        'project_id': project_id,
        'path': str(skill_md),
        'description': description,
        'body': body,
        'body_preview': body.strip()[:240],
        'mtime': skill_md.stat().st_mtime,
        'mtime_iso': datetime.fromtimestamp(skill_md.stat().st_mtime, tz=timezone.utc).isoformat(),
        'size': skill_md.stat().st_size,
        'frontmatter': meta,
    }


def _scan_dir(dirpath: Path, scope: str, project_id: str | None = None) -> list[dict[str, Any]]:
    if not dirpath.exists():
        return []
    out: list[dict[str, Any]] = []
    for child in sorted(dirpath.iterdir()):
        if not child.is_dir():
            continue
        rec = _read_one(child, scope, project_id)
        if rec:
            out.append(rec)
    return out


def list_skills(
    project_path: str | None = None,
    project_id: str | None = None,
    include_archived: bool = False,
    include_body: bool = False,
) -> list[dict[str, Any]]:
    """List skills across global + (optionally) project + (optionally) archive.

    Annotates shadowing: a global skill with the same name as a project skill
    gets `shadowed_by_project=True` in the output.
    """
    globals_ = _scan_dir(GLOBAL_SKILLS_DIR, 'global')
    project = _scan_dir(project_skills_dir(project_path), 'project', project_id) if project_path else []
    archived = _scan_dir(ARCHIVE_SKILLS_DIR, 'archive') if include_archived else []

    proj_names = {s['name'] for s in project}
    for g in globals_:
        if g['name'] in proj_names:
            g['shadowed_by_project'] = True

    all_skills = globals_ + project + archived
    if not include_body:
        for s in all_skills:
            s.pop('body', None)
    return all_skills


def read_skill(
    scope: str,
    name: str,
    project_path: str | None = None,
    project_id: str | None = None,
    include_body: bool = True,
) -> dict[str, Any] | None:
    path = _skill_dir(scope, name, project_path)
    rec = _read_one(path, scope, project_id)
    if rec and not include_body:
        rec.pop('body', None)
    return rec


def write_skill(
    name: str,
    description: str,
    body: str,
    scope: str,
    project_path: str | None = None,
    project_id: str | None = None,
    extra_meta: dict[str, Any] | None = None,
    overwrite: bool = True,
) -> dict[str, Any]:
    err = validate_name(name)
    if err:
        raise ValueError(err)
    if not description or not description.strip():
        raise ValueError('description is required')

    path = _skill_dir(scope, name, project_path)
    skill_md = path / 'SKILL.md'

    if skill_md.exists() and not overwrite:
        raise FileExistsError(f'skill {name} already exists in {scope}')

    meta: dict[str, Any] = {'name': name, 'description': description.strip()}
    if extra_meta:
        meta.update({k: v for k, v in extra_meta.items() if k not in ('name', 'description')})

    path.mkdir(parents=True, exist_ok=True)
    skill_md.write_text(dump_skill_md(meta, body), encoding='utf-8')
    return _read_one(path, scope, project_id)  # type: ignore[return-value]


def delete_skill(
    scope: str,
    name: str,
    project_path: str | None = None,
    archive: bool = True,
) -> dict[str, Any]:
    """Archive (global skills) or hard-delete.

    Project skills don't archive — they delete directly. (Archiving them
    globally would move files out of the user's project tree, which is
    confusing.) Archive flag is honored only for global skills.
    """
    path = _skill_dir(scope, name, project_path)
    if not path.exists():
        raise FileNotFoundError(f'skill not found: {scope}/{name}')

    if scope == 'global' and archive:
        ARCHIVE_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        dest = ARCHIVE_SKILLS_DIR / name
        if dest.exists():
            # already an archived copy — append timestamp
            ts = datetime.now().strftime('%Y%m%d%H%M%S')
            dest = ARCHIVE_SKILLS_DIR / f'{name}_{ts}'
        shutil.move(str(path), str(dest))
        return {'ok': True, 'action': 'archived', 'archived_path': str(dest)}

    shutil.rmtree(path)
    return {'ok': True, 'action': 'deleted'}


def restore_skill(name: str) -> dict[str, Any]:
    src = ARCHIVE_SKILLS_DIR / name
    if not src.exists():
        raise FileNotFoundError(f'archived skill not found: {name}')
    GLOBAL_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    dest = GLOBAL_SKILLS_DIR / name
    if dest.exists():
        raise FileExistsError(f'a live global skill named "{name}" already exists; rename one first')
    shutil.move(str(src), str(dest))
    return {'ok': True, 'restored_path': str(dest)}


# ── Search ───────────────────────────────────────────────────────────────────

def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r'[^a-zA-Z0-9]+', (text or '').lower()) if t]


def search_skills(
    query: str,
    project_path: str | None = None,
    project_id: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Rank skills across global + project pools by keyword overlap.

    Scoring: weighted hits in name (×3), description (×2), body (×1).
    Cheap, deterministic, sufficient for ≤500 skills.
    """
    qtok = _tokens(query)
    if not qtok:
        return []

    skills = list_skills(
        project_path=project_path,
        project_id=project_id,
        include_archived=False,
        include_body=True,
    )

    results = []
    for s in skills:
        name_t = _tokens(s.get('name', ''))
        desc_t = _tokens(s.get('description', ''))
        body_t = _tokens(s.get('body', ''))
        score = 0.0
        for q in qtok:
            if q in name_t:
                score += 3.0
            if q in desc_t:
                score += 2.0
            if q in body_t:
                score += 1.0
        if score > 0:
            entry = {
                'name': s['name'],
                'scope': s['scope'],
                'project_id': s.get('project_id'),
                'description': s['description'],
                'body_excerpt': s.get('body_preview', ''),
                'score': round(score, 2),
                'path': s['path'],
            }
            results.append(entry)
    results.sort(key=lambda x: x['score'], reverse=True)
    return results[:limit]


# ── Built-in install ─────────────────────────────────────────────────────────

_INSTALL_MARKER = '.mc-builtin-hash'


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def install_builtins(builtin_root: Path) -> dict[str, Any]:
    """Install/update built-in skills from `builtin_root` into ~/.claude/skills/.

    For each <name>/ subdir in builtin_root:
      - If target doesn't exist → copy it. Write hash marker.
      - If target exists AND has marker AND user hasn't modified SKILL.md →
        update from source, refresh marker.
      - If target exists AND has marker AND user HAS modified SKILL.md →
        leave alone (log "preserved").
      - If target exists with NO marker → leave alone (user-owned, never
        managed by MC).
    """
    if not builtin_root.exists():
        return {'installed': [], 'updated': [], 'preserved': [], 'skipped': []}

    GLOBAL_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    installed: list[str] = []
    updated: list[str] = []
    preserved: list[str] = []
    skipped: list[str] = []

    for src_dir in sorted(builtin_root.iterdir()):
        if not src_dir.is_dir():
            continue
        name = src_dir.name
        src_md = src_dir / 'SKILL.md'
        if not src_md.exists():
            continue
        dest_dir = GLOBAL_SKILLS_DIR / name
        dest_md = dest_dir / 'SKILL.md'
        marker = dest_dir / _INSTALL_MARKER
        src_hash = _file_sha256(src_md)

        if not dest_md.exists():
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src_dir, dest_dir, dirs_exist_ok=True)
            marker.write_text(src_hash, encoding='utf-8')
            installed.append(name)
            continue

        if not marker.exists():
            # User-owned (or installed before marker scheme) — never touch
            skipped.append(name)
            continue

        try:
            marker_hash = marker.read_text(encoding='utf-8').strip()
        except Exception:
            marker_hash = ''

        current_hash = _file_sha256(dest_md)
        if current_hash != marker_hash:
            preserved.append(name)
            continue

        if marker_hash == src_hash:
            skipped.append(name)
            continue

        # Hash matches our last install but differs from source → safe update
        shutil.copytree(src_dir, dest_dir, dirs_exist_ok=True)
        marker.write_text(src_hash, encoding='utf-8')
        updated.append(name)

    return {
        'installed': installed,
        'updated': updated,
        'preserved': preserved,
        'skipped': skipped,
    }


# ── Usage stats from CC transcripts ──────────────────────────────────────────

def _claude_projects_root() -> Path:
    return _home() / '.claude' / 'projects'


def skill_usage_stats(days: int = 30) -> dict[str, dict[str, Any]]:
    """Grep ~/.claude/projects/*/*.jsonl for Skill tool calls.

    Returns {skill_name -> {invocations, last_invoked_at, project_count}}.

    Looks for assistant messages with tool_use blocks where `name == "Skill"`
    and extracts the `skill` input parameter.
    """
    root = _claude_projects_root()
    if not root.exists():
        return {}

    cutoff_ts = (datetime.now(timezone.utc).timestamp() - days * 86400) if days > 0 else 0
    stats: dict[str, dict[str, Any]] = {}

    for proj_dir in root.iterdir():
        if not proj_dir.is_dir():
            continue
        for transcript in proj_dir.glob('*.jsonl'):
            if transcript.stat().st_mtime < cutoff_ts:
                continue
            try:
                with transcript.open('r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line or '"Skill"' not in line:
                            continue
                        try:
                            entry = json.loads(line)
                        except Exception:
                            continue
                        ts_raw = entry.get('timestamp') or ''
                        msg = entry.get('message') or {}
                        if msg.get('role') != 'assistant':
                            continue
                        for block in msg.get('content', []) or []:
                            if not isinstance(block, dict):
                                continue
                            if block.get('type') != 'tool_use':
                                continue
                            if block.get('name') != 'Skill':
                                continue
                            sk = (block.get('input') or {}).get('skill')
                            if not sk:
                                continue
                            rec = stats.setdefault(sk, {
                                'invocations': 0,
                                'last_invoked_at': '',
                                'projects': set(),
                            })
                            rec['invocations'] += 1
                            if ts_raw and ts_raw > rec['last_invoked_at']:
                                rec['last_invoked_at'] = ts_raw
                            rec['projects'].add(proj_dir.name)
            except Exception:
                continue

    # Serialize sets
    for sk, rec in stats.items():
        rec['project_count'] = len(rec['projects'])
        rec.pop('projects', None)
    return stats
