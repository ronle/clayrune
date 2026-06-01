## Seat 1 — Concurrency & lifecycle — RATIFY-WITH-CONDITIONS

**Decision:** RATIFY-WITH-CONDITIONS

**Summary (1-2 sentences):** The "external scheduler mirror + in-VM fire-or-skip" bridge is architecturally sound and the one-VM-per-user mapping onto the single-instance invariant is correct, but the design under-specifies four real failure paths that, if shipped as-is, would silently drop missed runs, fire due jobs minutes late, and risk corrupting an in-flight Mode-B agent turn at suspend. None of these is a hard blocker because each is fixable within the locked sleep/wake frame — but each must be closed in the design or gated to a build phase before compute-plane code lands.

---

### Blockers (if any)

None. Every failure path I pressure-tested degrades to "late / once / recoverable" rather than "corrupt / lost-without-recourse" **provided** the conditions below are met. The one path that *could* corrupt state (in-flight Mode-B turn across suspend, Condition 3) is preventable by an idle guard the codebase already has the primitive for (`_has_running_agent`, `server.py:1137`), so it is a must-fix-in-design condition, not a blocker.

---

### Conditions (RATIFY-WITH-CONDITIONS only)

#### Condition 1 (must-fix-in-design): The mirror's pre-wake lead time must be specified to absorb a stacked `MC-boot + up-to-30s scheduler-tick` skew, not "shortly before due."

**Why:** I read the loop. `_scheduler_loop()` (`server.py:12534`) is **check-first, wait-last**: the `while not _scheduler_stop.is_set()` body runs the overdue comparison `if now >= nr_dt` (`server.py:12558`) at the *top* of the iteration, and `_scheduler_stop.wait(30)` is the *last* statement (`server.py:12712`). So a freshly-booted process does fire an already-overdue job on its **first** iteration — good, no 30s penalty *if the job is already overdue at boot*. But §4.3 says the mirror wakes the VM **"shortly before"** the job is due. If the wake lands the VM up even 1 second *before* `next_run`, the first iteration's `now >= nr_dt` is **False**, the loop falls through to `wait(30)`, and the job does not fire until the *next* iteration — up to **30 seconds late**. Stack that on top of MC cold-boot wall time (Flask init + `load_projects()` + thread starts; `_startup_memory_maintenance` is threaded off the boot path per `server.py:4714`, so it does *not* block, but port-conflict check + scheduler thread start at `server.py:16488-16489` still precede readiness) and the Fly VM resume latency, and "shortly before" can produce a job that fires a minute-plus after its wall-clock time. For a "9:00 AM market open" scheduled scan that is a correctness bug, not cosmetic.

**Proposed fix:** Specify the mirror wakes the VM so that MC is *fully booted and past its first scheduler iteration* **at or after** `next_run` — i.e. lead time = `p95(VM_resume) + p95(MC_boot_to_first_tick)` with a safety margin, AND have the mirror pass the wake as "job is already due" (wake at `next_run`, not before it) so the first loop iteration's `now >= nr_dt` is immediately True and the 30s `wait` is never on the critical path. The design must state the target end-to-end skew budget (e.g. "due jobs fire within ≤ X s of wall-clock") and derive lead time from Phase-0 measured `VM_resume` + `MC_boot` numbers (this is the dependency on Seat 3's cold-boot measurement).

**Gate phase:** Phase 2 (sleep/wake + scheduler mirror) for implementation; the skew-budget statement itself is a Phase-0/design edit because it sets the Phase-0 spike's success criteria.

---

#### Condition 2 (must-fix-in-design): Fire-or-skip's "missed N windows → fires once" semantic must be an explicit, documented product decision, not an inherited side effect.

