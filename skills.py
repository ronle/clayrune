"""Skills filesystem layer for Mission Control.

Skills are Anthropic-format SKILL.md folders consumed natively by Claude Code:

  ~/.claude/skills/<name>/SKILL.md            ← global (visible everywhere)
  <project_path>/.claude/skills/<name>/SKILL.md ← project-local (only in that project)
  ~/.claude/skills.archive/<name>/SKILL.md    ← archived (hidden from CC, kept around)

MC does NOT teach CC about skills — CC already loads them natively at session
start. MC's job is purely management: list, read, write, archive, search,
import (paste / folder / git URL / cross-project copy).

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
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── Paths ────────────────────────────────────────────────────────────────────

def _home() -> Path:
    return Path(os.environ.get('USERPROFILE') or os.environ.get('HOME') or str(Path.home()))


GLOBAL_SKILLS_DIR = _home() / '.claude' / 'skills'
ARCHIVE_SKILLS_DIR = _home() / '.claude' / 'skills.archive'
STAGING_SKILLS_DIR = _home() / '.claude' / 'skills.staging'  # transient — git imports wait here for picker selection

# Sibling CC component directories — used by full-plugin install to drop
# plugin components alongside skills. We do NOT manage them in MC's UI; CC
# reads them natively.
GLOBAL_COMMANDS_DIR = _home() / '.claude' / 'commands'
GLOBAL_AGENTS_DIR = _home() / '.claude' / 'agents'
PLUGIN_MANIFEST_DIRNAME = '.claude-plugin'


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


# ── Import (paste / folder / git URL) ───────────────────────────────────────

_GIT_URL_RE = re.compile(
    r'^(https?://|git@[^:/\s]+:|ssh://git@|git://)[a-zA-Z0-9._\-:/~]+(\.git)?/?$'
)

# Matches GitHub web-UI tree/blob URLs we can auto-translate to clone URLs.
#   https://github.com/<owner>/<repo>/tree/<ref>/<subpath>
#   https://github.com/<owner>/<repo>/blob/<ref>/<path-to-file>
_GH_TREE_RE = re.compile(
    r'^(?P<base>https?://github\.com/[^/]+/[^/?#]+?)'
    r'(?:\.git)?'
    r'/(?P<kind>tree|blob)/(?P<ref>[^/]+)'
    r'(?:/(?P<subpath>.*?))?'
    r'/?$'
)


def _safe_skill_name_from(meta: dict[str, Any], folder_name: str, fallback: str = '') -> str:
    """Pick a skill name preferring frontmatter -> folder name -> fallback.

    Returns a kebab-case-normalized string. Caller still has to validate.
    """
    candidate = (meta.get('name') or folder_name or fallback or '').strip().lower()
    candidate = re.sub(r'[^a-z0-9-]+', '-', candidate)
    candidate = re.sub(r'-+', '-', candidate).strip('-')
    return candidate or 'imported-skill'


def _scan_for_skills(root: Path, max_depth: int = 3) -> list[dict[str, Any]]:
    """Find all SKILL.md files under root (depth-capped).

    Each result: {rel_dir, abs_dir, name, description, has_subassets}.
    `rel_dir` is relative to root and uniquely identifies the skill.
    """
    results: list[dict[str, Any]] = []
    if not root.exists() or not root.is_dir():
        return results

    root_abs = root.resolve()
    for path in root.rglob('SKILL.md'):
        try:
            rel = path.parent.resolve().relative_to(root_abs)
        except Exception:
            continue
        depth = len(rel.parts)
        if depth > max_depth:
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except Exception:
            continue
        meta, _ = parse_skill_md(text)
        rel_dir = str(rel) if str(rel) != '.' else ''
        folder_name = path.parent.name if depth > 0 else root_abs.name
        name = _safe_skill_name_from(meta, folder_name)
        # Detect supporting assets (anything else in the skill folder)
        has_subassets = any(
            child.name != 'SKILL.md' for child in path.parent.iterdir()
        )
        results.append({
            'name': name,
            'rel_dir': rel_dir,
            'abs_dir': str(path.parent),
            'description': (meta.get('description') or '').strip(),
            'has_subassets': has_subassets,
        })
    # Stable ordering: shallow first, then alphabetical
    results.sort(key=lambda r: (r['rel_dir'].count('/'), r['name']))
    return results


def import_from_paste(
    content: str,
    scope: str,
    project_path: str | None = None,
    project_id: str | None = None,
    name_override: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Import a skill from a pasted SKILL.md string."""
    if not content or not content.strip():
        raise ValueError('content is empty')
    meta, body = parse_skill_md(content)
    name = (name_override or meta.get('name') or '').strip()
    if not name:
        raise ValueError('skill name missing — provide one or include `name:` in frontmatter')
    err = validate_name(name)
    if err:
        raise ValueError(err)
    description = (meta.get('description') or '').strip()
    if not description:
        raise ValueError('description missing — required in frontmatter')
    return write_skill(
        name=name,
        description=description,
        body=body,
        scope=scope,
        project_path=project_path,
        project_id=project_id,
        extra_meta={k: v for k, v in meta.items() if k not in ('name', 'description')},
        overwrite=overwrite,
    )


