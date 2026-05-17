# Clayrune Maintenance Protocol

Authored 2026-05-17 as the successor to `IMPROVEMENT_PLAN_V2.md`. The v2 plan
was a useful one-shot forcing function — it shipped the github_sync
correctness fixes, the test harness, and the KB refresh. Past that point,
single-shot mega-plans go stale faster than they execute (see F1–F7 in
`IMPROVEMENT_PLAN_V2_FLAWS.md`). This protocol replaces "plan, freeze,
refactor, unfreeze" with three durable rules + one recurring read-only audit.

## Rule 1 — New subsystems are born outside `server.py`

Every new feature lands in its own module from day one. Default is a Flask
blueprint, registered through the `register(app, deps)` injection pattern
(same shape `github_sync.register()` already uses, proven across `mcp`,
`skills`, `marketing_preview`, and the deferred trio designs in
`docs/SERVER_SPLIT_PLAN.md`).

`server.py` only grows when there is genuinely no other home — i.e. wiring
between subsystems, app factory, root routes. If a PR adds more than ~100
lines to `server.py`, the reviewer asks whether those lines belong in
(or could seed) a new module.

This rule is cheap at feature-time. It is brutal if deferred — see the
current 530 KB `server.py`.

## Rule 2 — Extractions are opportunistic, not scheduled

When a feature PR is already editing a contiguous region of `server.py`,
the agent may extract that region as part of the same PR — *if*:

- The region is well-bounded by section banners.
- The extraction is mechanical (move + re-export shim, no opportunistic edits).
- Existing tests still pass.
- The PR description explicitly notes the extraction and why it was safe.

No standalone "extraction sprints." Drift becomes the signal: when a
subsystem stops shipping changes for ~14 days *and* has a measurable
extraction shape, it becomes eligible. The monthly audit (below) surfaces
candidates; humans dispatch.

### The `# DRIFT-DEBT:` marker convention

When you're in a region that *should* be its own module but extracting
it now is **not** clean (dispersed call sites / shared state — e.g. the
deferred trio), don't extract speculatively and don't stay silent. Leave
one greppable marker line at the top of the region:

```python
# DRIFT-DEBT: <subsystem> belongs in its own module — see SERVER_SPLIT_PLAN.md
```

- **Syntax:** exactly `# DRIFT-DEBT: ` + one line. One per region, at its
  top. No multi-line; link a doc if a recipe exists.
- **When to leave one:** you touched a region Rule 1 would have made a
  module, but Rule 2's "clean + mechanical" bar isn't met this PR. Also
  valid: a config key / `SHARED_RULES.md` line / doc section you noticed
  contradicts current behavior but is out of this PR's scope.
- **Who clears it:** whoever next does a feature PR in that region and
  finds it now *is* clean (extract + delete the marker in that PR), or
  Ron when dispatching an audit-surfaced candidate. Never delete a
  marker without resolving the underlying debt.
- **Lifecycle:** the monthly audit greps `# DRIFT-DEBT:` (checklist
  step 7), counts them, and flags any that have survived 3+ sweeps for
  explicit decision. Markers are a worklist, not decoration.

## Rule 3 — Deps-injection re-export shim is the default pattern

For any module that shares state with `server.py` (the deferred trio, future
extractions of agent_session, push, presence, hivemind, claydo): the moved
module owns the state; `server.py` re-exports via `from <module> import
<symbol>` so existing call sites keep working unchanged. Call sites migrate
to the new module gradually, as adjacent code is touched. Avoid 30-file
diffs in one PR.

Concrete example (process_tracker, when it eventually moves):
```python
# process_tracker.py
tracked_processes: dict[int, ProcessInfo] = {}
_lock = threading.RLock()

def register_process(...): ...
def unregister_process(...): ...
```
```python
# server.py — until call sites migrate
from process_tracker import tracked_processes, _lock as _tracked_lock
from process_tracker import register_process, unregister_process
```
Smoke test (`tests/test_smoke.py`) catches breakage on import.

## The monthly read-only audit

A scheduled Clayrune job runs the prompt in `docs/MAINTENANCE_AUDIT_PROMPT.md`
on the first of each month (or on-demand). The agent is **read-only** —
forbidden from staging, committing, or modifying any tracked file. Output is
a single Markdown report saved to `data/maintenance_reports/<YYYY-MM-DD>.md`
and surfaced as a high-priority backlog item titled
`Maintenance sweep — <date>`.

Ron reviews the report and decides what to action. Each actionable item
becomes its own supervised dispatch — normal feature-PR discipline applies.

### Audit checklist (the prompt operationalizes this)

1. **`CLAUDE_KB.md` freshness.** Is the header date >30 days old? Does the
   "Active Backlog" / "Recent Changelog Highlights" reflect the last 30 days
   of CHANGELOG? List specific gaps.
2. **`server.py` growth.** Current line count vs last sweep. Top 5 sections
   that grew by line count. Anything that crossed a 500-line threshold.
3. **Extraction candidates.** For each subsystem in the deferred trio +
   currently-frozen list (agent_session, push, presence, hivemind, claydo):
   days since last CHANGELOG mention, current ref count, recommendation
   (keep frozen / candidate for opportunistic extraction / actively ripe).
4. **New large files.** Any tracked Python file >300 lines that did not exist
   at the previous sweep date. Recommend whether it should have been a module
   from day one (Rule 1 compliance check).
5. **TODO / FIXME / XXX trend.** Net change in count by tag, with paths to
   newly-introduced ones.
6. **Test surface.** File count under `tests/`, last green run date from CI,
   any tests skipped/xfailed at HEAD.
7. **Drift-debt items.** Any `data/SHARED_RULES.md`, config keys, or doc
   sections that contradict current behavior. Specific line refs.
8. **Frozen-subsystem decay.** Subsystems on the freeze list that have been
   quiet for 14+ days — propose defrost candidates.

The report is short, structured, and machine-parseable. No prose theatre.

## Anti-patterns

The following have been tried and don't work on this project:

- **Big-bang refactor plans.** The v2 plan worked for correctness fixes
  because they were localized. It failed for `server.py` split because the
  underlying assumption (WIP confined to a few subsystems, code stable
  enough to extract verbatim) doesn't survive continuous shipping. F1, F2,
  F7 all trace to this.
- **Cron jobs that modify code.** Unsupervised agent runs that commit
  changes create F-series flaws while no human is watching. The audit is
  read-only by design.
- **"Find every issue" reviews.** Broad reviews produce 30+ findings;
  actionable rate is <20%; unactioned items become noise. Scope every
  review to one question.
- **Stale plans left in repo.** A plan document that doesn't match current
  reality is worse than no plan — agents read it as ground truth. Plans
  either get refreshed on a known cadence, marked SUPERSEDED, or deleted.

## When to revisit this protocol

This document itself drifts. The monthly audit checks (in step 7) whether
`MAINTENANCE_PROTOCOL.md` still matches observed behavior. If three
consecutive audits flag the same drift-debt item against this protocol,
the protocol gets revised — not the practice.

Last revised: 2026-05-17.