**Why:** Confirmed in code for **every** schedule type, not just the obvious case. On resume the loop fires the one overdue instance, sets `last_run`, then recomputes a *single* forward `next_run`:
- `daily`/`cron`: `_compute_next_run` (`server.py:12444`) computes the *next future* candidate from `now` (daily loop `server.py:12487-12494`; cron `_next_cron_match(expr, now_local)` `server.py:12520`). A VM asleep across three nightly 02:00 windows wakes, fires **once**, and schedules the *next* 02:00. The two missed nights are gone.
- `interval`: even sharper — `server.py:12507-12509` computes `nxt = last_dt + interval`, and if that is already past it **clamps to `now + 5s`** (`server.py:12509`). An interval schedule that slept for hours fires once and resets its phase to wake-time; all intervening intervals are dropped.

On an always-on PC (today's product) that same daily job fires **three** times across three nights. Hosted mode will fire it **once**. That is a real semantic divergence from the product it replaces. It may be *acceptable* (a daily diagnostic only needs the latest state; firing 3 stale catch-ups at once could be worse), but the design currently presents it as a free "clean bridge" (§4.3 "exactly as it does today") and buries the loss in "fire-or-skip." It is not free; it is a behavior change the user can observe.

**Proposed fix:** Add an explicit subsection to §4.3 stating the hosted semantic: *"A schedule that comes due while suspended fires exactly once on the next wake; missed occurrences are not backfilled. This matches the in-VM fire-or-skip loop and differs from an always-on machine, which fires every occurrence."* Then state the deliberate mitigation: because the mirror is the wake source, the mirror should wake the VM **for each** due occurrence it knows about (it holds `next_run` per schedule), so under normal operation no window is actually missed — the "fire once" path is only the *degraded* fallback when a wake itself was missed. That reframes fire-or-skip from "the mechanism" to "the safety net," which is both honest and better behavior. Document the residual: if the *mirror* (not the VM) is down across multiple windows, the user loses all but one — list that in §10 risks.

**Gate phase:** Phase 2 (validate "scheduled jobs fire correctly across suspend/resume" must include a multi-window-miss test asserting the documented semantic).

---

#### Condition 3 (must-fix-in-design): Idle detection must FORBID suspend while any agent session is `running` OR `idle`, and the design must state this explicitly — Mode B (persistent process, live HTTPS socket) is the deployed runtime and a mid-turn snapshot is the one path that can corrupt rather than merely delay.

**Why:** I verified the live runtime: `/api/config` returns `use_streaming_agent: True` and `scribe_checkpoint_enabled: True` on this deployment. So **Mode B is active** — the Claude CLI is a *persistent* subprocess (`_stream_reader_modeB`, `server.py:4081`) that stays alive between turns with `session['process_alive']` tracking (`server.py:4210/4296`). A microVM suspend snapshots RAM + the process + its open file descriptors, including the **live TLS socket to the Anthropic API mid-turn**. On resume, that socket's peer state is long gone (Anthropic closed it after the idle timeout); the CLI will see a dead/half-open connection. Best case the CLI surfaces a network error and the turn fails (recoverable — revival-from-log exists, `_revive_from_agent_log` `server.py:4787`, default-on); worst case the stream reader hangs on a socket that never returns data, leaving `status='running'` forever with no completion event, which is exactly the stuck-session state the guardian and the 30-min stale purge (`server.py:12663-12689`) exist to clean up — i.e. it *self-heals to "failed"*, but the user's turn is lost mid-flight.

§4.3's sleep trigger already lists "no running agent session" as an idle precondition — good — but it does **not** define what "running" means against the code, and the codebase's own definition is broader than the prose implies: `_has_running_agent` (`server.py:1137`) returns True for status in **`('running', 'idle')`** — i.e. a Mode-B process that is *alive between turns* (`idle`) counts as active. That is the correct, conservative definition (suspending an `idle` Mode-B process still kills its warm KV-cache and its live socket), and the design must adopt it verbatim, not a narrower "is a turn actively streaming" reading.

**Proposed fix:** §4.3 must state: *"Suspend is forbidden while `_has_running_agent(pid)` is True for any project — i.e. any session in status `running` OR `idle` (Mode B keeps the process `idle`/alive between turns with a warm cache and, mid-turn, a live API socket). The idle-detection probe MUST treat both as non-idle."* Additionally, because Mode B holds a warm process even when no turn is in flight, the design should note that the keep-warm cooldown (§10.2) and the agent-idle guard interact: a Mode-B session that is `idle` but not torn down will *block suspend indefinitely* until the session is finalized/torn down — so the orchestrator needs a defined max-session-age teardown (or rely on the existing 30-min stale purge to drop the in-memory session, after which suspend is permitted). State which.

**Gate phase:** must-fix-in-design (the guard rule); Phase 2 implementation + a soak-gate test that asserts no suspend fires while a synthetic long-running agent turn is in flight.

---

#### Condition 4 (must-fix-in-implementation): The orchestrator's idle/next-run probe needs a single endpoint that aggregates running-session state AND next due job across ALL projects; today no such endpoint exists.

**Why:** §9 hand-waves this as "a tiny in-VM agent (or reuse the health endpoints)." I checked the three candidate endpoints the brief named:
- `/api/system/status` (`server.py:15568`, payload builder `15547`) returns the cached model/version/rate-limit dict (`_LAST_SYSTEM_STATUS`) — it exposes **nothing** about running sessions or `next_run`. Unusable for idle detection.
- `/api/project/<id>/agent/status` (`server.py:8266`) exposes per-session status including the Mode-B `process_alive` field (`server.py:8291`) — but it is **per-project**. The orchestrator would have to enumerate every project and fan out N calls to know the VM is idle. Racy and slow.
- `/api/schedules` (`server.py:12875`) does return all schedules with `next_run` — so the *next-due-job* half is available globally.

So the "no running agent" guard (Condition 3) has **no single source of truth over HTTP today**. `_has_running_agent` reads the in-process `agent_sessions` dict (`server.py:943`, plain in-memory) project-by-project; there is no global "is *anything* running" route. The orchestrator must not guess idle from "no SSE connection" alone — mobile SSE is known-flaky (Doze parks Capacitor sockets; see MEMORY.md mobile-SSE entries), so "no SSE" will frequently be a false-positive-idle while a headless turn runs. The second guard (no running agent) is therefore **load-bearing**, and it currently requires an N-project fan-out.

**Proposed fix:** Add one control-plane-facing endpoint to the in-VM server, e.g. `GET /api/system/idle-state`, returning `{running_agents: <int over all projects, status in running|idle, excluding housekeeping>, soonest_next_run: <iso|null>, last_activity_at: <iso>}`. Implement `running_agents` by iterating `agent_sessions.values()` once with the **same** `('running','idle')` + `not housekeeping` predicate as `_has_running_agent` (`server.py:1137-1143`); implement `soonest_next_run` from `_load_schedules()`. This is ~30 LOC, lives behind the "hosted mode" flag (§9), and gives the orchestrator an atomic, single-call safe-to-suspend signal. The orchestrator's suspend decision = `running_agents == 0 AND no SSE AND (soonest_next_run is None OR soonest_next_run - now > cooldown)`.

**Gate phase:** Phase 2 (the endpoint is part of the sleep/wake build; without it idle detection is unsafe).

---

#### Condition 5 (must-fix-in-implementation): The `next_run` mirror sync must be ordered strictly BEFORE suspend completes, and a failed sync must block the suspend (not proceed with a stale mirror).

**Why:** §4.3 says the mirror is "synced from the VM on each sleep." The race: a user edits a schedule (new `next_run` written to `data/schedules.json` via the POST handler at `server.py:12885`) in the **last awake moment**, then the orchestrator decides to suspend. If the suspend snapshot is taken/committed *before* the mirror reads the fresh `next_run`, the mirror holds the **old** timestamp and will wake the VM at the wrong time — or not at all if the edit *added* an earlier job the mirror never learned about. The doc claims the worst case is "fires late on next wake," but that is only true if the mirror eventually learns the new time. If the mirror's sole sync opportunity is "on sleep" and that sync raced the suspend, the mirror can hold a `next_run` that is **wrong in the early direction** (job due sooner than the mirror thinks) → the wake is scheduled too late or skipped, and the VM sleeps **through** the due job with no other wake source. That is worse than "late"; it is "missed until the next unrelated wake," which collapses into the Condition-2 single-fire loss.

**Proposed fix:** Make the suspend a two-phase commit: (1) orchestrator reads `/api/schedules` (or the new idle-state endpoint extended with schedule data) and persists the mirror, (2) **only on successful sync** issue the Fly suspend. If the sync fails, abort the suspend and retry on the next idle tick (the VM stays awake a little longer — pure cost, never correctness). State explicitly that a sync failure is a *suspend-blocker*, so the documented "degrades to fire-late" worst case actually holds. Also: because the in-VM scheduler keeps running right up to suspend, snapshot the mirror from a *quiesced* read — read schedules after the idle guard already proved no agent is running, to minimize the window where the in-VM loop mutates `next_run` (it does so at `server.py:12613`) between the mirror read and the suspend.

**Gate phase:** Phase 2.

---

#### Condition 6 (soak-gate): Validate that Fly suspend/resume is genuinely snapshot-clean for a live Mode-B process — i.e. resume does NOT trip the hard-kill path — before flipping sleep/wake default-on.

**Why:** This is the suspend ≠ kill distinction the brief flagged. The memory system's `_reconcile_unscribed_sessions` (`server.py:4548`, called from the threaded `_startup_memory_maintenance` at `4727`) exists precisely to close the **hard-kill gap**: when MC is *killed*, `_log_agent_completion` never runs, and on the *next boot* reconcile baseline-stamps or re-scribes the orphaned sessions. Two distinct lifecycle transitions exist and the design conflates them:
- **Suspend → resume (snapshot-clean, the design's assumption):** RAM is restored, `agent_sessions` (in-memory, `server.py:943`) is intact, the process never restarted, so reconcile never runs and never needs to. The only casualty is the live API socket (Condition 3, prevented by the idle guard).
- **Suspend that the provider implements as stop, or a destroy/redeploy/crash → cold boot (the hard-kill path):** the process is gone, `agent_sessions` is **lost** (it does not persist), MC reboots, `_reconcile_unscribed_sessions` fires, and any session that was `running`/`idle` at the moment of the kill is an orphan to be scribed-after-the-fact. Mode-B revival (`_revive_from_agent_log`, `server.py:4787`, default-on) can reconstruct the *conversation* on the next user turn, but the *in-flight turn* at kill time is lost.

The design's §4.2 names Fly suspend/resume but its alternatives list (§4.2: "AWS Firecracker," "container runtime") includes options whose "idle" primitive is **stop**, not **suspend** — i.e. a cold boot every wake. If the chosen provider's "scale-to-zero" is actually stop-and-cold-start, then **every** wake traverses the hard-kill path, `_reconcile_unscribed_sessions` runs on every wake (adding to cold-boot time — cross-ref Seat 3), and the "agent_sessions survives" assumption underpinning Conditions 3–4 is **false**. The design must commit to a *true RAM-snapshot suspend* primitive and prove it.

**Proposed fix:** Phase-0 spike must explicitly test: start a Mode-B agent, drive it to `idle` (process alive, warm), suspend, wait past Anthropic's socket-idle timeout, resume, and assert (a) `agent_sessions` still contains the session, (b) `process_alive` is recoverable / the next turn dispatches on the warm process, and (c) `_reconcile_unscribed_sessions` did **not** fire (proving no cold boot). If the provider can only stop (cold boot), the design must either (i) accept cold-boot-every-wake and re-cost it (Seat 3) and re-validate idle detection against a fresh-process `agent_sessions={}`, or (ii) require a suspend-capable provider as a hard Phase-0 gate. Flip to default-on only after the snapshot-clean assertion passes on the production provider.

**Gate phase:** soak-gate (blocks default-on flip / GA), with the underlying primitive proven in Phase 0.

---

### Ratifications

- **One-VM-per-user onto the single-instance invariant is the right call and correctly grounded.** `_check_port_conflict()` (`server.py:13282`, called `16488`) makes the server fatal-on-second-instance; leaning into that as hardware isolation per user (rather than a multi-tenant refactor) avoids touching `agent_sessions`, the per-project locks, the scheduler, and the memory writers — all of which assume sole ownership of `DATA_ROOT`. The "zero server code changes for tenancy" claim holds for the *concurrency* surface.
- **The external-mirror-as-wake-source pattern is correct and minimal.** Keeping the in-VM scheduler untouched and using the control plane only to *wake* (not to *fire*) means the firing logic — including the continued-session threading at `server.py:12567-12605` and the `_dispatch_agent_internal` reuse-session path — is unchanged. The mirror needs only `next_run`, which `/api/schedules` (`server.py:12875`) already serves. This is genuinely the low-risk bridge the doc claims, *once* Conditions 1/2/5 tighten the timing and sync semantics.
- **Listing "no running agent" as an idle precondition (§4.3)** is the correct instinct; it just needs the code-accurate definition (Condition 3) and a real endpoint (Condition 4).
- **The "worst case degrades to fire-late, never a hard break" framing** is the right *target* posture and is achievable — Conditions 1/2/5 are precisely the work to make that framing *true* rather than aspirational.

---

### Out-of-scope but flagged

- **(→ Seat 2) Suspend snapshot contains the BYOK key at rest.** Condition 3/6 establish that a true suspend persists process RAM to disk. The injected `ANTHROPIC_API_KEY` lives in the MC process environment/memory → it is in the snapshot blob. This directly undermines §10.1's "inject only at wake, never persisted." Whether the provider encrypts the snapshot, and whether §5.4 *revoke* ("clear vault + force restart") actually destroys a snapshot that could otherwise resurrect a revoked key, is Seat 2's call — but the *lifecycle mechanism* (suspend = RAM-to-disk) is the thing that creates the at-rest exposure, so flagging from here.
- **(→ Seat 3) Cold-boot wall time is on the wake critical path and compounds Condition 1.** If the provider's idle primitive is stop-not-suspend (Condition 6), every wake pays MC boot: `_check_port_conflict` + `_start_scheduler`/`_start_hivemind_orchestrator` (`server.py:16488-16490`) + the threaded `_startup_memory_maintenance` (backfill + `_reconcile_unscribed_sessions`, `server.py:4714-4733`) + `_install_builtin_skills`/`_install_builtin_mcps` checksum scans (`server.py:16494-16497`). Seat 3 must measure this; Seat 1's scheduler-skew budget (Condition 1) *depends* on that number.
- **(→ Seat 3) Hivemind mid-flight wake economics.** §4.3 treats a mid-flight hivemind workstream as a "scheduled wake." The hivemind loop (`server.py:10342`, 10s cadence) auto-spawns workers and retries failed ones (`server.py:10390-10396`). A hivemind with serial dependencies could keep the VM awake for a long contiguous stretch (each workstream spawns the next), which is correct behavior but a sustained-compute cost the §8 "minutes of compute" model may under-count. Routed to cost.
- **(→ Seat 4 / general) The 30-min stale-session purge interacts with suspend duration.** The purge (`server.py:12663-12689`) drops in-memory sessions whose `started_at` is >30 min old and not running/idle. Across a long suspend, wall-clock advances while the process is frozen; on resume the purge may immediately reap sessions that were "recent" at suspend time. Benign (they're terminal anyway) but worth a note in the design's lifecycle section so it isn't mistaken for data loss during Phase-2 validation.
