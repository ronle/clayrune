# Autonomous Steward — `fire-and-forget` self-directing agent (SCOPE)

**Status: BUILT (2026-07-10). MVP steps 1–5 shipped behind the enable/disable
API; 78 tests green. Goes LIVE on the next MC restart (installs the mc-steward
builtin + registers the blueprint). Remaining: a Skills-style UI + merge to
master. Decisions locked below.**

**Build ledger (2026-07-10):**
- Step 1 — `mc-steward` builtin directive skill (`data/skills/builtin/mc-steward/`). ✅ `fc5dccc`
- Step 2 — steward/ package: config, charter, cycle-task, notify seam. ✅
- Step 3 — bootstrap (`/steward/enable` seeds charter + schedule + fence) & kill switch. ✅
- Step 4 — reversibility FENCE (`steward/fence.py` PreToolUse hook, fail-closed;
  verified it fires even under `--dangerously-skip-permissions`). ✅
- Step 5 — loop-health (`GET /api/steward/loop-health`). ✅
- Fence wiring: installed into the project's own `.claude/settings.json`, but
  the hook **SELF-GATES** — `fence.py` reads the CC transcript and enforces ONLY
  when the session's first user message carries the `[Steward cycle]` marker.
  So manual/dev sessions in the same project run unfenced; only steward cycles
  are fenced. This makes it safe on a live dev repo (mission_control itself).
  Gate: confirmed-non-steward → allow all; steward OR unknown → enforce
  (fail-closed on ambiguity). Runs fresh per tool call → changes are live with
  no restart.
- NOT built: a dashboard UI to enable/steward + review the decision queue (curl
  the API for now); merge to master.

The north star: dispatch an agent **once**, hand it a *field of responsibility*,
and let it run unattended — it assesses state, **sets its own next goal and next
step**, does the reversible work, asks only when it must, and reports to the
human over Clayrune surfaces. This is the last data-plane rung of the
self-learning journey (`docs/SKILLS_CURATION_PHASE5_AUTOMODE_ROLLBACK_SCOPE.md`
is the sibling rung for *skill* self-install; this doc is the *action* rung).

The insight from the 2026-07-10 primitives audit: **~80% already ships.** The
steward is mostly a system-prompt + a self-scheduled continuation over existing
scheduler / backlog / memory / push machinery. Two things are genuinely new: the
**self-goal loop** and (deferred) a **unified inbox**.

---

## 0. Locked decisions (Ron, 2026-07-10)

1. **Engine = single-agent steward loop.** One steward per project on a
   self-scheduled continuation thread. NOT the hivemind DAG (that drives a
   fixed decomposition to "done"; the steward is open-ended). Hivemind stays
   available as a graduation path for stewards that later need parallel workers.
2. **Autonomy = reversible-freely / irreversible-asks.** The steward does any
   *reversible* action autonomously (matches Ron's standing full-autonomy
   preference); for *mutating / irreversible* actions it posts a
   `decision-needed` item and waits. This is the distiller `_proposed → promote`
   gate applied to **actions instead of artifacts**.
3. **Comms = a pluggable seam, not a build here.** A separate in-flight effort
   owns the unified inbox / messages / email surface. The steward writes through
   a thin `steward_notify(project_id, kind, body)` seam; the MVP backs it with
   the primitives that already ship (PushNotification + backlog notes + beacon),
   and the inbox slots in behind the same seam when it lands. Do NOT build the
   inbox in this workstream.

---

## 1. The loop (the one genuinely new thing)

Today an agent continues only on a **human-queued follow-up** or a
**pre-decomposed hivemind DAG**. Nothing lets a *single* agent wake, invent its
own next step, and re-arm itself. The steward closes that — and it's a thin
layer, because the re-entry, self-schedule, self-backlog and memory-readback all
already exist.

Per cycle (one scheduled fire = one turn on a continued thread):

1. **Wake** — Clayrune local scheduler fires; `_scheduled_continue`
   (`scheduler_routes.py:568`) re-enters the **same session_id / same Claude
   conversation** (or revives it via `_revive_from_agent_log`). No fresh tab.
2. **Orient** — read the objective + backlog + memory readback + last cycle's
   notes (all injected by `_build_agent_context` already).
3. **Decide** — the steward system-prompt directive makes the model pick the
   single highest-value next step itself. *This is the new behavior; it's prompt,
   not plumbing.*
4. **Act** — reversible work: proceed. Irreversible/mutating: post
   `decision-needed` via the notify seam and stop this step (§3).
5. **Record** — append a dated progress note to the backlog item / MEMORY (the
   Scribe already captures the turn).
6. **Report** — `steward_notify` FYI/done/blocked as warranted (§4).
7. **Re-arm** — set the next wake. Cadence is the steward's own call within a
   configured floor/ceiling; it `PUT /api/schedules/<own-id>` to adjust, or
   leaves the standing cadence. Self-scheduling is already sanctioned
   (`agent_routes.py:1387`).

### The steward directive (the actual new artifact)

A system-prompt block, injected only when `steward_mode` is on for the project,
roughly:

> You are the autonomous **steward** of this project. Your field of
> responsibility: `<objective>`. You run unattended on a schedule; each fire is
> one cycle. Each cycle: (a) assess current state from backlog + memory + your
> last notes; (b) choose the SINGLE highest-value next step toward the
> objective; (c) if it is reversible, do it; if it is mutating or irreversible
> (deploy, delete, external send, spend, force-push, schema change), do NOT do
> it — post a `decision-needed` item describing the action + why + the exact
> command, and move on; (d) log what you did; (e) message the human only when
> they need to KNOW or DECIDE — silence is fine; (f) ensure your next wake is
> scheduled. Never block the loop waiting; if you're stuck, post `blocked` and
> re-arm for later.

