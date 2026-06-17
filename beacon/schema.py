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
DONE_MAX = 300
STANDING_MAX = 300
NEXT_MAX = 220

BRIEF_FIELDS = ('done', 'standing', 'next')


def clamp(s, n: int) -> str:
    """Collapse whitespace to single spaces and hard-truncate to n chars with
    an ellipsis. Single-line output (headlines/brief fields never wrap)."""
    s = ' '.join(str(s or '').split())
    if len(s) > n:
        s = s[: n - 1].rstrip() + '…'  # …
    return s


def normalize_brief(raw: dict) -> dict:
    """Coerce a model's (possibly sloppy) JSON into the strict 4-field brief,
    applying caps. Missing/empty fields become 'unavailable' rather than
    blocking — degrade gracefully, never raise."""
    def g(k):
        return str((raw or {}).get(k, '') or '').strip()

    return {
        'headline': clamp(g('headline') or 'unavailable', HEADLINE_MAX),
        'done': clamp(g('done') or 'unavailable', DONE_MAX),
        'standing': clamp(g('standing') or 'unavailable', STANDING_MAX),
        'next': clamp(g('next') or 'unavailable', NEXT_MAX),
    }


def empty_heartbeat(project_id: str, name: str = '') -> dict:
    return {
        'project': name or project_id,
        'project_id': project_id,
        'updated_at': None,
        'headline': '',
        'live': 'resting',
        'brief': {'done': 'unavailable', 'standing': 'unavailable', 'next': 'unavailable'},
        'blocker': None,
    }
