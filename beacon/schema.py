"""Beacon heartbeat schema — field caps + normalization.

The field caps are load-bearing per the brief: they are what keep the expanded
view a *briefing* and not a wall of text. `done`/`standing`/`next` are hard
1-2-sentence fields; `headline` is one verb-led line.
"""

# Blocker taxonomy (brief §1.2). `stale` is computed by the aggregator from
# ABSENCE of signal, never written by a project.
BLOCKER_TYPES = ('plan_pending', 'question_pending', 'failed_resume', 'stale')

# `live` is a derived projection, not stored authoritatively (the aggregator
# overlays the real value from agent_sessions at read time). running | resting.
LIVE_STATES = ('running', 'resting')

HEADLINE_MAX = 70
# Each briefing field is two-level: a one-line summary (`line`, shown in the
# expanded row) + the full text (`detail`, revealed when that line is clicked).
# The line is a real condensation, not a truncated peek (the model writes it).
FIELD_LINE_MAX = 110
FIELD_DETAIL_MAX = 320

BRIEF_FIELDS = ('done', 'standing', 'next')


def clamp(s, n: int) -> str:
    """Collapse whitespace to single spaces and hard-truncate to n chars with
    an ellipsis. Single-line output (headlines/brief fields never wrap)."""
    s = ' '.join(str(s or '').split())
    if len(s) > n:
        s = s[: n - 1].rstrip() + '…'  # …
    return s


def _norm_field(raw_field) -> dict:
    """Coerce one briefing field into {line, detail}. Accepts the new nested
    {line, detail} shape OR a plain string (older briefs / sloppy model output):
    a string becomes the detail, with a clamped line as the collapsed summary."""
    if isinstance(raw_field, dict):
        line = str(raw_field.get('line', '') or '').strip()
        detail = str(raw_field.get('detail', '') or raw_field.get('full', '') or '').strip()
    else:
        detail = str(raw_field or '').strip()
        line = detail
    line = clamp(line or 'unavailable', FIELD_LINE_MAX)
    detail = clamp(detail or line, FIELD_DETAIL_MAX)
    return {'line': line, 'detail': detail}


def normalize_brief(raw: dict) -> dict:
    """Coerce a model's (possibly sloppy) JSON into the strict brief: a one-line
    headline + three {line, detail} fields. Missing/empty become 'unavailable'
    rather than blocking — degrade gracefully, never raise."""
    raw = raw or {}
    return {
        'headline': clamp(str(raw.get('headline', '') or '').strip() or 'unavailable', HEADLINE_MAX),
        'done': _norm_field(raw.get('done')),
        'standing': _norm_field(raw.get('standing')),
        'next': _norm_field(raw.get('next')),
    }


def empty_heartbeat(project_id: str, name: str = '') -> dict:
    return {
        'project': name or project_id,
        'project_id': project_id,
        'updated_at': None,
        'headline': '',
        'live': 'resting',
        'brief': {
            'done': {'line': 'unavailable', 'detail': 'unavailable'},
            'standing': {'line': 'unavailable', 'detail': 'unavailable'},
            'next': {'line': 'unavailable', 'detail': 'unavailable'},
        },
        'blocker': None,
    }