Delivered as a skill (`data/skills/builtin/mc-steward/SKILL.md`) so it's
versionable + checksum-updated like the other built-ins, plus a short read-floor
line when `steward_mode` is on. Skill, not hardcoded, so it's editable and
scoped.

---

## 2. Reuse map (what the steward stands on — all shipping today)

| Need | Primitive | Location |
|---|---|---|
| Re-enter same thread each cycle | `_scheduled_continue` | `scheduler_routes.py:568` |
| Self-schedule / re-arm | `POST/PUT /api/schedules` (agent already told) | `scheduler_routes.py:641`; prompt `agent_routes.py:1387` |
| Self-authored goals | backlog CRUD + `TodoWrite` auto-sync | `project_routes.py:553`; `agent_routes.py:2002` |
| Progress notes to human | `add_backlog_note` | `project_routes.py:652` |
| Ping human | PushNotification → FCM | `push_mobile.py:473` |
| "Anything need me?" digest | Beacon | `beacon/`, `beacon_routes.py` |
| Memory readback into cycle | `_build_agent_context` + exploration read-floor | `agent_routes.py:1515`; `distiller.exploration_read_floor` |
| Escalation hard-stop | AskUserQuestion interception | `agent_routes.py:2010` |
| Reversibility-gate pattern | distiller `_proposed → promote` | `distiller_routes.py:109/188` |

---

## 3. The guardrail — reversible-freely / irreversible-asks

The firewall that makes unattended operation safe. **Enforced in the directive
first, backstopped in code where cheap.**

- **Reversible (proceed autonomously):** edits in the working tree, reads,
  analysis, drafting, local file writes under the project, backlog/schedule
  self-management, proposing (never installing) skills.
- **Irreversible (post `decision-needed`, do NOT act):** anything that leaves
  the box or can't be undone — `git push --force`, deletes outside `_scratch/`,
  deploys, external sends (email/API POST to third parties), spend, package
  publishes, schema/migration, server restart (already human-gated per
  `feedback_server_restart_approval`).
- A `decision-needed` item carries: the action, the rationale, and the **exact
  command** so the human's approval is one click / one reply, and the steward
  executes it verbatim next cycle.
- **Kill switch:** `steward_mode=off` per project (default off) halts new cycles;
  in-flight turn finishes. Mirrors the distiller kill switch discipline.

---

## 4. The comms seam (deferred — do not build the inbox here)

Single choke point so the in-flight inbox work is a drop-in:

```
steward_notify(project_id, kind, body, *, action=None)
    kind ∈ {fyi, done, blocked, decision-needed}
```

MVP backing (compose what ships): `fyi/done` → backlog note + optional
PushNotification; `blocked/decision-needed` → backlog note (durable) +
PushNotification (alert) + surfaced in Beacon triage. When the unified inbox
lands, re-point `steward_notify` at it — the steward code and directive don't
change. **Reply-back-into-next-cycle** (human answers a `decision-needed`, the
answer appears in the next cycle's context) is the one capability the seam must
eventually guarantee; MVP approximates it by having the human's backlog-note
reply / schedule edit be visible to `_build_agent_context`.

---

## 5. MVP definition + build order

MVP = one project, one steward, running unattended for a multi-day objective,
reporting via existing surfaces, asking before anything irreversible.

1. **`mc-steward` skill + read-floor line** (the directive). Prompt-only;
   zero runtime risk. — *smallest, first.*
2. **`steward_mode` + `steward_objective` + cadence floor/ceiling** per-project
   config (default off) + the notify seam (`steward_notify`, MVP backing).
3. **Steward bootstrap**: turning `steward_mode` on for a project seeds a
   self-continuing schedule (the standing cadence) pointed at the steward
   directive; turning it off is the kill switch.
4. **Reversibility backstop** (cheap code checks for the highest-risk verbs) +
   `decision-needed` execution path (human approves → steward runs the exact
   command next cycle).
5. **Loop-health** for the steward (reuse the distiller loop-health shape):
   cycles run, decisions pending, blocked count, last-wake age — so a stuck or
   runaway steward is visible.

Steps 1–3 are the walking skeleton; 4–5 harden it. Comms inbox and hivemind
graduation are explicitly out of scope.

---

## 6. Open questions (resolve before build, not now)

- Cadence model: fixed standing interval the steward nudges, vs. steward sets
  each next wake explicitly. (Lean: standing interval + allowed self-nudge
  within floor/ceiling — bounds runaway.)
- Objective representation: a single north-star string vs. a pinned top-of-
  backlog "charter" item. (Lean: charter item — visible, editable, versioned
  like everything else.)
- Where reversibility enforcement lives: directive-only (trust) vs.
  directive + a code backstop on the riskiest verbs. (Lean: both — belt and
  suspenders for an unattended actor.)

---

*Companion docs: `SKILLS_CURATION_PHASE5_AUTOMODE_ROLLBACK_SCOPE.md` (skill
self-install rung), `SKILLS_CURATION_PHASE4_SPEC_V2.md` (learning substrate).
Primitives audit that grounds this doc: 2026-07-10 session.*
