"""Clayrune Skills Curation — Phase 4 Distiller (v2.1).

The cross-session learning observer. Reads the same .jsonl Scribe reads,
extracts multi-class signals (topics, preferences, explorations), counts
recurrence across sessions, and proposes artifacts (SKILL / UPDATE /
EXPLORATION / PREFERENCE) into ``data/skills/_proposed/`` for human review.

Design: ``docs/SKILLS_CURATION_PHASE4_SPEC_V2.md`` (DRAFT v2.1
post-committee-review 2026-05-27, revised 2026-05-29). The locked
learning definition this implements: ``learning is when the agent's
effective behavior changes over time, driven by experience, without
the human having to type the change``
(``memory/decision_learning_definition.md``).

Best-effort posture (parent design + §2 principle 2): Distiller
failure NEVER breaks Scribe, MEMORY.md write, completion logging, or
the session lifecycle. Every entry point is wrapped in
``try/except: pass`` at the daemon-thread boundary; per-call failures
become structured telemetry, never raised.

Concurrency (§4.7–4.9):
  - Reads AND writes to ``_skill_stats.json`` go through
    ``_get_skill_stats_lock(project_id)``.
  - Cross-project aggregation walks lock-free with 3-retry parse
    (D3 ii Option B locked 2026-05-29).
  - Daemon-thread dispatch with non-blocking 2s semaphore acquire
    (D8: backpressure on the best-effort path).
  - Hard-kill recovery: signal commits before proposal-generate;
    outbox marker writes after successful artifact land (D7).

Sidecars live OUTSIDE DATA_DIR (project records dir) per the
load-bearing rule. ``_skill_stats.json`` and ``_skill_stats_summary.json``
ARE suffix-excluded in ``load_projects()`` because they're per-project
sidecars (flat-sidecar form: ``<pid>_skill_stats.json``).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections import Counter
from pathlib import Path

# ── Injected helpers (set by register()) ─────────────────────────────────────

_data_root: Path | None = None        # data/projects/
_skills_root: Path | None = None      # data/skills/
_atomic_write_text = None             # server._atomic_write_text
_scribe_call = None                   # server._scribe_call (cheap-model wrapper)
_scribe_render_transcript = None      # server._scribe_render_transcript
_log = None                           # server._log
_load_project = None
_save_project = None
_now_iso = None
_config_get = None                    # CONFIG.get-equivalent (callable: key, default)
_get_per_project_semaphore = None     # parent design BoundedSemaphore cap=2


def register(*, data_root: Path, skills_root: Path,
             atomic_write_text, scribe_call, scribe_render_transcript,
             log, load_project, save_project, now_iso, config_get,
             get_per_project_semaphore):
    """Inject server helpers — called once at startup."""
    global _data_root, _skills_root, _atomic_write_text, _scribe_call
    global _scribe_render_transcript, _log, _load_project, _save_project
    global _now_iso, _config_get, _get_per_project_semaphore
    _data_root = data_root
    _skills_root = skills_root
    _atomic_write_text = atomic_write_text
    _scribe_call = scribe_call
    _scribe_render_transcript = scribe_render_transcript
    _log = log
    _load_project = load_project
    _save_project = save_project
    _now_iso = now_iso
    _config_get = config_get
    _get_per_project_semaphore = get_per_project_semaphore


# ── Excluded sidecar suffixes (load-bearing — DATA_DIR pollution rule) ──────

# Single source of truth: load_projects() in server.py imports this constant
# (D9 + I2 + Seat 4 v2 Cond 6 closure). Any new sidecar added to data/projects/
# MUST be added here AND the parametric regression test
# tests/test_load_projects_sidecar_exclusions.py picks it up automatically.
EXCLUDED_SIDECAR_SUFFIXES = (
    '_agent_log.json',
    '_scribe_stats.json',
    '_skill_stats.json',
    '_skill_stats_summary.json',
)


# ── Closed vocabulary (D1 closure — Seat 1 Cond 1+2) ─────────────────────────

# Verbs: actions an agent takes. Enriched 2026-05-29 from the last ~200
# commits of this codebase (Seat 1 sampling). Adding a verb here propagates
# via Stage 1 extraction; remove only after evidence it's never used.
VERBS = frozenset({
    'add', 'archive', 'audit', 'backfill', 'bake', 'bundle', 'build',
    'cleanup', 'configure', 'daemonize', 'debug', 'delete', 'deploy',
    'design', 'diagnose', 'document', 'edit', 'enable', 'enrich', 'expose',
    'extract', 'fix', 'gate', 'generate', 'harden', 'ignore', 'index',
    'inject', 'install', 'instrument', 'interpret', 'lint', 'materialize',
    'migrate', 'mint', 'monitor', 'normalize', 'package', 'paginate',
    'parse', 'pin', 'polish', 'preflight', 'propagate', 'propose', 'query',
    'rebrand', 'redact', 'refactor', 'register', 'remove', 'rename',
    'replace', 'research', 'restore', 'retry', 'revert', 'revoke', 'route',
    'run', 'schedule', 'scope', 'search', 'seed', 'send', 'ship', 'sign',
    'simplify', 'skip', 'sort', 'split', 'swap', 'sync', 'test', 'trace',
    'unify', 'update', 'validate', 'wire', 'write',
})

# Nouns: things topics are ABOUT. Subsystem names (condense, scribe,
# distiller, hivemind, pair, mobile-pair, github-sync, project-sync) are
# NOUNS in v2.1 (D1 closure), not modifiers — they're routinely the
# grammatical noun of topics about them.
NOUNS = frozenset({
    'agent', 'alert', 'artifact', 'audit-log', 'auth', 'backlog', 'binding',
    'build', 'cache', 'callback', 'certificate', 'condense', 'config',
    'context', 'dashboard', 'dependency', 'deploy', 'diff', 'dispatch',
    'distiller', 'doc', 'endpoint', 'env-var', 'error', 'event', 'exception',
    'feature-flag', 'fingerprint', 'form', 'frontmatter', 'github-sync',
    'handler', 'hash', 'header', 'hivemind', 'hook', 'identity', 'incident',
    'indicator', 'integration', 'lock', 'log', 'marker', 'memory', 'message',
    'metadata', 'middleware', 'migration', 'mobile-pair', 'mode', 'model',
    'module', 'notification', 'output', 'package', 'pair', 'parser', 'path',
    'payload', 'permission', 'pipeline', 'plan', 'prompt', 'provider',
    'project-sync', 'push', 'queue', 'race', 'read-floor', 'record',
    'refresh', 'regex', 'render', 'report', 'request', 'resource',
    'response', 'route', 'schedule', 'schema', 'scope', 'scribe',
    'screenshot', 'script', 'secret', 'session', 'settings', 'sidecar',
    'signal', 'skill', 'sort-order', 'source', 'spec', 'state', 'status',
    'stream', 'suppression', 'table', 'target', 'telemetry', 'template',
    'terminal', 'test', 'threshold', 'throttle', 'timeout', 'token', 'tool',
    'transcript', 'transport', 'trigger', 'ui', 'update', 'upload', 'user',
    'validation', 'view', 'watermark', 'web', 'window', 'worker', 'workflow',
    'write',
})

# Modifiers: surface narrowing ONLY (not subsystem names). A modifier
# answers "which implementation of X" not "which X".
MODIFIERS = frozenset({
    'mobile', 'desktop', 'ios', 'android', 'web', 'cli', 'server', 'client',
    'frontend', 'backend', 'agent-side', 'mc-side',
})


# ── Defaults (mirrored from v2.1 spec §11; overridden via CONFIG / project) ──

_DEFAULTS_GLOBAL = {
    'distiller_enabled_global': True,
    'distiller_cross_project_enabled': True,
    'distiller_model': '',                                # → haiku
    'distiller_window_days': 30,
    'distiller_cost_cap_tokens_per_project_per_day': 100000,
    'distiller_proposal_dedupe_days': 7,
    'distiller_cross_project_walk_debounce_session_count': 5,
    'distiller_cross_project_walk_debounce_seconds': 600,
}

_DEFAULTS_PER_PROJECT = {
    'distiller_mode': 'proposed',                         # off | proposed | auto
    'distiller_min_recurrence': 3,
    'distiller_max_topics_per_session': 3,
    'distiller_max_preferences_per_session': 3,
    'distiller_max_explorations_per_session': 3,
    'distiller_min_turns': 5,
    'distiller_skip_errors': True,
}


def _cfg(key, default=None):
    """Read a global config key, falling back to the v2.1 default."""
    if _config_get is None:
        return _DEFAULTS_GLOBAL.get(key, default)
    return _config_get(key, _DEFAULTS_GLOBAL.get(key, default))


def _pcfg(project, key, default=None):
    """Read a per-project config key with v2.1 default fallback."""
    if project and key in project:
        return project[key]
    return _DEFAULTS_PER_PROJECT.get(key, default)


# ── Closed-vocabulary fingerprint (D2 closure — dual-layer, Option A) ────────

# Telemetry counters incremented by the fingerprint pure function.
# Counter names are stable strings used as _skill_stats.json keys.
TELEM_VOCABULARY_MISS = 'vocabulary_miss'
TELEM_EXTRA_TOKENS_DROPPED = 'extra_tokens_dropped'
TELEM_WALK_SKIPPED_PROJECTS = 'walk_skipped_projects'
TELEM_SEMAPHORE_SKIP = 'distiller_semaphore_skip'
TELEM_SUPPRESSED_AFTER_GENERATE = 'distiller_suppressed_after_generate'


def fingerprint(phrase: str) -> tuple[str, str] | None:
    """Pure function. Returns (exact_hex, coarse_hex) or None on OOV.

    exact_hex is the positional ``verb-noun[-modifier]`` hash.
    coarse_hex is the order-insensitive bag-of-tokens hash. Both are
    16-char SHA-256 prefixes — long enough to avoid collision in
    realistic per-project corpora (~10⁴ fingerprints), short enough
    to read in logs.

    Parser handles two real-world wrinkles:
      1. **Multi-word nouns** (`mobile-pair`, `audit-log`, `github-sync`,
         `project-sync`, `read-floor`, `env-var`, `feature-flag`,
         `sort-order`). Greedy match: try 2-token noun first, fall back
         to 1-token.
      2. **Noun-as-modifier** (`fix-condense-timeout` — the cheap model
         qualifies one noun with another). The modifier slot accepts
         either MODIFIERS or NOUNS, so the second-noun qualifier is
         preserved instead of silently dropped.

    Returns None on:
      - verb not in VERBS (OOV verb)
      - no NOUN candidate from the remaining tokens (OOV noun)

    Over-emission beyond 3 tokens is tolerated: tokens[3:] are dropped
    and the caller can increment ``extra_tokens_dropped`` telemetry.
    """
    if not phrase:
        return None
    parts = phrase.strip().lower().split('-')
    if not parts or not parts[0]:
        return None
    # Tolerate over-emission (I1 — Seat 1 Cond 4)
    parts = parts[:4]  # 4 to give multi-word nouns room before truncation
    verb = parts[0]
    if verb not in VERBS:
        return None
    remaining = parts[1:]
    # Greedy noun match: try 2-token compound first, then 1-token
    noun = None
    noun_len = 0
    if len(remaining) >= 2:
        candidate = f"{remaining[0]}-{remaining[1]}"
        if candidate in NOUNS:
            noun = candidate
            noun_len = 2
    if noun is None and remaining and remaining[0] in NOUNS:
        noun = remaining[0]
        noun_len = 1
    if noun is None:
        return None
    remaining = remaining[noun_len:]
    # Modifier slot — accept MODIFIERS OR NOUNS (cheap models qualify nouns
    # with other nouns). Unknown tokens silently dropped.
    modifier = ''
    if remaining:
        cand = remaining[0]
        if cand in MODIFIERS or cand in NOUNS:
            modifier = cand
    # Exact: positional, ordering-sensitive
    exact_canon = f"{verb}-{noun}"
    if modifier:
        exact_canon = f"{exact_canon}-{modifier}"
    exact = hashlib.sha256(exact_canon.encode('utf-8')).hexdigest()[:16]
    # Coarse: set-based, slot-insensitive — collapses slot-order variance
    # (modifier-noun swap, etc.). Verb-choice variance is NOT collapsed
    # by design; that's handled at extraction time by the closed vocab.
    tokens = sorted(t for t in (verb, noun, modifier) if t)
    coarse_canon = '-'.join(tokens)
    coarse = hashlib.sha256(coarse_canon.encode('utf-8')).hexdigest()[:16]
    return (exact, coarse)


# ── Per-project leaf lock (mirrors _get_mem_write_lock) ──────────────────────

_skill_stats_locks: dict[str, threading.Lock] = {}
_skill_stats_locks_guard = threading.Lock()


def _get_skill_stats_lock(project_id: str) -> threading.Lock:
    """Per-project leaf lock for _skill_stats.json + _skill_stats_summary.json.

    Reads AND writes go through this lock per the §4.7 RMW contract
    (Seat 3 v1.1 Cond 8 closure carried into v2.1). Held for the full
    read-decide-write span; never around model calls.
    """
    with _skill_stats_locks_guard:
        lk = _skill_stats_locks.get(project_id)
        if lk is None:
            lk = threading.Lock()
            _skill_stats_locks[project_id] = lk
    return lk


# ── _skill_stats.json schema + I/O ────────────────────────────────────────────

# Schema:
# {
#   "_updated": "<iso8601>",
#   "signals": [
#     {"sid": "<sid>", "ts": "<iso>", "scope_tag": "...",
#      "kind": "topic|preference|exploration",
#      "exact": "<hash>", "coarse": "<hash>",
#      "phrase": "<verb-noun-modifier>",
#      "summary": "...",         # preferences only
#      "evidence_quote": "...",  # preferences only
#      "question": "...",        # explorations only
#      "paths_tried": [...],     # explorations only
#      "outcome": "...",         # explorations only
#      "tools_used": [...]       # explorations only
#     }, ...
#   ],
#   "suppressions": {
#     # key: f"{exact}:{kind}"  (D6 — Seat 2/3 convergence)
#     "<exact>:<kind>": {"decided_at": "<iso>", "decision": "no|later",
#                        "wait_until_recurrence": <N>}
#   },
#   "outbox": {
#     # key: f"{exact}:{kind}" (D7 — Seat 3 Cond 4)
#     "<exact>:<kind>": {"last_proposed_at": "<iso>", "last_proposed_path": "..."}
#   },
#   "counters": {
#     "vocabulary_miss": <N>, "extra_tokens_dropped": <N>,
#     "walk_skipped_projects": <N>, ...
#   },
#   "cost": {"<YYYY-MM-DD>": <tokens>},
#   "cap_hits": <N>,
#   "cap": <int>,                 # active per-project cap (I4)
#   "last_walk_ts": "<iso>",       # cross-project walk debounce
#   "last_walk_session_count": <N>
# }


def _skill_stats_path(project_id: str) -> Path:
    """Flat sidecar form (D9): data/projects/<pid>_skill_stats.json."""
    return _data_root / f'{project_id}_skill_stats.json'


def _skill_stats_summary_path(project_id: str) -> Path:
    return _data_root / f'{project_id}_skill_stats_summary.json'


def _read_skill_stats(project_id: str) -> dict:
    """Read under lock; return empty schema on missing/corrupt. Never raises."""
    p = _skill_stats_path(project_id)
    if not p.exists():
        return _empty_stats()
    try:
        return json.loads(p.read_text(encoding='utf-8') or '{}')
    except json.JSONDecodeError:
        # Torn-write recovery posture (§4.9): structured warning, never
        # silently re-initialize (Seat 3 v1.1 Cond 4 inherited).
        if _log:
            _log(f"[distiller] _skill_stats.json parse failure for "
                 f"project_id={project_id} — preserving file untouched, "
                 f"returning empty schema for this read")
        return _empty_stats()
    except Exception:
        return _empty_stats()


def _empty_stats() -> dict:
    return {
        '_updated': None,
        'signals': [],
        'suppressions': {},
        'outbox': {},
        'counters': {},
        'cost': {},
        'cap_hits': 0,
        'cap': 0,
        'last_walk_ts': None,
        'last_walk_session_count': 0,
    }


def _write_skill_stats(project_id: str, stats: dict) -> None:
    """Atomic write — caller MUST hold _get_skill_stats_lock."""
    stats['_updated'] = _now_iso() if _now_iso else ''
    p = _skill_stats_path(project_id)
    _atomic_write_text(p, json.dumps(stats, indent=2, ensure_ascii=False))


def _increment_counter(project_id: str, key: str, n: int = 1) -> None:
    """Best-effort counter bump. Acquires lock; never raises."""
    if n <= 0:
        return
    try:
        with _get_skill_stats_lock(project_id):
            stats = _read_skill_stats(project_id)
            stats['counters'][key] = int(stats['counters'].get(key, 0)) + n
            _write_skill_stats(project_id, stats)
    except Exception:
        pass


def _structured_log(line: str) -> None:
    """Emit a structured log line for operator observability. Never raises."""
    if _log:
        try:
            _log(f"[distiller] {line}")
        except Exception:
            pass


# ── Kill-switch gate (D6 enumeration — §4.6) ─────────────────────────────────

# Enumerated entry points. Unit test
# tests/test_distiller_kill_switch_enumeration.py asserts every code path
# that touches Distiller state routes through _distiller_should_proceed.
ENTRY_POINTS = frozenset({
    'session_end_extract',     # _distill_extract_and_aggregate
    'proposal_generate',       # per-kind renderers
    'cross_project_aggregate', # _distill_cross_project_aggregate
    'record_push',             # POST /distiller/record-push from in-session
    'auto_promote',            # Phase 5 (stubbed; gate still enforced)
    'dispatch_hint',           # Phase 6 (stubbed; gate still enforced)
})


def _distiller_should_proceed(project_id: str, entry_point: str,
                              session: dict | None = None) -> bool:
    """Single kill-switch gate. Per parent design Cond 10 v2 — no entry
    point may inline its own check. Every Distiller-touching call site
    routes through here.

    Order: cheap → expensive. Master kill is one dict lookup; per-entry
    gates are also dict lookups; session-flag check requires the session.
    """
    if entry_point not in ENTRY_POINTS:
        # Defensive: a future contributor adding a new entry point without
        # registration fails loudly here. Caller treats this as 'gated off'.
        if _log:
            _log(f"[distiller] unknown entry_point={entry_point!r}; gating OFF")
        return False
    if not _cfg('distiller_enabled_global', True):
        return False
    if entry_point == 'cross_project_aggregate':
        if not _cfg('distiller_cross_project_enabled', True):
            return False
    project = _load_project(project_id) if _load_project else None
    if project is None:
        return False
    if _pcfg(project, 'distiller_mode', 'proposed') == 'off':
        return False
    if session is not None:
        if session.get('incognito') or session.get('housekeeping'):
            return False
    return True


# ── Extraction prompt (§4.1 with D4 K-caps) ──────────────────────────────────

def _extraction_prompt(project_id: str, project: dict) -> str:
    """Build the extraction prompt. Vocabulary is embedded; caps are per-project.

    The prompt is the inverse of mc-distill (which asks 'is this worth
    bottling?'). Phase 4 asks 'what happened here?' — a narrow objective
    extraction. The cross-session aggregator (§4.2) is what filters for
    recurrence and decides whether to propose.
    """
    max_topics = _pcfg(project, 'distiller_max_topics_per_session', 3)
    max_prefs = _pcfg(project, 'distiller_max_preferences_per_session', 3)
    max_expl = _pcfg(project, 'distiller_max_explorations_per_session', 3)
    verbs_str = ', '.join(sorted(VERBS))
    nouns_str = ', '.join(sorted(NOUNS))
    mods_str = ', '.join(sorted(MODIFIERS))
    return (
        "You are the Phase 4 Distiller extraction model. You read a rendered "
        "session transcript and emit a JSON object describing what topics, "
        "preferences, and explorations occurred. You are NOT an agent: no "
        "tools, no writes, no prose, no markdown fences. Return ONLY a JSON "
        "object.\n\n"
        "OUTPUT SCHEMA (exact keys):\n"
        '{"scope_tag": "cross-project"|"project-specific"|"ambiguous",\n'
        ' "signals": {\n'
        '   "topics": [{"phrase": "<verb>-<noun>[-<modifier>]"}, ...],\n'
        '   "preferences": [{"phrase": "<verb>-<noun>[-<modifier>]", '
        '"summary": "<one-line>", "evidence_quote": "<verbatim quote>"}, ...],\n'
        '   "explorations": [{"phrase": "<verb>-<noun>[-<modifier>]", '
        '"question": "<what was being investigated>", '
        '"paths_tried": ["<path1>", ...], "outcome": "<what worked + why>", '
        '"tools_used": ["WebSearch", "WebFetch", "Grep", ...]}, ...]\n'
        " }}\n\n"
        f"CAPS — emit AT MOST {max_topics} topics, {max_prefs} preferences, "
        f"{max_expl} explorations. Force yourself to choose the most salient "
        "in each class.\n\n"
        "CLOSED VOCABULARY — every `phrase` field MUST match the form "
        "`<verb>-<noun>[-<modifier>]` where:\n"
        f"  verbs: {verbs_str}\n"
        f"  nouns: {nouns_str}\n"
        f"  modifiers (optional): {mods_str}\n"
        "Do NOT invent verbs, nouns, or modifiers — out-of-vocabulary "
        "phrases will be discarded.\n\n"
        "GRANULARITY:\n"
        "  - FLOOR: do NOT emit at language level (`edit-source`), "
        "session-symptom level (`fix-error`), or project-name level.\n"
        "  - CEILING: emit at the level of a thing a future skill could be "
        "about: a subsystem invariant, a recurring workflow, a gotcha class, "
        "a diagnostic procedure.\n"
        "  - GOOD: `fix-condense-timeout`, `gate-skill-distiller`, "
        "`debug-pair-mobile`, `validate-config`.\n"
        "  - BAD: `python-code` (language level), `edited-line-1158` "
        "(symptom level), `mission-control-work` (project level), `fix-bug` "
        "(too vague).\n\n"
        "SCOPE TAG — default `cross-project`. Narrow to `project-specific` "
        "ONLY when the evidence references project-local files, paths, "
        "configs, or services not shared across projects. Use `ambiguous` "
        "when you genuinely cannot tell.\n\n"
        "PREFERENCES — only emit when the user expressed a behavioral "
        "preference, correction, confirmation, or constraint. "
        "`evidence_quote` MUST be a verbatim user-message substring. If "
        "the user didn't express a preference, return empty list.\n\n"
        "EXPLORATIONS — only emit when the agent performed substantive "
        "external research (WebSearch / WebFetch / multi-step Grep across "
        "unfamiliar subsystems / explicit alternatives comparison). "
        "`question`, `paths_tried`, `outcome` are all required when emitted. "
        "If the session had no substantive exploration, return empty list.\n\n"
        "REFUSE PATH — empty session, gibberish, or nothing salient: "
        'return `{"scope_tag": "ambiguous", "signals": {"topics": [], '
        '"preferences": [], "explorations": []}}`. Padding noise is worse '
        "than silence."
    )


# ── Per-kind generation prompts (§4.3, §4.4, §4.5 with D5 elements 6+7) ──────

_SKILL_PROMPT_PREAMBLE = (
    "You are the Phase 4 SKILL.md proposal generator. You receive aggregated "
    "evidence from N recurring sessions touching the same pattern. Output a "
    "complete SKILL.md ready to land in `data/skills/_proposed/`. No prose "
    "outside the SKILL.md body; no markdown fences around the whole output.\n\n"
    "REQUIRED ELEMENTS:\n"
    "  1. **TRIGGER phrasing in description** — `TRIGGER when <observable "
    "symptom / file / error / screenshot characteristic>`. Element 7 below "
    "amplifies this: a future agent must recognize the trigger from incoming "
    "context, not from re-reading the original debug.\n"
    "  2. **Operating procedure, not summary** — `do this, then this, then "
    "this` framing. Extract steps, don't paraphrase what happened.\n"
    "  3. **Body budget ≤120 lines.** Tight, scannable, no padding.\n"
    "  4. **At least one verbatim observation quote** from the evidence. "
    "Prevents context drift.\n"
    "  5. **REFUSE path** — if the N observations are too heterogeneous to "
    "form one coherent skill, output exactly `REFUSE` and nothing else.\n"
    "  6. **Anti-patterns section (if applicable)** — if the evidence "
    "contains failed-approach observations (`tried X, didn't work` "
    "patterns), include them under an `## Anti-patterns` heading. The most "
    "useful skills tell future agents what to STOP doing.\n"
    "  7. **Recognition-test phrasing** — the TRIGGER must describe what the "
    "agent SEES that maps to the skill. Bad: `TRIGGER when debugging X`. "
    "Good: `TRIGGER when the user reports <observable> AND <observable> in "
    "<observable>`. Pattern-bound, not session-bound."
)

_EXPLORATION_PROMPT_PREAMBLE = (
    "You are the Phase 4 EXPLORATION.md generator. You receive a single "
    "session's exploration record and output a complete EXPLORATION.md. "
    "Single-shot retention — no recurrence gating. Body shape:\n\n"
    "  # <Question being investigated>\n\n"
    "  ## Paths tried\n  - <path>: <result>\n\n"
    "  ## What worked\n  <answer + why>\n\n"
    "  ## What didn't work\n  <dead-ends — explicitly named so future "
    "agents skip them>\n\n"
    "  ## When this applies\n  <one-line recognition condition>\n\n"
    "REFUSE PATH: if the exploration was trivial or didn't actually try "
    "alternatives, output `REFUSE`."
)

_PREFERENCE_PROMPT_PREAMBLE = (
    "You are the Phase 4 PREFERENCE.md generator. You receive N verbatim "
    "user quotes expressing a recurring preference and output a "
    "PREFERENCE.md ready to promote into feedback memory. Body shape mirrors "
    "the existing feedback_*.md files:\n\n"
    "  # <The preference, as a one-line rule>\n\n"
    "  ## Why (the underlying reason, if observable)\n  <extracted reason>\n\n"
    "  ## How to apply\n  <when this preference kicks in>\n\n"
    "  ## Evidence\n  - Session <sid> (<ts>): \"<verbatim quote>\"\n\n"
    "PROMOTION TARGET — `suggested_target` is NOT set at extraction time "
    "(I3 closure). The promotion UI offers feedback_memory (default), "
    "project CLAUDE.md, or global SKILL.md.\n\n"
    "REFUSE PATH: if the quotes don't actually express a coherent recurring "
    "preference, output `REFUSE`."
)


# ── Lifecycle: extract → aggregate → render → write ──────────────────────────

# Per-project guard preventing concurrent Distiller fans-out on the same
# session (mirrors _scribing_projects discipline).
_distilling_projects: set[str] = set()
_distilling_guard = threading.Lock()


def _distill_extract_and_aggregate(project_id: str, sid: str,
                                   jsonl_path: str | None) -> None:
    """Daemon-thread entry point. Wrapped in blanket try/except — best-effort.

    Dispatched from server._write_session_memory via
    threading.Thread(daemon=True). Failure NEVER breaks Scribe or
    completion. Per §4.8: signal commits BEFORE proposal-generate
    (Option A); outbox marker writes AFTER successful artifact land
    (D7 — Seat 3 Cond 4 extension).
    """
    try:
        # Per-project re-entrancy guard
        with _distilling_guard:
            if project_id in _distilling_projects:
                return  # another thread is already on this project
            _distilling_projects.add(project_id)
        try:
            _distill_extract_and_aggregate_inner(project_id, sid, jsonl_path)
        finally:
            with _distilling_guard:
                _distilling_projects.discard(project_id)
    except Exception as e:
        _structured_log(
            f"daemon_thread_exception:project_id={project_id}:sid={sid}:err={e!r}"
        )


def _distill_extract_and_aggregate_inner(project_id: str, sid: str,
                                         jsonl_path: str | None) -> None:
    """Inner: kill-switch gate → semaphore → extract → aggregate →
    per-kind generate → cross-project pass."""
    project = _load_project(project_id)
    if project is None:
        return
    # Hard re-check the gate at the entry point (Cond 10 v2 discipline)
    if not _distiller_should_proceed(project_id, 'session_end_extract',
                                     session={'incognito': False,
                                              'housekeeping': False}):
        return
    # Non-blocking semaphore acquire (D8 — Seat 3 Cond 3)
    sem = _get_per_project_semaphore(project_id) if _get_per_project_semaphore \
        else None
    if sem is not None:
        if not sem.acquire(blocking=True, timeout=2.0):
            _increment_counter(project_id, TELEM_SEMAPHORE_SKIP)
            return
    try:
        _do_extract_aggregate(project_id, project, sid, jsonl_path)
    finally:
        if sem is not None:
            try:
                sem.release()
            except Exception:
                pass


def _do_extract_aggregate(project_id: str, project: dict,
                          sid: str, jsonl_path: str | None) -> None:
    # Cost cap check — early return if today's budget already blown
    if not _within_cost_cap(project_id, project):
        return
    # Render transcript (reuses Scribe's renderer)
    if jsonl_path:
        try:
            transcript = _scribe_render_transcript(Path(jsonl_path))
        except Exception:
            return
    else:
        # No transcript path → can't extract; skip cleanly
        return
    if not transcript or len(transcript.strip()) < 200:
        return  # too thin for meaningful extraction (skip silently)
    # Cheap-model extraction
    model = _cfg('distiller_model', '') or 'haiku'
    try:
        raw = _scribe_call(model, _extraction_prompt(project_id, project),
                           transcript)
    except Exception:
        _increment_counter(project_id, 'extraction_error')
        return
    parsed = _parse_extraction(raw)
    if parsed is None:
        _increment_counter(project_id, 'extraction_parse_error')
        return
    # Commit signals first (Option A: signal-before-generate)
    new_signals = _normalize_signals(project_id, sid, parsed)
    _commit_signals(project_id, new_signals)
    # Aggregate per-project and emit candidates
    candidates = _aggregate_per_project(project_id, project, new_signals)
    # Generate artifacts (cheap-model calls, no locks)
    for cand in candidates:
        _generate_and_write_artifact(project_id, project, cand)
    # Update per-project summary cache (D3 — Seat 1 Cond 5)
    _update_summary_cache(project_id)
    # Cross-project aggregation (inline, same daemon thread per D3)
    if _distiller_should_proceed(project_id, 'cross_project_aggregate'):
        if _cross_project_walk_debounced(project_id):
            _distill_cross_project_aggregate(project_id, project)


# ── Signal normalization (closed-vocab fingerprinting at server side) ────────

_RE_SLUG_TOKEN = re.compile(r'[a-z0-9-]+')


def _normalize_phrase(phrase: str) -> str:
    """Lowercase, collapse whitespace to '-', strip non-vocab chars."""
    if not phrase:
        return ''
    s = phrase.strip().lower()
    # Replace whitespace + underscores with -, drop everything else outside
    # the allowed character class
    s = re.sub(r'[\s_]+', '-', s)
    s = re.sub(r'[^a-z0-9-]', '', s)
    s = re.sub(r'-+', '-', s).strip('-')
    return s


def _normalize_signals(project_id: str, sid: str, parsed: dict) -> list[dict]:
    """Apply fingerprint + telemetry. Returns the list of valid signals
    ready to write to _skill_stats.json. OOV phrases are dropped (with
    telemetry). Drops beyond cap counters are recorded too."""
    out = []
    scope_tag = parsed.get('scope_tag', 'cross-project')
    if scope_tag not in ('cross-project', 'project-specific', 'ambiguous'):
        scope_tag = 'cross-project'
    signals = parsed.get('signals', {}) or {}
    ts = _now_iso() if _now_iso else ''
    # Topics
    for sig in (signals.get('topics') or []):
        phrase = _normalize_phrase(sig.get('phrase', ''))
        fp = fingerprint(phrase)
        if fp is None:
            _increment_counter(project_id, TELEM_VOCABULARY_MISS)
            continue
        exact, coarse = fp
        out.append({
            'sid': sid, 'ts': ts, 'scope_tag': scope_tag,
            'kind': 'topic', 'phrase': phrase,
            'exact': exact, 'coarse': coarse,
        })
    # Preferences
    for sig in (signals.get('preferences') or []):
        phrase = _normalize_phrase(sig.get('phrase', ''))
        fp = fingerprint(phrase)
        if fp is None:
            _increment_counter(project_id, TELEM_VOCABULARY_MISS)
            continue
        exact, coarse = fp
        out.append({
            'sid': sid, 'ts': ts, 'scope_tag': scope_tag,
            'kind': 'preference', 'phrase': phrase,
            'exact': exact, 'coarse': coarse,
            'summary': str(sig.get('summary', '') or '').strip()[:500],
            'evidence_quote': str(sig.get('evidence_quote', '') or '')\
                .strip()[:2000],
        })
    # Explorations
    for sig in (signals.get('explorations') or []):
        phrase = _normalize_phrase(sig.get('phrase', ''))
        fp = fingerprint(phrase)
        if fp is None:
            _increment_counter(project_id, TELEM_VOCABULARY_MISS)
            continue
        exact, coarse = fp
        out.append({
            'sid': sid, 'ts': ts, 'scope_tag': scope_tag,
            'kind': 'exploration', 'phrase': phrase,
            'exact': exact, 'coarse': coarse,
            'question': str(sig.get('question', '') or '').strip()[:500],
            'paths_tried': list(sig.get('paths_tried') or [])[:20],
            'outcome': str(sig.get('outcome', '') or '').strip()[:2000],
            'tools_used': list(sig.get('tools_used') or [])[:20],
        })
    return out


def _parse_extraction(raw: str) -> dict | None:
    """Tolerant JSON extraction from the cheap model's output."""
    if not raw:
        return None
    s = raw.strip()
    # Tolerate ```json ... ``` fences
    if s.startswith('```'):
        s = s.split('```', 2)[-2] if s.count('```') >= 2 else s.strip('`')
        if s.lstrip().lower().startswith('json'):
            s = s.lstrip()[4:]
    i = s.find('{')
    j = s.rfind('}')
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(s[i:j + 1])
    except json.JSONDecodeError:
        return None


def _commit_signals(project_id: str, signals: list[dict]) -> None:
    """Append signals to _skill_stats.json under the per-project lock."""
    if not signals:
        return
    with _get_skill_stats_lock(project_id):
        stats = _read_skill_stats(project_id)
        stats['signals'].extend(signals)
        _write_skill_stats(project_id, stats)


# ── Per-project aggregation (dual-layer recurrence per D2) ───────────────────

def _aggregate_per_project(project_id: str, project: dict,
                           new_signals: list[dict]) -> list[dict]:
    """Read recurrence state under lock, return proposal candidates.

    Each candidate is a dict with enough info for the renderer to
    produce a complete artifact:
      {kind, exact, coarse, scope_tag, evidence_signals: [...],
       recurrence_exact: N, recurrence_coarse: N}
    """
    candidates = []
    min_rec = int(_pcfg(project, 'distiller_min_recurrence', 3))
    window_days = int(_cfg('distiller_window_days', 30))
    dedupe_days = int(_cfg('distiller_proposal_dedupe_days', 7))
    new_fingerprints = {(s['exact'], s['coarse'], s['kind'])
                        for s in new_signals}
    with _get_skill_stats_lock(project_id):
        stats = _read_skill_stats(project_id)
        window_signals = _filter_window(stats['signals'], window_days)
        # Build exact + coarse recurrence indexes per kind
        kind_exact: dict[tuple[str, str], set[str]] = {}  # (kind, exact) → sids
        kind_coarse: dict[tuple[str, str], set[str]] = {}
        for s in window_signals:
            k = s.get('kind')
            if k not in ('topic', 'preference', 'exploration'):
                continue
            kind_exact.setdefault((k, s['exact']), set()).add(s.get('sid', ''))
            kind_coarse.setdefault((k, s['coarse']), set()).add(s.get('sid', ''))
        suppressions = stats.get('suppressions', {}) or {}
        outbox = stats.get('outbox', {}) or {}
        for (exact, coarse, kind) in new_fingerprints:
            cand = _evaluate_candidate(
                kind=kind, exact=exact, coarse=coarse,
                kind_exact=kind_exact, kind_coarse=kind_coarse,
                suppressions=suppressions, outbox=outbox,
                min_rec=min_rec, dedupe_days=dedupe_days,
                window_signals=window_signals,
                new_signals=new_signals,
            )
            if cand:
                candidates.append(cand)
    return candidates


def _evaluate_candidate(*, kind: str, exact: str, coarse: str,
                        kind_exact: dict, kind_coarse: dict,
                        suppressions: dict, outbox: dict,
                        min_rec: int, dedupe_days: int,
                        window_signals: list[dict],
                        new_signals: list[dict]) -> dict | None:
    """One candidate decision. Returns None if NOT a candidate (gated)."""
    # Suppression check
    supp_key = f"{exact}:{kind}"
    supp = suppressions.get(supp_key)
    if supp and supp.get('decision') == 'no':
        return None
    # Outbox dedupe check (D7 extended window)
    obx = outbox.get(supp_key)
    if obx and obx.get('last_proposed_at'):
        if _within_dedupe_days(obx['last_proposed_at'], dedupe_days):
            return None
    # Exploration: single-shot retention, no recurrence gate
    if kind == 'exploration':
        # Per-(fingerprint, day) intra-day dedupe (cheap-check via outbox shape)
        # already covered by D7 dedupe above (much wider window — fine).
        # Find the evidence signal in new_signals
        evid = [s for s in new_signals
                if s['exact'] == exact and s['kind'] == 'exploration']
        if not evid:
            return None
        return {
            'kind': 'exploration', 'exact': exact, 'coarse': coarse,
            'scope_tag': evid[0].get('scope_tag', 'cross-project'),
            'evidence_signals': evid,
            'recurrence_exact': 1, 'recurrence_coarse': 1,
        }
    # Skill / preference: dual-layer recurrence check
    exact_sids = kind_exact.get((kind, exact), set())
    coarse_sids = kind_coarse.get((kind, coarse), set())
    exact_count = len(exact_sids)
    coarse_count = len(coarse_sids)
    if exact_count < min_rec and coarse_count < (min_rec + 1):
        # Honor Later: bump wait_until_recurrence comparison
        if supp and supp.get('decision') == 'later':
            wait = int(supp.get('wait_until_recurrence', 0))
            if max(exact_count, coarse_count) < wait:
                return None
        return None
    # Compose evidence: window signals matching either layer
    evid = [s for s in window_signals
            if s.get('kind') == kind
            and (s.get('exact') == exact or s.get('coarse') == coarse)]
    if not evid:
        return None
    scope_counts = Counter(s.get('scope_tag', 'cross-project') for s in evid)
    scope_tag = scope_counts.most_common(1)[0][0]
    return {
        'kind': 'skill' if kind == 'topic' else kind,
        'exact': exact, 'coarse': coarse,
        'scope_tag': scope_tag,
        'evidence_signals': evid,
        'recurrence_exact': exact_count,
        'recurrence_coarse': coarse_count,
    }


def _filter_window(signals: list[dict], days: int) -> list[dict]:
    """Read-time window filter — never purge, just filter."""
    if not signals:
        return []
    cutoff = time.time() - (days * 86400)
    return [s for s in signals if _iso_to_epoch(s.get('ts', '')) >= cutoff]


def _within_dedupe_days(iso_ts: str, days: int) -> bool:
    """Return True if iso_ts is within the last `days` days."""
    epoch = _iso_to_epoch(iso_ts)
    return (time.time() - epoch) < (days * 86400)


def _iso_to_epoch(iso: str) -> float:
    """Tolerant ISO → epoch. Returns 0.0 on parse failure (signal counts as
    'older than any window' → excluded from filter)."""
    if not iso:
        return 0.0
    try:
        from datetime import datetime
        # Tolerate Z suffix and lack of timezone
        s = iso.replace('Z', '+00:00')
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


# ── Cost cap (I4 — Seat 4 Cond 7) ────────────────────────────────────────────

def _within_cost_cap(project_id: str, project: dict) -> bool:
    """Check if today's token spend is under the cap. Best-effort.

    Returns True if within budget; False if cap exceeded (emits structured
    log + counter exactly once per cap-hit per day).
    """
    cap = int(_cfg('distiller_cost_cap_tokens_per_project_per_day', 100000))
    today = (_now_iso() or '')[:10]
    try:
        stats = _read_skill_stats(project_id)
        used = int(stats.get('cost', {}).get(today, 0))
        if used >= cap:
            # Structured log per I4 — INCLUDES cap_value
            _structured_log(
                f"distiller_cost_cap_hit:project_id={project_id}:"
                f"date={today}:tokens_used={used}:cap_value={cap}"
            )
            with _get_skill_stats_lock(project_id):
                stats = _read_skill_stats(project_id)
                stats['cap_hits'] = int(stats.get('cap_hits', 0)) + 1
                stats['cap'] = cap
                _write_skill_stats(project_id, stats)
            return False
    except Exception:
        pass
    return True


# ── Cross-project walk debounce (D3 — Seat 1 Cond 5) ─────────────────────────

def _cross_project_walk_debounced(project_id: str) -> bool:
    """Returns True if the cross-project walk should fire now; False if
    debounced. Updates last_walk_ts / last_walk_session_count atomically."""
    sess_cap = int(_cfg('distiller_cross_project_walk_debounce_session_count', 5))
    secs_cap = int(_cfg('distiller_cross_project_walk_debounce_seconds', 600))
    with _get_skill_stats_lock(project_id):
        stats = _read_skill_stats(project_id)
        last_ts = stats.get('last_walk_ts') or ''
        last_sess = int(stats.get('last_walk_session_count', 0))
        last_epoch = _iso_to_epoch(last_ts) if last_ts else 0.0
        elapsed = time.time() - last_epoch if last_epoch else float('inf')
        sess_delta = last_sess + 1
        if elapsed >= secs_cap or sess_delta >= sess_cap:
            stats['last_walk_ts'] = _now_iso()
            stats['last_walk_session_count'] = 0
            _write_skill_stats(project_id, stats)
            return True
        stats['last_walk_session_count'] = sess_delta
        _write_skill_stats(project_id, stats)
        return False


# ── Cross-project aggregation (D3 ii — Option B: lock-free + 3-retry) ────────

def _read_skill_stats_with_retry(path: Path, attempts: int = 3,
                                 spacing_ms: int = 50) -> dict | None:
    """Lock-free read with bounded retry on parse failure. Returns None on
    persistent failure (caller logs + skips). Matches D3 ii spec verbatim."""
    last_err = None
    for attempt in range(attempts):
        try:
            return json.loads(path.read_text(encoding='utf-8') or '{}')
        except (json.JSONDecodeError, FileNotFoundError) as e:
            last_err = e
            if attempt + 1 < attempts:
                time.sleep(spacing_ms / 1000.0)
                continue
            return None
        except Exception:
            return None
    return None


def _distill_cross_project_aggregate(project_id: str,
                                     project: dict) -> None:
    """Lock-free walk per D3 ii Option B. Aggregates cross-project fingerprint
    recurrence; if ≥2 projects each clear their own per-project threshold,
    promotes the artifact's scope to cross-project (routed to global staging).

    The lock-free read tolerates the dominant race window (mid-rename
    atomic-write); persistent parse failures (3 retries × 50ms) increment
    walk_skipped_projects telemetry and the project is excluded from THIS
    walk's contribution. Self-healing on the next walk.
    """
    if _data_root is None:
        return
    # Walk all project _skill_stats.json files; each project contributes only
    # if its signal contribution clears that project's own min_recurrence
    # (D14 — composition rule).
    cross_exact: dict[str, set[str]] = {}   # exact → set of project_ids
    cross_coarse: dict[str, set[str]] = {}
    walked = 0
    skipped = 0
    try:
        for p in _data_root.glob('*_skill_stats.json'):
            pid = p.name[:-len('_skill_stats.json')]
            walked += 1
            stats = _read_skill_stats_with_retry(p)
            if stats is None:
                skipped += 1
                _structured_log(
                    f"distiller_walk_skip:project_id={pid}:"
                    f"reason=json_parse_failed"
                )
                continue
            this_project = _load_project(pid) if _load_project else None
            if this_project is None:
                continue
            this_min = int(_pcfg(this_project, 'distiller_min_recurrence', 3))
            window_days = int(_cfg('distiller_window_days', 30))
            window_sigs = _filter_window(stats.get('signals', []), window_days)
            # Build per-kind recurrence counts on this project
            exact_sids: dict[tuple[str, str], set[str]] = {}
            coarse_sids: dict[tuple[str, str], set[str]] = {}
            for s in window_sigs:
                k = s.get('kind', 'topic')
                exact_sids.setdefault((k, s.get('exact', '')), set()).add(
                    s.get('sid', ''))
                coarse_sids.setdefault((k, s.get('coarse', '')), set()).add(
                    s.get('sid', ''))
            # Only contribute fingerprints that clear THIS project's threshold
            for (kind, exact), sids in exact_sids.items():
                if len(sids) >= this_min:
                    cross_exact.setdefault(f"{kind}:{exact}", set()).add(pid)
            for (kind, coarse), sids in coarse_sids.items():
                if len(sids) >= (this_min + 1):
                    cross_coarse.setdefault(f"{kind}:{coarse}", set()).add(pid)
    except Exception as e:
        _structured_log(f"cross_project_walk_exception:err={e!r}")
        return
    # Bump skipped counter (best-effort)
    if skipped > 0:
        _increment_counter(project_id, TELEM_WALK_SKIPPED_PROJECTS, n=skipped)
    # Cross-project promotion: ≥2 distinct projects on the same fingerprint
    for key, projects in cross_exact.items():
        if len(projects) >= 2:
            _structured_log(
                f"cross_project_candidate:layer=exact:fingerprint={key}:"
                f"projects={','.join(sorted(projects))}"
            )
    for key, projects in cross_coarse.items():
        if len(projects) >= 2:
            _structured_log(
                f"cross_project_candidate:layer=coarse:fingerprint={key}:"
                f"projects={','.join(sorted(projects))}"
            )
    # Note: cross-project candidates surface as structured-log notifications
    # in v2.1; the dashboard/audit picks them up via /api/distiller-stats.
    # Auto-write of cross-project artifacts is deferred to Phase 5 per the
    # parent design's "auto-authored project-local only" rule and the
    # promotion-checkpoint discipline of v2.1.


# ── Per-project summary cache (D3 — Seat 1 Cond 5) ───────────────────────────

def _update_summary_cache(project_id: str) -> None:
    """Materialize a compact per-project recurrence summary under the same
    lock as _skill_stats.json. Cross-project walkers (future readers) read
    summaries, not raw signal streams. Storage: ~150B × fingerprints."""
    try:
        with _get_skill_stats_lock(project_id):
            stats = _read_skill_stats(project_id)
            window_days = int(_cfg('distiller_window_days', 30))
            window_sigs = _filter_window(stats.get('signals', []), window_days)
            summary: dict[str, dict] = {}
            for s in window_sigs:
                k = s.get('kind', 'topic')
                ek = f"{k}:exact:{s.get('exact', '')}"
                ck = f"{k}:coarse:{s.get('coarse', '')}"
                for key in (ek, ck):
                    e = summary.setdefault(key, {'sids': set(), 'last_ts': ''})
                    e['sids'].add(s.get('sid', ''))
                    if s.get('ts', '') > e['last_ts']:
                        e['last_ts'] = s.get('ts', '')
            # Materialize for JSON (sets → counts)
            out = {
                k: {'count': len(v['sids']), 'last_ts': v['last_ts']}
                for k, v in summary.items()
            }
            p = _skill_stats_summary_path(project_id)
            _atomic_write_text(
                p, json.dumps({'_updated': _now_iso(), 'fingerprints': out},
                              indent=2, ensure_ascii=False))
    except Exception:
        pass


# ── Per-kind artifact generation (§4.3, §4.4, §4.5) ──────────────────────────

def _generate_and_write_artifact(project_id: str, project: dict,
                                 candidate: dict) -> None:
    """Generate one artifact via cheap-model call + atomic write to
    _proposed/. Per §4.8 ordering: signal already committed (Option A);
    on success, write outbox marker (D7). All cheap-model calls are
    OUTSIDE the lock domain."""
    if not _distiller_should_proceed(project_id, 'proposal_generate'):
        return
    kind = candidate['kind']
    try:
        if kind == 'skill':
            body, target_path = _render_skill(project_id, project, candidate)
        elif kind == 'exploration':
            body, target_path = _render_exploration(project_id, project, candidate)
        elif kind == 'preference':
            body, target_path = _render_preference(project_id, project, candidate)
        else:
            return  # update kind reserved for future expansion
        if body is None or body.strip() == 'REFUSE':
            _increment_counter(project_id, f'render_refuse:{kind}')
            return
        if not body.strip():
            return
        # TOCTOU re-check under lock before atomic write (D6 extension —
        # Seat 3 Cond 5: suppression marker may have been written during
        # the cheap-model call)
        with _get_skill_stats_lock(project_id):
            stats = _read_skill_stats(project_id)
            supp_key = f"{candidate['exact']}:{kind}"
            supp = stats.get('suppressions', {}).get(supp_key)
            if supp and supp.get('decision') == 'no':
                _increment_counter(project_id, TELEM_SUPPRESSED_AFTER_GENERATE)
                return
        # Atomic write via .tmp + rename
        target_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(target_path, body)
        # Outbox marker (D7) — under lock, after successful artifact land
        with _get_skill_stats_lock(project_id):
            stats = _read_skill_stats(project_id)
            stats.setdefault('outbox', {})[f"{candidate['exact']}:{kind}"] = {
                'last_proposed_at': _now_iso(),
                'last_proposed_path': str(target_path),
            }
            _write_skill_stats(project_id, stats)
        _increment_counter(project_id, f'proposed:{kind}')
    except Exception as e:
        _structured_log(
            f"render_exception:kind={kind}:project_id={project_id}:err={e!r}"
        )


def _slug(s: str) -> str:
    """Filesystem-safe slug. Keeps lower alnum + hyphen."""
    s = (s or '').lower()
    s = re.sub(r'[^a-z0-9-]+', '-', s)
    s = re.sub(r'-+', '-', s).strip('-')
    return s[:64] or 'unnamed'


def _proposal_target(project_id: str, scope_tag: str, kind: str,
                     fingerprint_exact: str, name_slug: str) -> Path:
    """Compute the destination path for a _proposed/ artifact.

    Cross-project → data/skills/_proposed/global/<...>
    Project-specific → data/skills/_proposed/<project_id>/<...>
    """
    ts = (_now_iso() or '').replace(':', '-').replace('+', '_')[:19]
    scope_dir = 'global' if scope_tag == 'cross-project' else project_id
    sub = f"{ts}-{fingerprint_exact[:12]}-{name_slug}"
    base = _skills_root / '_proposed' / scope_dir / sub
    kind_to_file = {
        'skill': 'SKILL.md', 'update': 'UPDATE.md',
        'exploration': 'EXPLORATION.md', 'preference': 'PREFERENCE.md',
    }
    return base / kind_to_file.get(kind, 'SKILL.md')


def _render_skill(project_id: str, project: dict,
                  candidate: dict) -> tuple[str | None, Path]:
    """Render a SKILL.md proposal. Returns (body, target_path) or (None, _)."""
    model = _cfg('distiller_model', '') or 'haiku'
    evidence_block = _build_evidence_block(candidate['evidence_signals'])
    instruction = _SKILL_PROMPT_PREAMBLE + "\n\n" + (
        "Aggregated evidence below; produce ONE coherent SKILL.md per the "
        "REQUIRED ELEMENTS. Include the frontmatter block exactly as shown.\n\n"
        "FRONTMATTER TEMPLATE:\n"
        "---\n"
        "name: <kebab-case-name>\n"
        "description: <TRIGGER phrasing — observable symptoms>\n"
        "---\n"
    )
    body_in = (
        f"Recurrence: exact={candidate['recurrence_exact']} "
        f"coarse={candidate['recurrence_coarse']}\n"
        f"Scope tag (extraction): {candidate['scope_tag']}\n\n"
        f"Evidence:\n{evidence_block}"
    )
    try:
        out = _scribe_call(model, instruction, body_in)
    except Exception:
        return None, Path()
    if not out:
        return None, Path()
    name_slug = _extract_name_from_frontmatter(out) or f"distilled-{candidate['exact'][:8]}"
    body = _wrap_skill_body(out, project_id, candidate, kind='skill',
                            name_slug=name_slug)
    target = _proposal_target(project_id, candidate['scope_tag'], 'skill',
                              candidate['exact'], name_slug)
    return body, target


def _render_exploration(project_id: str, project: dict,
                        candidate: dict) -> tuple[str | None, Path]:
    model = _cfg('distiller_model', '') or 'haiku'
    sig = candidate['evidence_signals'][0]
    instruction = _EXPLORATION_PROMPT_PREAMBLE
    body_in = (
        f"Question: {sig.get('question', '')}\n"
        f"Paths tried:\n" +
        '\n'.join(f"  - {p}" for p in sig.get('paths_tried', [])) + "\n\n"
        f"Outcome: {sig.get('outcome', '')}\n"
        f"Tools used: {', '.join(sig.get('tools_used', []))}\n"
    )
    try:
        out = _scribe_call(model, instruction, body_in)
    except Exception:
        return None, Path()
    if not out:
        return None, Path()
    q = sig.get('question', '') or candidate['exact']
    name_slug = _slug(q.split('?')[0][:60]) or f"exploration-{candidate['exact'][:8]}"
    body = _wrap_skill_body(out, project_id, candidate, kind='exploration',
                            name_slug=name_slug)
    target = _proposal_target(project_id, candidate['scope_tag'], 'exploration',
                              candidate['exact'], name_slug)
    return body, target


def _render_preference(project_id: str, project: dict,
                       candidate: dict) -> tuple[str | None, Path]:
    model = _cfg('distiller_model', '') or 'haiku'
    instruction = _PREFERENCE_PROMPT_PREAMBLE
    evid_lines = []
    for s in candidate['evidence_signals'][:10]:
        evid_lines.append(
            f"  - Session {s.get('sid', '')} ({s.get('ts', '')}): "
            f"\"{s.get('evidence_quote', '')[:300]}\""
        )
    body_in = (
        f"Recurrence: exact={candidate['recurrence_exact']} "
        f"coarse={candidate['recurrence_coarse']}\n"
        f"Summary signals:\n" +
        '\n'.join(f"  - {s.get('summary', '')}"
                  for s in candidate['evidence_signals'][:10]) + "\n\n"
        f"Evidence quotes:\n" + '\n'.join(evid_lines)
    )
    try:
        out = _scribe_call(model, instruction, body_in)
    except Exception:
        return None, Path()
    if not out:
        return None, Path()
    name_slug = _extract_name_from_frontmatter(out) or f"preference-{candidate['exact'][:8]}"
    body = _wrap_skill_body(out, project_id, candidate, kind='preference',
                            name_slug=name_slug)
    target = _proposal_target(project_id, candidate['scope_tag'], 'preference',
                              candidate['exact'], name_slug)
    return body, target


def _build_evidence_block(signals: list[dict]) -> str:
    """Render evidence signals as model input."""
    lines = []
    for s in signals[:10]:  # cap at 10 evidence sessions per render
        lines.append(f"  Session {s.get('sid', '')} ({s.get('ts', '')}):")
        lines.append(f"    phrase: {s.get('phrase', '')}")
        if s.get('summary'):
            lines.append(f"    summary: {s.get('summary')}")
        if s.get('evidence_quote'):
            lines.append(f"    quote: \"{s.get('evidence_quote', '')[:300]}\"")
        if s.get('question'):
            lines.append(f"    question: {s.get('question')}")
        if s.get('outcome'):
            lines.append(f"    outcome: {s.get('outcome')[:200]}")
    return '\n'.join(lines)


_RE_FRONTMATTER_NAME = re.compile(r'^name:\s*(.+?)\s*$', re.MULTILINE)


def _extract_name_from_frontmatter(text: str) -> str | None:
    """Pull the `name:` field from YAML frontmatter, if present."""
    if not text:
        return None
    m = _RE_FRONTMATTER_NAME.search(text)
    if not m:
        return None
    return _slug(m.group(1).strip())


def _wrap_skill_body(model_output: str, project_id: str, candidate: dict,
                     kind: str, name_slug: str) -> str:
    """Inject required frontmatter fields if the model omitted them.

    Required: extraction_scope, extraction_fingerprint_exact,
    extraction_fingerprint_coarse, evidence_session_ids,
    recurrence_count_exact, recurrence_count_coarse, provenance,
    source_session, created_at, kind, name.
    """
    sids = [s.get('sid', '') for s in candidate['evidence_signals']]
    source_session = sids[0] if sids else ''
    fm = {
        'kind': kind,
        'name': name_slug,
        'extraction_scope': candidate['scope_tag'],
        'extraction_fingerprint_exact': candidate['exact'],
        'extraction_fingerprint_coarse': candidate['coarse'],
        'evidence_session_ids': sids,
        'evidence_window_days': int(_cfg('distiller_window_days', 30)),
        'recurrence_count_exact': candidate['recurrence_exact'],
        'recurrence_count_coarse': candidate['recurrence_coarse'],
        'provenance': 'distilled',
        'source_session': source_session,
        'created_at': _now_iso() or '',
    }
    # If the model already produced frontmatter, splice our required fields
    text = model_output.strip()
    if text.startswith('---'):
        end = text.find('\n---', 4)
        if end >= 0:
            existing = text[4:end].strip()
            body = text[end + 4:].lstrip('\n')
            merged_fm = _merge_frontmatter(existing, fm)
            return f"---\n{merged_fm}\n---\n\n{body}\n"
    fm_lines = _dump_frontmatter(fm)
    return f"---\n{fm_lines}\n---\n\n{text}\n"


def _dump_frontmatter(fm: dict) -> str:
    """Simple YAML serialization for our flat frontmatter shape."""
    lines = []
    for k, v in fm.items():
        if isinstance(v, list):
            if not v:
                lines.append(f"{k}: []")
            else:
                items = ', '.join(json.dumps(x, ensure_ascii=False) for x in v)
                lines.append(f"{k}: [{items}]")
        elif isinstance(v, (int, float)):
            lines.append(f"{k}: {v}")
        elif v is None:
            lines.append(f"{k}: null")
        else:
            lines.append(f"{k}: {json.dumps(str(v), ensure_ascii=False)}")
    return '\n'.join(lines)


def _merge_frontmatter(existing: str, override: dict) -> str:
    """Replace any duplicate keys in existing with override; append new keys."""
    seen = set()
    keep = []
    for ln in existing.splitlines():
        if ':' not in ln:
            keep.append(ln)
            continue
        k = ln.split(':', 1)[0].strip()
        if k in override:
            seen.add(k)
            continue  # skip — we'll write override below
        keep.append(ln)
    parts = list(keep)
    for k, v in override.items():
        if isinstance(v, list):
            items = ', '.join(json.dumps(x, ensure_ascii=False) for x in v)
            parts.append(f"{k}: [{items}]")
        elif isinstance(v, (int, float)):
            parts.append(f"{k}: {v}")
        elif v is None:
            parts.append(f"{k}: null")
        else:
            parts.append(f"{k}: {json.dumps(str(v), ensure_ascii=False)}")
    return '\n'.join(parts)


# ── Endpoint handlers (called by server.py Flask routes) ─────────────────────

def record_push(project_id: str, body: dict) -> tuple[dict, int]:
    """POST /api/project/<id>/distiller/record-push handler.

    Body shape (D6 + Seat 1/3 C-G):
      {"phrase": "<verb-noun-modifier>", "kind": "skill|exploration|preference",
       "decision": "no|later"}

    Server-side re-normalizes the phrase through the closed-vocab
    fingerprint function (single source of truth) so in-session agent
    output and silent Distiller use the same fingerprint key.

    Returns (json_dict, http_status).
    """
    if not _distiller_should_proceed(project_id, 'record_push'):
        return {'accepted': False, 'reason': 'distiller_disabled'}, 200
    phrase = _normalize_phrase((body or {}).get('phrase', ''))
    kind = (body or {}).get('kind', '')
    decision = (body or {}).get('decision', '')
    if kind not in ('skill', 'exploration', 'preference'):
        return {'accepted': False, 'reason': 'invalid_kind'}, 400
    if decision not in ('no', 'later'):
        return {'accepted': False, 'reason': 'invalid_decision'}, 400
    fp = fingerprint(phrase)
    if fp is None:
        return {'accepted': False, 'reason': 'oov_phrase'}, 400
    exact, coarse = fp
    key = f"{exact}:{kind}"
    with _get_skill_stats_lock(project_id):
        stats = _read_skill_stats(project_id)
        if decision == 'no':
            stats.setdefault('suppressions', {})[key] = {
                'decided_at': _now_iso(),
                'decision': 'no',
            }
        else:  # later
            # Compute current count to decide wait threshold
            window_days = int(_cfg('distiller_window_days', 30))
            window_sigs = _filter_window(stats.get('signals', []), window_days)
            cur_count = max(
                sum(1 for s in window_sigs
                    if s.get('kind') == ('topic' if kind == 'skill' else kind)
                    and s.get('exact') == exact),
                sum(1 for s in window_sigs
                    if s.get('kind') == ('topic' if kind == 'skill' else kind)
                    and s.get('coarse') == coarse),
            )
            stats.setdefault('suppressions', {})[key] = {
                'decided_at': _now_iso(),
                'decision': 'later',
                'wait_until_recurrence': cur_count + 1,
            }
        _write_skill_stats(project_id, stats)
    return {'accepted': True, 'exact': exact, 'coarse': coarse}, 200


def get_distiller_stats(project_id: str) -> dict:
    """GET /api/project/<id>/distiller-stats handler."""
    stats = _read_skill_stats(project_id)
    window_days = int(_cfg('distiller_window_days', 30))
    window_sigs = _filter_window(stats.get('signals', []), window_days)
    cap = int(_cfg('distiller_cost_cap_tokens_per_project_per_day', 100000))
    # Compute fingerprints_near_threshold (Seat 1 Cond 3 inherited telemetry)
    project = _load_project(project_id) if _load_project else None
    min_rec = int(_pcfg(project, 'distiller_min_recurrence', 3))
    exact_counts: dict[tuple[str, str], set[str]] = {}
    coarse_counts: dict[tuple[str, str], set[str]] = {}
    for s in window_sigs:
        k = s.get('kind', 'topic')
        exact_counts.setdefault((k, s.get('exact', '')), set()).add(s.get('sid', ''))
        coarse_counts.setdefault((k, s.get('coarse', '')), set()).add(s.get('sid', ''))
    near = sum(
        1 for (k, e), sids in exact_counts.items()
        if (min_rec - 2) <= len(sids) < min_rec
    )
    return {
        'project_id': project_id,
        'window_days': window_days,
        'signals_in_window': len(window_sigs),
        'counters': stats.get('counters', {}),
        'cost': stats.get('cost', {}),
        'cap': cap,
        'cap_hits': stats.get('cap_hits', 0),
        'fingerprints_near_threshold': near,
        'suppressions': len(stats.get('suppressions', {})),
        'outbox_entries': len(stats.get('outbox', {})),
        'last_walk_ts': stats.get('last_walk_ts'),
    }


def list_proposed() -> list[dict]:
    """GET /api/distiller/_proposed handler. Walks the unified _proposed/
    layout (global/ + <project_id>/) AND tolerates legacy flat entries
    per §3.0 (D13).
    """
    if _skills_root is None:
        return []
    out = []
    proposed = _skills_root / '_proposed'
    if not proposed.exists():
        return out
    # Walk both new layout (global/ + <project_id>/) and legacy flat
    for entry in proposed.iterdir():
        if not entry.is_dir():
            continue
        name = entry.name
        if name in ('global',) or _is_valid_project_id(name):
            # New layout: <scope>/<ts-hash-slug>/<kind>.md
            scope = 'cross-project' if name == 'global' else name
            for sub in entry.iterdir():
                if not sub.is_dir():
                    continue
                meta = _read_proposed_meta(sub, scope=scope)
                if meta:
                    out.append(meta)
        else:
            # Legacy flat _proposed/<sid>/ entry — surface as uncategorized
            meta = _read_proposed_meta(entry, scope='uncategorized')
            if meta:
                out.append(meta)
    # Sort newest first by created_at
    out.sort(key=lambda d: d.get('created_at', ''), reverse=True)
    return out


_RE_PROJECT_ID = re.compile(r'^[a-z0-9_-]+$')


def _is_valid_project_id(s: str) -> bool:
    """D13 validation — project IDs writing to _proposed/<pid>/ must
    match this pattern. 'global' is reserved (not a valid project_id)."""
    if s == 'global':
        return False
    return bool(_RE_PROJECT_ID.match(s))


def _read_proposed_meta(d: Path, scope: str) -> dict | None:
    """Read frontmatter metadata from one proposal directory."""
    try:
        for f in d.iterdir():
            if not f.is_file() or not f.name.endswith('.md'):
                continue
            text = f.read_text(encoding='utf-8', errors='replace')
            fm = _parse_frontmatter(text)
            return {
                'path': str(f),
                'directory': str(d),
                'scope': scope,
                'kind': fm.get('kind', f.stem.lower()),
                'name': fm.get('name', d.name),
                'extraction_scope': fm.get('extraction_scope', scope),
                'created_at': fm.get('created_at', ''),
                'evidence_session_ids': fm.get('evidence_session_ids', ''),
                'recurrence_count_exact':
                    fm.get('recurrence_count_exact', '1'),
                'recurrence_count_coarse':
                    fm.get('recurrence_count_coarse', '1'),
            }
    except Exception:
        return None
    return None


def _parse_frontmatter(text: str) -> dict:
    """Parse the leading YAML-ish frontmatter into a flat dict.

    Tolerant: missing frontmatter → empty dict; per-line `key: value`
    only. Lists are returned as the raw bracket-string for the UI to
    render.
    """
    if not text.startswith('---'):
        return {}
    end = text.find('\n---', 4)
    if end < 0:
        return {}
    out = {}
    for ln in text[4:end].splitlines():
        if ':' not in ln:
            continue
        k, v = ln.split(':', 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


# ── Module self-test entry (for tests/) ──────────────────────────────────────

def _vocab_lists():
    """Exposed for tests: the three closed lists as raw tuples."""
    return tuple(sorted(VERBS)), tuple(sorted(NOUNS)), tuple(sorted(MODIFIERS))