def import_from_folder(
    src_path: str | os.PathLike,
    scope: str,
    project_path: str | None = None,
    project_id: str | None = None,
    name_override: str | None = None,
    selected_rel_dir: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Import a skill from an on-disk folder.

    Folder shapes accepted:
      - <src>/SKILL.md              → install <src> as the skill folder
      - <src>/<name>/SKILL.md       → install the named subfolder
      - <src>/skills/<name>/SKILL.md (or any nested) → require selected_rel_dir
    """
    src = Path(src_path).expanduser()
    if not src.exists():
        raise FileNotFoundError(f'path does not exist: {src}')
    if not src.is_dir():
        raise ValueError(f'not a directory: {src}')

    candidates = _scan_for_skills(src, max_depth=3)
    plugin_info = detect_plugin_at(src)

    if not candidates:
        if plugin_info:
            kinds = []
            if plugin_info['command_files']: kinds.append(f"{len(plugin_info['command_files'])} command(s)")
            if plugin_info['agent_files']: kinds.append(f"{len(plugin_info['agent_files'])} sub-agent(s)")
            if plugin_info['hook_files']: kinds.append(f"{len(plugin_info['hook_files'])} hook(s)")
            kinds_str = ', '.join(kinds) if kinds else 'no managed components'
            raise ValueError(
                f"This is the Anthropic plugin \"{plugin_info['name']}\" but it contains no skills "
                f"(only {kinds_str}). Clayrune manages skills; for the rest, install via CC's "
                f"/plugin command instead."
            )
        raise ValueError('no SKILL.md found in this folder (searched 3 levels deep)')

    # When a plugin is detected, always return the picker (skill-only vs full-plugin
    # decision belongs to the user, not the importer).
    if plugin_info or (len(candidates) > 1 and not selected_rel_dir):
        return {
            'multiple': True,
            'candidates': candidates,
            **({'plugin': _plugin_summary_for_response(plugin_info)} if plugin_info else {}),
        }

    target = candidates[0] if len(candidates) == 1 else next(
        (c for c in candidates if c['rel_dir'] == selected_rel_dir), None
    )
    if not target:
        raise ValueError(f'selected skill folder not found among candidates: {selected_rel_dir}')

    return _install_skill_dir(
        Path(target['abs_dir']),
        name_override=name_override or target['name'],
        scope=scope,
        project_path=project_path,
        project_id=project_id,
        overwrite=overwrite,
    )


def _install_skill_dir(
    src_dir: Path,
    name_override: str,
    scope: str,
    project_path: str | None,
    project_id: str | None,
    overwrite: bool,
) -> dict[str, Any]:
    """Copy a skill folder (containing SKILL.md + any extras) into the target scope."""
    err = validate_name(name_override)
    if err:
        raise ValueError(err)

    # Read frontmatter so the caller knows what landed
    skill_md_src = src_dir / 'SKILL.md'
    if not skill_md_src.exists():
        raise ValueError(f'SKILL.md missing in {src_dir}')
    meta, body = parse_skill_md(skill_md_src.read_text(encoding='utf-8'))
    description = (meta.get('description') or '').strip()
    if not description:
        raise ValueError('description missing in SKILL.md frontmatter')

    target_dir = _skill_dir(scope, name_override, project_path)
    if target_dir.exists() and not overwrite:
        raise FileExistsError(f'skill {name_override} already exists in {scope}')

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    if target_dir.exists() and overwrite:
        shutil.rmtree(target_dir)
    shutil.copytree(src_dir, target_dir)

    # Normalize the SKILL.md so frontmatter `name` matches the install name
    target_md = target_dir / 'SKILL.md'
    new_meta = dict(meta)
    new_meta['name'] = name_override
    target_md.write_text(dump_skill_md(new_meta, body), encoding='utf-8')

    return _read_one(target_dir, scope, project_id)  # type: ignore[return-value]


def is_valid_git_url(url: str) -> bool:
    url = (url or '').strip()
    return bool(_GIT_URL_RE.match(url) or _GH_TREE_RE.match(url))


def normalize_git_url(url: str) -> dict[str, Any]:
    """Translate a Git URL (or a GitHub web tree/blob URL) into the parts we need.

    Returns {clone_url, ref, subpath}. `ref` and `subpath` are None for bare
    repo URLs. For tree/blob URLs, subpath is the directory inside the repo
    that should be scanned for SKILL.md (blob URLs pointing at SKILL.md have
    the file portion stripped to its containing dir).
    """
    raw = (url or '').strip()
    if not raw:
        return {'clone_url': '', 'ref': None, 'subpath': None}

    m = _GH_TREE_RE.match(raw)
    if m:
        base = m.group('base').rstrip('/')
        ref = m.group('ref')
        kind = m.group('kind')
        sub = (m.group('subpath') or '').strip('/')
        if kind == 'blob' and sub:
            # blob URLs point at a specific file; scope the scan to its parent dir
            parts = sub.split('/')
            if parts[-1].lower().endswith('.md'):
                sub = '/'.join(parts[:-1])
        clone_url = base if base.endswith('.git') else base + '.git'
        return {'clone_url': clone_url, 'ref': ref, 'subpath': sub or None}

    # Bare repo URL — pass through unchanged
    return {'clone_url': raw.rstrip('/'), 'ref': None, 'subpath': None}


def git_clone_to_staging(url: str, ref: str | None = None, timeout: int = 60) -> dict[str, Any]:
    """Shallow-clone a repo into a fresh staging directory under STAGING_SKILLS_DIR.

    Accepts bare repo URLs AND GitHub web-UI tree/blob URLs (we translate to
    the underlying clone URL + extract the ref / subpath). When a subpath is
    detected, the cloned tree is trimmed so only that subpath remains in the
    staging dir — install_from_staging stays unchanged.

    Returns {staging_id, candidates}. Caller is responsible for invoking
    `install_from_staging(staging_id, rel_dir, ...)` next.
    """
    url = (url or '').strip()
    if not is_valid_git_url(url):
        raise ValueError('not a recognizable Git URL')

    parsed = normalize_git_url(url)
    clone_url = parsed['clone_url']
    parsed_ref = parsed['ref']
    subpath = parsed['subpath']
    effective_ref = ref or parsed_ref  # explicit ref param overrides URL-embedded ref

    STAGING_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    staging_id = uuid.uuid4().hex[:12]
    staging_path = STAGING_SKILLS_DIR / staging_id

    cmd = ['git', 'clone', '--depth', '1']
    if effective_ref:
        cmd += ['--branch', effective_ref]
    # `--` terminates option parsing so a hostile URL/ref can't smuggle a git
    # flag (e.g. --upload-pack=…) into the positional slots.
    cmd += ['--', clone_url, str(staging_path)]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            text=True,
        )
    except FileNotFoundError:
        raise RuntimeError('git is not on PATH — install git or import the skill manually')
    except subprocess.TimeoutExpired:
        # Clean up partial clone
        shutil.rmtree(staging_path, ignore_errors=True)
        raise RuntimeError(f'git clone timed out after {timeout}s')

    if result.returncode != 0:
        shutil.rmtree(staging_path, ignore_errors=True)
        stderr = (result.stderr or '').strip()[:500]
        raise RuntimeError(f'git clone failed: {stderr}')

    # Strip the .git folder so the clone behaves like a plain copy of files
    git_meta = staging_path / '.git'
    if git_meta.exists():
        shutil.rmtree(git_meta, ignore_errors=True)

    # If the URL specified a subdirectory inside the repo, trim the staging
    # tree down to just that subdirectory's contents so the rest of the
    # pipeline stays unchanged.
    if subpath:
        sub = (staging_path / subpath).resolve()
        try:
            sub.relative_to(staging_path.resolve())
        except ValueError:
            shutil.rmtree(staging_path, ignore_errors=True)
            raise ValueError(f'invalid subpath: {subpath}')
        if not sub.exists() or not sub.is_dir():
            shutil.rmtree(staging_path, ignore_errors=True)
            raise ValueError(
                f'subpath "{subpath}" not found in repo. The URL points at a '
                f'directory inside the repo; verify the path and ref are correct.'
            )
        # Move subpath contents to a sibling, then swap so staging_path now
        # holds only the requested subdirectory.
        tmp_holder = staging_path.parent / (staging_id + '-sub')
        shutil.move(str(sub), str(tmp_holder))
        shutil.rmtree(staging_path, ignore_errors=True)
        shutil.move(str(tmp_holder), str(staging_path))

    candidates = _scan_for_skills(staging_path, max_depth=3)
    plugin_info = detect_plugin_at(staging_path)

    if not candidates:
        if plugin_info:
            # Plugin folder with no skills (commands/hooks/agents only)
            shutil.rmtree(staging_path, ignore_errors=True)
            kinds = []
            if plugin_info['command_files']: kinds.append(f"{len(plugin_info['command_files'])} command(s)")
            if plugin_info['agent_files']: kinds.append(f"{len(plugin_info['agent_files'])} sub-agent(s)")
            if plugin_info['hook_files']: kinds.append(f"{len(plugin_info['hook_files'])} hook(s)")
            kinds_str = ', '.join(kinds) if kinds else 'no managed components'
            raise ValueError(
                f"This is the Anthropic plugin \"{plugin_info['name']}\" but it contains no skills "
                f"(only {kinds_str}). Clayrune manages skills; for the rest, install via CC's "
                f"/plugin command instead."
            )
        shutil.rmtree(staging_path, ignore_errors=True)
        msg = 'no SKILL.md found in cloned repo (searched 3 levels deep)'
        if subpath:
            msg = f'no SKILL.md found under "{subpath}" (searched 3 levels deep). The folder you pointed at doesn\'t look like a skill.'
        raise ValueError(msg)

    out = {
        'staging_id': staging_id,
        'candidates': candidates,
        'normalized': {'clone_url': clone_url, 'ref': effective_ref, 'subpath': subpath},
    }
    if plugin_info:
        out['plugin'] = _plugin_summary_for_response(plugin_info)
    return out


def _plugin_summary_for_response(info: dict[str, Any]) -> dict[str, Any]:
    """Trim a plugin-info dict down to what the frontend needs."""
    return {
        'name': info['name'],
        'skill_count': len(info['skill_dirs']),
        'command_count': len(info['command_files']),
        'agent_count': len(info['agent_files']),
        'hook_count': len(info['hook_files']),
        'has_hooks': info['has_hooks'],
        'readme_excerpt': info['readme_excerpt'],
        'root_path': info['root_path'],
    }


def install_from_staging(
    staging_id: str,
    rel_dir: str,
    scope: str,
    project_path: str | None = None,
    project_id: str | None = None,
    name_override: str | None = None,
    overwrite: bool = False,
    cleanup: bool = False,
) -> dict[str, Any]:
    """Install a specific skill from a previously-staged git clone."""
    staging_path = STAGING_SKILLS_DIR / staging_id
    if not staging_path.exists() or not staging_path.is_dir():
        raise FileNotFoundError(f'staging dir not found: {staging_id}')
    # Defensive path check — rel_dir must stay inside staging_path
    src_dir = (staging_path / rel_dir).resolve() if rel_dir else staging_path.resolve()
    try:
        src_dir.relative_to(staging_path.resolve())
    except ValueError:
        raise ValueError('selected path escapes staging area')
    if not src_dir.exists():
        raise FileNotFoundError(f'selected skill folder not found in staging: {rel_dir}')

    # Derive name if not provided
    skill_md = src_dir / 'SKILL.md'
    if skill_md.exists():
        meta, _ = parse_skill_md(skill_md.read_text(encoding='utf-8'))
        derived = _safe_skill_name_from(meta, src_dir.name)
    else:
        derived = src_dir.name
    name = (name_override or derived).strip()

    rec = _install_skill_dir(
        src_dir,
        name_override=name,
        scope=scope,
        project_path=project_path,
        project_id=project_id,
        overwrite=overwrite,
    )

    if cleanup:
        shutil.rmtree(staging_path, ignore_errors=True)
    return rec


# ── Anthropic-plugin detection + full-plugin install ────────────────────────
#
# A plugin folder is identified by `.claude-plugin/` (Anthropic's manifest
# directory). It may contain:
#   skills/<name>/SKILL.md     — managed by MC (this module)
#   commands/<name>.md         — slash commands (CC reads from ~/.claude/commands)
#   agents/<name>.md           — sub-agent definitions (CC reads from ~/.claude/agents)
#   hooks/*                    — lifecycle scripts; CC registers them via
#                                ~/.claude/settings.json — NOT auto-installed by
#                                MC because hooks run arbitrary shell code on
#                                lifecycle events and registration is a stronger
#                                trust statement than copying data files.
#
# When MC detects a plugin in a cloned/folder import, the UI offers "Install
# skill(s) only" vs "Install full plugin". The latter copies skills + commands
# + agents and tells the user how to enable hooks via CC's own /plugin command.

def _read_manifest(plugin_root: Path) -> dict[str, Any]:
    """Read .claude-plugin/plugin.json if present, return {} on absence/parse error."""
    manifest_path = plugin_root / PLUGIN_MANIFEST_DIRNAME / 'plugin.json'
    if not manifest_path.exists():
        return {}
    try:
        return json.loads(manifest_path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _list_md_files(dirpath: Path) -> list[str]:
    """Return relative file names of *.md files directly under dirpath."""
    if not dirpath.exists() or not dirpath.is_dir():
        return []
    return sorted(
        p.name for p in dirpath.iterdir()
        if p.is_file() and p.suffix.lower() == '.md'
    )


def _list_any_files(dirpath: Path) -> list[str]:
    """Return relative file names of any files directly under dirpath."""
    if not dirpath.exists() or not dirpath.is_dir():
        return []
    return sorted(p.name for p in dirpath.iterdir() if p.is_file())


def detect_plugin_at(root: Path | str) -> dict[str, Any] | None:
    """If `root` looks like an Anthropic plugin folder, return its info.

    A plugin folder has a `.claude-plugin/` sibling. We also accept the
    parent-of-skills shape: if root has skills/, commands/, or agents/ as
    direct children AND a sibling .claude-plugin/, treat it as a plugin.

    Returns: {name, manifest, readme_excerpt, skill_dirs, command_files,
              agent_files, hook_files, has_hooks, root_path}
    Or None when the directory isn't a plugin.
    """
    root = Path(root)
    if not root.exists() or not root.is_dir():
        return None
    if not (root / PLUGIN_MANIFEST_DIRNAME).exists():
        return None

    manifest = _read_manifest(root)
    name = (manifest.get('name') or root.name).strip()

    skill_dirs: list[str] = []
    if (root / 'skills').exists():
        for child in (root / 'skills').iterdir():
            if child.is_dir() and (child / 'SKILL.md').exists():
                skill_dirs.append(child.name)
    skill_dirs.sort()

    command_files = _list_md_files(root / 'commands')
    agent_files = _list_md_files(root / 'agents')
    hook_files = _list_any_files(root / 'hooks')

    readme_path = root / 'README.md'
    readme_excerpt = ''
    if readme_path.exists():
        try:
            readme_excerpt = readme_path.read_text(encoding='utf-8')[:600]
        except Exception:
            pass

    return {
        'name': name,
        'manifest': manifest,
        'readme_excerpt': readme_excerpt,
        'skill_dirs': skill_dirs,
        'command_files': command_files,
        'agent_files': agent_files,
        'hook_files': hook_files,
        'has_hooks': bool(hook_files),
        'root_path': str(root),
    }


def install_full_plugin(
    plugin_root: Path | str,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Install all skill + command + agent components of a plugin folder.

    Hooks are NOT installed: registration requires modifying
    ~/.claude/settings.json with author-supplied event/script bindings, which
    is arbitrary code execution and a stronger trust statement than copying
    data. The caller surfaces a note pointing at CC's `/plugin install`.

    All components install to GLOBAL scope (~/.claude/{skills,commands,agents}).
    Project-scope full-plugin install is not supported in v1.

    Returns: {
      plugin_name,
      installed: {skills: [names], commands: [names], agents: [names]},
      skipped:   {hooks: [names], collisions: [{type, name, reason}]},
      message: human-readable summary,
    }
    """
    root = Path(plugin_root)
    info = detect_plugin_at(root)
    if not info:
        raise ValueError(f'not a plugin folder (no {PLUGIN_MANIFEST_DIRNAME}/): {root}')

    GLOBAL_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    GLOBAL_COMMANDS_DIR.mkdir(parents=True, exist_ok=True)
    GLOBAL_AGENTS_DIR.mkdir(parents=True, exist_ok=True)

    installed = {'skills': [], 'commands': [], 'agents': []}
    collisions: list[dict[str, str]] = []

    # 1. Skills
    for sname in info['skill_dirs']:
        src = root / 'skills' / sname
        dest = GLOBAL_SKILLS_DIR / sname
        if dest.exists() and not overwrite:
            collisions.append({'type': 'skill', 'name': sname, 'reason': 'already exists'})
            continue
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        # Normalize frontmatter name to match the install name
        md = dest / 'SKILL.md'
        if md.exists():
            meta, body = parse_skill_md(md.read_text(encoding='utf-8'))
            meta['name'] = sname
            md.write_text(dump_skill_md(meta, body), encoding='utf-8')
        installed['skills'].append(sname)

    # 2. Commands (flat .md files)
    for cname in info['command_files']:
        src = root / 'commands' / cname
        dest = GLOBAL_COMMANDS_DIR / cname
        if dest.exists() and not overwrite:
            collisions.append({'type': 'command', 'name': cname, 'reason': 'already exists'})
            continue
        shutil.copy2(src, dest)
        installed['commands'].append(cname)

    # 3. Agents (flat .md files)
    for aname in info['agent_files']:
        src = root / 'agents' / aname
        dest = GLOBAL_AGENTS_DIR / aname
        if dest.exists() and not overwrite:
            collisions.append({'type': 'agent', 'name': aname, 'reason': 'already exists'})
            continue
        shutil.copy2(src, dest)
        installed['agents'].append(aname)

    # 4. Hooks intentionally NOT installed
    skipped_hooks = list(info['hook_files'])

    # Build a one-line summary
    parts = []
    if installed['skills']:
        parts.append(f"{len(installed['skills'])} skill" + ('' if len(installed['skills']) == 1 else 's'))
    if installed['commands']:
        parts.append(f"{len(installed['commands'])} command" + ('' if len(installed['commands']) == 1 else 's'))
    if installed['agents']:
        parts.append(f"{len(installed['agents'])} sub-agent" + ('' if len(installed['agents']) == 1 else 's'))
    summary = 'Installed ' + (', '.join(parts) if parts else 'nothing new')
    if skipped_hooks:
        summary += f". {len(skipped_hooks)} hook(s) NOT installed — use CC's /plugin command to register hooks."
    if collisions:
        summary += f" Skipped {len(collisions)} component(s) that already exist."

    return {
        'plugin_name': info['name'],
        'installed': installed,
        'skipped': {'hooks': skipped_hooks, 'collisions': collisions},
        'message': summary,
    }


def cleanup_stale_staging(max_age_hours: int = 24) -> int:
    """Remove staging dirs older than max_age_hours. Returns count cleaned."""
    if not STAGING_SKILLS_DIR.exists():
        return 0
    cutoff = datetime.now().timestamp() - max_age_hours * 3600
    removed = 0
    for child in STAGING_SKILLS_DIR.iterdir():
        try:
            if child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        except Exception:
            continue
    return removed


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
