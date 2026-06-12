"""Agent character management — Prompt Builder Phase 1.

A "character" is a standard Claude Code subagent file: `<name>.md` with
YAML frontmatter (`name`, `description`) whose body is the subagent's
system prompt verbatim. CC reads them natively from `~/.claude/agents/`
(global) and `<project_path>/.claude/agents/` (project) — Mission Control
only provides the management surface, exactly like the Skills surface
does for `.claude/skills/`. Design: docs/PROMPT_BUILDER_DESIGN.md §3/§5.

Reuses skills.py's frontmatter parse/dump and kebab-case name validation
so the two surfaces never drift on format rules. Writes go ONLY under
`.claude/agents/` in either scope — never DATA_DIR.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import skills as _skills

GLOBAL_AGENTS_DIR = _skills.GLOBAL_AGENTS_DIR

# Characters ride --append-system-prompt in Phase 2 alongside MEMORY +
# rules + activity, under Windows' ~32 KB CreateProcess ceiling — hence a
# hard per-character cap well below it (design §8).
MAX_BODY_BYTES = 6 * 1024


def project_agents_dir(project_path: str | os.PathLike[str]) -> Path:
    return Path(project_path) / '.claude' / 'agents'


def _scope_dir(scope: str, project_path: str | None) -> Path:
    if scope == 'global':
        return GLOBAL_AGENTS_DIR
    if scope == 'project':
        if not project_path:
            raise ValueError('project_path required for project scope')
        return project_agents_dir(project_path)
    raise ValueError('scope must be global|project')


def _find_file(scope: str, name: str, project_path: str | None) -> Path | None:
    """Locate a character by name. New characters are written top-level as
    `<name>.md`, but CC scans recursively and imported community packs may
    nest files in subfolders — so lookup falls back to a recursive walk
    matching the file stem."""
    d = _scope_dir(scope, project_path)
    direct = d / f'{name}.md'
    if direct.is_file():
        return direct
    if not d.is_dir():
        return None
    try:
        for p in sorted(d.rglob('*.md')):
            if p.is_file() and p.stem == name:
                return p
    except OSError:
        return None
    return None


def _read_one(path: Path, scope: str, project_id: str | None,
              include_body: bool) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return None
    meta, body = _skills.parse_skill_md(text)
    rec: dict[str, Any] = {
        # Frontmatter `name` wins for display; the file stem is the
        # identity used in URLs (it's what delete/read look up).
        'name': path.stem,
        'display_name': str(meta.get('name') or path.stem),
        'description': str(meta.get('description') or ''),
        'scope': scope,
        'file': path.name,
        'size': len(text.encode('utf-8')),
    }
    if project_id and scope == 'project':
        rec['project_id'] = project_id
    if include_body:
        rec['body'] = body
    return rec


def _scan_dir(dirpath: Path, scope: str, project_id: str | None,
              include_body: bool) -> list[dict[str, Any]]:
    if not dirpath.is_dir():
        return []
    out: list[dict[str, Any]] = []
    try:
        files = sorted(dirpath.rglob('*.md'))
    except OSError:
        return []
    for p in files:
        if not p.is_file():
            continue
        rec = _read_one(p, scope, project_id, include_body)
        if rec is not None:
            out.append(rec)
    return out


def list_characters(project_path: str | None = None,
                    project_id: str | None = None,
                    include_body: bool = False) -> list[dict[str, Any]]:
    """Global pool + (when a project is given) that project's pool.
    Project-scope entries shadow-flag a same-named global, mirroring the
    Skills surface semantics."""
    items = _scan_dir(GLOBAL_AGENTS_DIR, 'global', None, include_body)
    if project_path:
        proj = _scan_dir(project_agents_dir(project_path), 'project',
                         project_id, include_body)
        proj_names = {r['name'] for r in proj}
        for r in items:
            if r['name'] in proj_names:
                r['shadowed_by_project'] = True
        items = proj + items
    return items


def read_character(scope: str, name: str, project_path: str | None = None,
                   project_id: str | None = None,
                   include_body: bool = True) -> dict[str, Any] | None:
    path = _find_file(scope, name, project_path)
    if path is None:
        return None
    return _read_one(path, scope, project_id, include_body)


def write_character(scope: str, name: str, description: str, body: str,
                    project_path: str | None = None,
                    overwrite: bool = False) -> dict[str, Any]:
    """Create or update `<scope agents dir>/<name>.md`. Raises ValueError on
    bad input, FileExistsError on collision without overwrite."""
    err = _skills.validate_name(name)
    if err:
        raise ValueError(err)
    description = (description or '').strip()
    if not description:
        raise ValueError('description is required (it drives auto-delegation)')
    body = (body or '').strip()
    if not body:
        raise ValueError('body is required — it is the character\'s system prompt')
    if len(body.encode('utf-8')) > MAX_BODY_BYTES:
        raise ValueError(
            f'body too large (max {MAX_BODY_BYTES // 1024} KB — characters '
            f'ride inside the agent system prompt)')

    existing = _find_file(scope, name, project_path)
    if existing is not None and not overwrite:
        raise FileExistsError(f'character "{name}" already exists in {scope} scope')

    d = _scope_dir(scope, project_path)
    d.mkdir(parents=True, exist_ok=True)
    # Updates land on the file we found (which may be nested); creates go
    # top-level.
    path = existing if existing is not None else d / f'{name}.md'
    text = _skills.dump_skill_md({'name': name, 'description': description},
                                 body + ('\n' if not body.endswith('\n') else ''))
    path.write_text(text, encoding='utf-8')
    rec = _read_one(path, scope, None, include_body=False)
    return rec if rec is not None else {'name': name, 'scope': scope}


def delete_character(scope: str, name: str,
                     project_path: str | None = None) -> bool:
    path = _find_file(scope, name, project_path)
    if path is None:
        return False
    path.unlink()
    return True
