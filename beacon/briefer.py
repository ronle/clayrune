"""Beacon brief generator — the Haiku 4-field summarizer.

Named `briefer` (not `distiller`) to avoid colliding with the existing root
`distiller.py` (Skills-Curation Phase 4) in the import namespace.

Reuses the canonical cheap-model path — ClaudeRuntime.oneshot(), exactly as
mc.memory._scribe_call does — rather than a bespoke `claude -p` shell-out. The
codebase already moved AWAY from free-roaming `claude -p` agents for file
mutation (condense redesign: 91 errors + 58 timeouts) toward bounded-input →
validated-JSON. This is a single read-only structured call, the safe shape.
"""
import json
from pathlib import Path

from ._config import CFG, _log
from . import schema

_BRIEF_PROMPT = """You are generating a STATUS HEARTBEAT for ONE project in a multi-project dashboard. You receive recent context for a single project (its description, recent activity, memory log, changelog). Produce a tight status brief so an operator can see where this project stands at a glance — across many projects — without opening it.

Return ONLY a JSON object. No prose, no markdown fences. EXACTLY these four string fields:

{
  "headline": "one verb-led line, MAX 70 chars — the 'where we left off'",
  "done": "1-2 sentences: what was actually accomplished most recently",
  "standing": "1-2 sentences: the current state. If something is blocked or paused, state WHY it is blocked, not just that it is",
  "next": "1 sentence: the single concrete, resumable next action"
}

Rules:
- Be specific and concrete. Name the actual thing, never 'the task'.
- Domain-neutral: this may be a coding, trading, ops, or research project. Do NOT assume code.
- headline is verb-led and <=70 chars.
- If the context is thin, say so honestly in 'standing' — do not invent detail.
- Output the raw JSON object only."""

_CTX_MAX = 8000


def gather_context(project: dict) -> str:
    """Assemble bounded recent context for one project. Domain-neutral sources:
    description/summary + recent activity_log + MEMORY.md tail + CHANGELOG tail
    (if the project even has one)."""
    parts = []
    name = project.get('name') or project.get('id') or 'project'
    parts.append(f"PROJECT: {name}")
    if project.get('domain'):
        parts.append(f"DOMAIN: {project['domain']}")
    if project.get('description'):
        parts.append(f"DESCRIPTION: {project['description']}")
    if project.get('summary'):
        parts.append(f"SUMMARY: {project['summary']}")

    al = project.get('activity_log') or []
    if al:
        parts.append("RECENT ACTIVITY (newest first):")
        for e in al[:12]:
            parts.append(f"  - [{e.get('ts', '')}] {e.get('msg', '')}")

    try:
        getp = CFG.get_memory_path
        mp = getp(project) if getp else None
        if mp and Path(mp).exists():
            txt = Path(mp).read_text(encoding='utf-8', errors='replace')
            if txt.strip():
                parts.append("MEMORY (tail):")
                parts.append(txt[-3000:])
    except Exception as e:
        _log(f"[beacon] gather memory tail failed for {project.get('id')}: {e}")

    try:
        pp = project.get('project_path') or ''
        if pp:
            cl = Path(pp) / 'CHANGELOG.md'
            if cl.exists():
                txt = cl.read_text(encoding='utf-8', errors='replace')
                if txt.strip():
                    parts.append("CHANGELOG (tail):")
                    parts.append(txt[-2000:])
    except Exception as e:
        _log(f"[beacon] gather changelog tail failed for {project.get('id')}: {e}")

    ctx = "\n".join(parts)
    return ctx[-_CTX_MAX:]


def _parse_json(raw: str):
    """Extract a JSON object from raw model output — tolerant of markdown
    fences / leading prose (the _condense_parse_json discipline)."""
    if not raw:
        return None
    i = raw.find('{')
    j = raw.rfind('}')
    if i < 0 or j < 0 or j < i:
        return None
    try:
        return json.loads(raw[i:j + 1])
    except Exception:
        return None


def _fallback(project: dict) -> dict:
    """When the model is unavailable or its output won't parse: keep a useful
    headline from the latest activity / summary, mark the brief unavailable.
    NEVER blocks the digest (brief §1.4)."""
    headline = ''
    al = project.get('activity_log') or []
    if al:
        headline = (al[0].get('msg') or '').strip()
    if not headline:
        headline = (project.get('summary') or '').strip()
    return {
        'headline': schema.clamp(headline or 'No recent activity captured', schema.HEADLINE_MAX),
        'done': 'unavailable',
        'standing': 'unavailable',
        'next': 'unavailable',
    }


def generate_brief(project: dict) -> dict:
    """Return {headline, done, standing, next}. Best-effort: any failure falls
    back to a raw-context headline rather than raising."""
    ctx = gather_context(project)
    if not ctx.strip():
        return _fallback(project)
    raw = None
    try:
        import agent_runtime as _agent_runtime  # lazy: keep beacon import light
        result = _agent_runtime.get_runtime('claude').oneshot(
            prompt=_BRIEF_PROMPT,
            model='haiku',
            stdin_text=ctx,
            cwd=str(Path.home()),
        )
        raw = result.text if result else None
    except Exception as e:
        _log(f"[beacon] brief model call failed for {project.get('id')}: {e}")
        raw = None
    if not raw:
        return _fallback(project)
    data = _parse_json(raw)
    if not isinstance(data, dict):
        _log(f"[beacon] brief JSON parse failed for {project.get('id')}")
        return _fallback(project)
    return schema.normalize_brief(data)
