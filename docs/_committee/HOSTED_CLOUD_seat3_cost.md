## Seat 3 — Cost & ops — RATIFY-WITH-CONDITIONS

**Decision:** RATIFY-WITH-CONDITIONS

**Summary (1-2 sentences):** The sleep/wake + BYOK frame is sound and the *active-compute* unit economics genuinely survive a modest subscription — but the design under-states three cost lines that compound (the always-on **storage floor**, our-side **egress**, and **MC cold-boot stacked on VM resume**), and it carries **zero runaway-compute primitive** while inviting `/loop`/hivemind workloads. The storage-floor-for-dormant-users mitigation and a wake/compute cap are mis-phased: the design assigns them to Phase 3/4, but at least the dormant-storage decision is **Phase-0/1 gating** because it determines whether a free tier can exist at all.

---

### Blockers (if any)

None that flip the decision to BLOCK. The active-session math is positive, cold-start is survivable on the *stop* (not *suspend*) path, and the storage floor is bounded and recoverable — *provided* the conditions below land. I came close to blocking on the storage floor + missing compute cap; they are downgraded to must-fix-in-design conditions because each has a concrete, cheap fix that fits the existing architecture.

---

### Conditions (RATIFY-WITH-CONDITIONS only)

#### Condition 1 (must-fix-in-design) — Re-do §8 with real arithmetic and pick "stop" vs "suspend" explicitly; the headline understates the floor.

**Why:** §8 says a 20-min/day + one-nightly-job user costs "~minutes of compute + a small volume." The *compute* half is true; the *floor* half is materially understated, and the design says "**suspend**" everywhere (§2, §4.2, §10.2) without confirming suspend is the $0 state. On Fly, a **stopped** machine bills $0 for CPU/RAM (you pay only the volume); a **suspended** machine keeps a RAM snapshot resident and historically can carry a reservation/RAM charge. The whole "zeroes compute cost when idle" premise (locked decision #2) depends on which one the orchestrator actually uses.

**Arithmetic (public Fly pricing; stated as assumptions — the doc itself defers exact numbers to a Phase-0 spike, so these are my grounding, not gospel):**

Per-second assumptions (Fly, NA/EU, my knowledge):
- `shared-cpu-1x` @1GB ≈ **$5.70/mo** 24/7 → ~$0.0000022/s. MC (Flask) **plus** the Node Claude CLI **plus** an agent subprocess **plus** a real git checkout realistically wants **2–4GB**. Use `shared-cpu-2x` @4GB ≈ **~$32/mo** 24/7 → **~$0.0000122/s**.
- Volume: **~$0.15/GB-mo**.
- Egress: **~$0.02/GB** (NA/EU; APAC/India higher).
- **Stopped** machine compute: **$0**. **Suspended** machine: *assume non-zero until Phase 0 proves otherwise.*

Active-compute for the headline persona (4GB machine):
- Interactive: 20 min/day awake = 1,200 s/day × 30 = **36,000 s/mo** → 36,000 × $0.0000122 = **$0.44/mo**.
- Nightly job: assume 5 min awake × 30 = 9,000 s → **$0.11/mo**.
- Wake overhead (cold-boot wall time billed each wake — see Condition 3): ~30 wakes/mo × ~15 s = 450 s → **$0.006/mo** (negligible *if* boot is ~15s; not negligible if it's 60s — still under $0.03).
- **Active compute total ≈ $0.55/mo.** The design's "minutes of compute" claim is **correct** for compute.

Storage floor for the **same** persona — this is the part §8 waves at:
- §6 explicitly puts **the codebases** on the volume. One real software project with `.git`, `node_modules`/build artifacts, transcripts under `.claude/projects/`, and `data/` is rarely "a small volume." A conservative single-project user = **5GB**; a multi-repo power user = **20–50GB**. At $0.15/GB-mo:
  - 5GB → **$0.75/mo**, 20GB → **$3.00/mo**, 50GB → **$7.50/mo**.
- So for the headline persona the **storage floor ($0.75–$3) is 1.4×–5.5× the active compute ($0.55)**. The doc's framing ("minutes of compute + a small volume") inverts the real ratio: **storage is the dominant per-active-user cost, not compute.** That's fine for margin against a $5–10/mo sub, but the doc should say it plainly because it changes the pricing-tier design (you price on **GB**, not on vCPU-hours).

Egress for the same persona (Condition 4): material but not fatal — see Condition 4.

**Margin check at $7/mo sub, headline persona, 20GB:** $7 − ($0.55 compute + $3.00 storage + ~$0.50 egress + amortized CP per Condition 7) ≈ **$2.9 gross before control-plane amortization**. Survives, but it is **thinner than "tokens are the user's cost so our sub can be modest" implies** — the modesty is bounded by storage, not compute.

**Proposed fix:** Rewrite §8 with (a) explicit per-second/per-GB/per-GB-egress assumptions, (b) **stop vs suspend named** as the idle state with the cost consequence of each, (c) the storage-dominates-compute ratio stated, and (d) a worked margin example at the intended sub price for both a light and a heavy persona. Add to Phase 0's decision gate: "measure *stopped* and *suspended* idle billing on the real image; pick the idle state."

**Gate phase:** Phase 0 (the spike's "rough unit economics" gate) + the §8 spec edit before any Phase-3 pricing work.

---

#### Condition 2 (must-fix-in-design) — Dormant-storage handling is Phase-0/1-gating, not Phase 4; a free/dormant tier without it is a money pit.

**Why:** §6/§8/§10.3 acknowledge the floor but assign the mitigation (cold-tier / archive-and-detach) to **Phase 4 — Polish & scale**. That is backwards for the *free/dormant tier* the doc floats in §8. Every signup creates a **permanent volume**. A signup who makes one project and never returns = **perpetual storage cost vs $0 revenue**.

**Arithmetic at 1,000 dormant users** (the brief's scale):
- Assume dormant users skew small (one project, abandoned): **3GB avg** is generous-low; many will be 5–10GB because a single `git clone` of a real repo + a `node_modules` is already multiple GB.
  - 1,000 × 3GB × $0.15 = **$450/mo** perpetual.
  - 1,000 × 8GB × $0.15 = **$1,200/mo** perpetual.
- This is **pure loss** under a free tier, recurring monthly, growing with every dead signup. It dwarfs the entire existing control-plane ($67/mo, Condition 7). At 10,000 dormant signups it is **$4,500–$12,000/mo** of dead weight.
- Fly volumes also can't be trivially shrunk in place and have a **minimum size** (historically 1GB, often 3GB practical floor for a usable image), so even a zero-content dormant user costs the minimum-volume × $0.15 = **~$0.15–0.45/mo each** *even before* any codebase — 1,000 empty volumes = **$150–450/mo floor with literally zero usage.**

The archive-and-detach primitive (snapshot the volume to object storage at ~$0.02–0.023/GB-mo, **delete the live volume**) cuts the dormant cost by **~85%** (object storage vs block volume) and is the difference between a viable and a non-viable free tier. It is not "polish" — it is the economic precondition of the tier §8 wants to offer.

**Proposed fix:** Move "archive-and-detach dormant volumes to object storage after N days inactivity, restore-on-next-login" out of Phase 4 and make it a **Phase-1 design requirement and Phase-3 ship requirement** (i.e., it must exist before open signup with a free/dormant tier). If the free tier is deferred until the archive primitive ships, say *that* explicitly. Until then, **no free/dormant tier at GA** — invite-only or paid-only, mirroring `06-rollout-plan.md`'s M5 invite gate. Add a dormant-volume cost line to §8.

**Gate phase:** must-fix-in-design now; ships as a Phase-3 GA prerequisite (blocks the free-tier flip). The *decision* gates Phase 0 (it changes the storage-tier choice the spike validates).

---

#### Condition 3 (must-fix-in-implementation) — "Cold-start sub-second to a few seconds" is the **VM**, not **MC**; the economics and the UX promise must use MC-ready wall time on the chosen wake path.

**Why:** §4.2/§10.2 cite Fly's "sub-second to a few seconds" — that's machine resume. The "24/7 from a phone" promise and the first-request latency depend on **MC being ready**, which is a separate, larger number. I traced the startup sequence (`server.py:16486–16544`, `__main__`):

The following run **synchronously before `app.run()`** (i.e., before MC accepts the first request):
- `_check_port_conflict()` (13282) — single socket bind on a fresh VM, **fast** (the 15s wait is only the restart-re-exec path, not a cold boot — good).
- `_start_scheduler()`, `_start_hivemind_orchestrator()`, `_start_session_guardian()` — thread spawns, fast.
- `_install_builtin_skills()` (11696) → `_skills.install_builtins()` — **checksum scan of every builtin skill on every boot**.
- `_install_builtin_mcps()` (11718) — same pattern, writes `~/.claude.json` + per-project `.mcp.json`.
- staging cleanup, `_ensure_incognito_project()`, `_reconcile_pending_agent_log_entries()`, `_hm_reconcile_stale_on_startup()` — all synchronous.

Correctly **backgrounded** (not blocking `app.run`): `_startup_memory_maintenance` (incl. `_reconcile_unscribed_sessions`/backfill), `_session_label_enforcer_loop`, `_warmup_control_plane`, `_update_check_loop`. **Good** — the heavy reconcile is off the hot path. (The brief listed `_reconcile_unscribed_sessions` as a boot cost; it is, but it's daemon-threaded, so it doesn't block first-request readiness. Credit the design's existing architecture here.)

**Per-VM cost de-risk found during review:** `load_projects()` (1351) globs **one user's** projects dir, not a fleet — single-tenant-per-VM means it's a handful of JSON files, so cold-boot parse cost is **small per user**. This is a genuine point in the design's favour and should be stated.

Net: cold MC boot is plausibly **~3–15s** (Python interpreter start + Flask import graph + builtin checksum scans + synchronous reconciles), **on top of** the 1–3s VM resume → realistic first-request latency **~5–20s** on a *cold-boot* wake. That is **noticeably worse than the doc's "few seconds"** and, stacked with Seat 1's scheduler tick, a scheduled job could be **tens of seconds late** — tolerable for a nightly job, **jarring for an interactive phone tap.**

**The economics' escape hatch is resume-from-snapshot** (Fly `suspend`/resume restores the RAM image → MC is *already booted* → ~1–3s). **But the design must then assume the suspend path, which (a) re-raises Seat 2's key-in-snapshot-at-rest problem and (b) may carry the non-zero suspended-billing from Condition 1.** The doc cannot have it both ways: either **stop** (cheap idle, but pay full MC cold boot on every wake) or **suspend** (fast wake, but key-at-rest + possible idle charge).

**Proposed fix:** State explicitly which wake path the economics and the UX latency claim assume. If interactive UX requires suspend/resume, own the snapshot-key consequence (route to Seat 2) and the suspended-billing measurement (Condition 1). If cost requires stop, then (i) restate the latency claim as ~5–20s cold and add **pre-warm on app-foreground** (already floated in §10.2) as a *required* mitigation, not optional, and (ii) measure real MC-ready wall time in Phase 0 and put it on the decision gate. Recommend a **hybrid**: stop after long idle (cheap), but keep-warm + suspend during an active "session window" so interactive taps hit a resumed image.

**Gate phase:** Phase 0 (measure MC-ready time, not just VM resume) and Phase 2 (sleep/wake) for the path decision.

---

#### Condition 4 (must-fix-in-design) — Egress is ours and is non-trivial at scale; put a number on it and a per-VM egress cap in scope.

**Why:** §8 lists egress but treats it as minor. BYOK moves **token cost** to the user; it does **not** move the **bytes**. Every model API call's full request+response transits *our* VM's NIC outbound to Anthropic, plus the dashboard/SSE bytes to the phone. Large contexts (the design's own memory system injects read-floors, MEMORY.md, transcripts — context grows over a session) mean each turn can push **tens to hundreds of KB up and similar down**, and a long agent run is many turns.

**Arithmetic (assumptions explicit):** Suppose an active session does 50 model round-trips, avg 80KB up + 40KB down = 120KB/turn → ~6MB/session to Anthropic; dashboard/SSE another ~5–20MB for a chatty session. Call it **~25MB/active-session egress**. At 1 session/day × 30 = **750MB/mo/user** → 0.75GB × $0.02 = **$0.015/mo** — *negligible for a light user.* But a **heavy/hivemind/`/loop` user** running multi-hour agent loops with large contexts can do **gigabytes/day**: 5GB/day × 30 = 150GB × $0.02 = **$3/mo/user just in egress**, and an abusive loop (Condition 5) is unbounded. So egress is **negligible for the median, material for the tail, and uncapped for the adversary.** The doc's "minor" framing is right for the median and wrong for the tail it explicitly invites (hivemind, `/loop`).

**Proposed fix:** Add an egress line to §8 with a median and a tail number. Fold a **per-VM monthly egress cap** into the same primitive as Condition 5's compute cap (Fly exposes per-machine metrics; the orchestrator can read egress and throttle/suspend on breach). State that BYOK does **not** insulate us from bytes.

**Gate phase:** must-fix-in-design (§8 line); enforcement ships with Condition 5 in Phase 3.

---

#### Condition 5 (must-fix-in-design) — There is **no runaway-compute primitive**; "reuse the abuse posture" (§10.4) is not a mechanism, and the single-tenant-don't-inspect model makes it *harder*, not easier.

**Why:** §10.4 says "Need per-user compute caps / wake-budget, reusing the abuse-prevention posture in `04-abuse-prevention.md`." But `04-abuse-prevention.md` (per `06-rollout-plan.md` M4) is an **edge Worker** that caps **request rate, bandwidth, body size, path allowlist** — it governs the *ingress to a user's PC*. It has **no concept of in-VM compute time**, because in the existing product the compute is the *user's own electricity* — there was nothing to cap. In hosted mode the failure is new and inverted: a user fires one `/loop 5m`, backgrounds the phone, and the **in-VM scheduler/hivemind keeps the agent running on OUR compute, on THEIR tokens, indefinitely** — and crucially, **idle-detection (§4.3) will keep the VM AWAKE** the whole time because "running agent session" is true. So the very guard that prevents bad suspends (Seat 1) becomes the mechanism by which a runaway pins a 4GB machine awake 24/7: **$32/mo of our compute per runaway user, plus uncapped egress (Condition 4), against a flat sub.** Ten such users = the entire existing platform cost, from ten accounts.

The design deliberately **does not inspect the single-tenant VM** (that's the isolation posture, §4.1) — so you cannot cap by "looking inside." You must cap from the **orchestrator**, externally, using signals the VM already exposes.

**Proposed fix — concrete primitive (does not require inspecting agent logic):**
1. **Awake-budget per billing period.** Orchestrator already tracks awake-seconds for billing (Condition 1). Define a per-tier **awake-seconds/month budget** (e.g., free = 2h/mo awake, paid = N hours). On breach: force-suspend the VM and surface "compute budget exhausted — resume / upgrade" in the dashboard. This is purely external (the orchestrator owns start/suspend) and needs **no in-VM code**.
2. **Continuous-awake watchdog.** If a VM has been continuously awake > T (e.g., 2h) with no inbound client request (SSE), the orchestrator treats it as a probable runaway and force-suspends, regardless of "running agent" — i.e., **the awake-budget guard *overrides* the keep-awake guard.** The user's job is paused, not lost (fire-or-skip resumes on next wake, Seat 1).
3. **Egress cap (Condition 4)** as a co-equal trip wire on the same watchdog.
4. Expose these in §8's pricing tiers as the *enforcement* mechanism for "vCPU-hours cap" (already named in §8's tiered shape — make it real, not aspirational).

Without (1)+(2), the flat-sub pricing in §8 is **uncapped-cost-per-account**, which is the classic flat-rate-hosting blowup.

**Gate phase:** must-fix-in-design (the primitive must be specced); enforcement is a **Phase-3 ship requirement** (the doc already lists "per-user compute caps" in Phase 3 — this condition makes it concrete and ties the override-semantics to Seat 1's idle guard). **Soak-gate:** the awake-budget thresholds must be tuned before any free tier flips on.

---

#### Condition 6 (must-fix-in-design) — Internal daemon timers (GitHub sync, code sync, update-check) interact with idle-detection and quietly defeat suspend.

**Why:** The in-VM scheduler loop (`server.py:12620–12661`) runs **GitHub auto-sync** and **code-sync auto-fetch** — **outbound git fetches every 5 minutes per enabled project** — and there are additional always-on daemon threads (`_update_check_loop` every 6h, `_warmup_control_plane`, `_session_label_enforcer_loop`). §4.3 defines idle as "no SSE AND no running agent AND no due job in lookahead." **None of these internal timers are in that definition.** Consequences:
- If "due job in lookahead" is interpreted to include the 5-min git-sync cadence, a sync-enabled user's VM has a reason to wake/stay-awake **every 5 minutes, forever** → effectively **always-on**, which **defeats the entire sleep/wake cost premise** for that user. 5-min wakes × 288/day, even at 15s each, = 4,320s/day awake = **~$1.5/mo just in sync-wake compute**, plus the wake overhead dominates.
- If those timers are *excluded* from idle/lookahead, then GitHub/code sync **silently stops working** in hosted mode (the VM sleeps through every 5-min tick and only syncs opportunistically on the next unrelated wake) — a **behavior regression vs the always-on PC** the user is replacing, and one the design's §9 "all run inside the VM exactly as today" claim **explicitly denies.**

Either way, §9's "scheduler + hivemind loops — untouched … run inside the VM exactly as today" is **false for sync-enabled projects**: their behavior *does* change under suspend.

**Proposed fix:** Enumerate the internal periodic tasks (git sync, code sync, update-check, label-enforcer, CP warmup) and decide, per task, the hosted semantic: (a) demote to "fire opportunistically on next wake" (accept the latency, document the regression), or (b) **lift git/code sync into the external scheduler mirror** (the orchestrator wakes the VM for a sync the same way it wakes for a `next_run` job) — consistent with the §4.3 pattern but **extending the mirror beyond user schedules to internal cadences**, which the doc does not currently contemplate. Update §9's "exactly as today" claim to be honest about the sync behavior delta.

**Gate phase:** must-fix-in-design (idle definition + §9 honesty); validate in Phase 2 (the sleep/wake phase) that a sync-enabled project behaves as specified across suspend.

---

#### Condition 7 (must-fix-in-design) — The reused "~$67/mo at 50 users" figure is the **relay-only** control plane; it cannot absorb the new fleet plane and must not be cited as if it does.

**Why:** I verified the source. `06-rollout-plan.md` §13.1 itemizes the $67/mo: **Cloud Run ($10), Firestore ($2), KMS ($0.10), Memorystore Redis ($35), Cloud Logging ($5), Workers/KV/DO ($5), bandwidth $0 (CF free tier), Firebase $0, domains ($10)** = $67. That stack is purely **auth + relay + attestation-log + rate-limit** for **PCs the users run themselves**. It contains **zero** compute-hosting cost because in that design *there is no compute to host.* The same doc's §13.2 even recommends **dropping Memorystore to hit ~$32/mo** — i.e., the $67 is already a loose upper bound on a *relay-only* plane.

§8 cites this as evidence the control plane "already runs ~$67/mo … and scale-to-zero keeps this low." That is a **category error**: the new design adds (a) **per-VM compute** (Conditions 1/5 — the dominant new variable cost), (b) a **fleet orchestrator** (a new always-on control service — more Cloud Run/compute than the relay stub), (c) **volume storage** (Conditions 2 — potentially **$450–$12,000/mo** at the dormant scales above, **alone exceeding the entire $67 by 7×–180×**), and (d) **our-side egress** (Condition 4). The $67 covers **none** of these. Citing it next to the new model implies a cost continuity that does not exist.

**Proposed fix:** In §8, **stop reusing the $67 as a proxy for hosted cost.** Keep it *only* as the **amortized control-plane (auth/relay) line** and add the **new** lines explicitly: per-VM compute, volume floor (incl. dormant), egress, and the incremental orchestrator/fleet-management cost (which is itself larger than the relay stub — the orchestrator polling/managing N machines is more than a relay). Present hosted cost as **$67-ish fixed + (per-active-user compute) + (per-user storage, dominant) + (egress) + (incremental orchestrator)**. The headline takeaway should be that **storage, not the $67 control plane, is the cost center.**

**Gate phase:** must-fix-in-design (§8 edit) before Phase-3 pricing.

---

### Ratifications

- **Sleep/wake genuinely zeroes the dominant *compute* variable when idle** — verified that the in-VM scheduler **checks-then-waits** (`server.py:12534` body runs before `_scheduler_stop.wait(30)` at 12712), so a cold-thread wake fires overdue jobs on the **first** iteration with **no 30s pre-penalty**. The active-compute math (~$0.55/mo for the headline persona) is real; the locked decision earns its keep on compute.
- **BYOK imposes no per-request key-handling cost.** Verified the dispatch spawn inherits the parent env (`agent_runtime.py:1752 env = os.environ.copy()` → `1765 env=env`); the CLI picks up `ANTHROPIC_API_KEY` from the process environment. The cost model correctly assumes **one inject-at-wake**, not a per-turn vault round-trip — no hot-path cost, no per-call orchestrator dependency. (Security consequences of env-resident key are Seat 2's.)
- **Single-tenant-per-VM bounds the boot cost per user.** `load_projects()` (`server.py:1351`) globs one user's `DATA_DIR`, not a fleet — cold-boot JSON parse is small per user. This is a real economic advantage of the one-VM-per-user choice and should be stated in §6/§8.
- **The heavy reconcile is already off the cold-start hot path.** `_startup_memory_maintenance` (incl. unscribed-session reconcile + agent-log backfill) is daemon-threaded (`server.py:16524`), so it does **not** block first-request readiness — the architecture already did the right thing for wake latency. Preserve this; do not let any future hosted-mode boot step move heavy work back onto the synchronous pre-`app.run()` path.
- **Phase 0 spike as the unit-economics decision gate** is the right discipline — the conditions above are mostly "make the spike measure these specific things and write the numbers into §8," which is exactly what a Phase-0 gate is for.

---

### Out-of-scope but flagged

- **(→ Seat 2) Snapshot-key-at-rest is also a cost coupling.** Condition 3's "suspend for fast wake" path is the same RAM-snapshot that holds `ANTHROPIC_API_KEY` at rest — the **cost-vs-UX** choice (stop vs suspend) and the **security** choice (key in snapshot) are the *same* decision. Whatever Seat 2 rules on snapshot encryption/revoke directly constrains which wake path the economics (Condition 1/3) may assume. These must be resolved jointly, not independently.
- **(→ Seat 1) The awake-budget override (Condition 5.2) intersects idle-detection.** My proposed "force-suspend a continuously-awake runaway regardless of running-agent" deliberately **overrides** Seat 1's keep-awake-while-agent-running guard. Seat 1 must confirm a force-suspend mid-runaway degrades to fire-or-skip cleanly (the user's runaway is *paused*, and on next wake the catch-up logic does the right thing) rather than corrupting an in-flight turn.
- **(→ Seat 4) Self-hostable open core caps pricing power, which caps margin tolerance.** If (per Seat 4) the moat is "convenience, not moat" because a user can self-host the open core on their own Fly account, then the subscription **cannot** be priced aggressively — which makes the storage floor (Condition 2) and runaway cost (Condition 5) **more** dangerous, because there's less margin to absorb them. The thinner the pricing power, the more load-bearing Conditions 2 and 5 become.
- **(→ Seat 4 / general) Volume minimum-size + Fly region pricing.** Fly volumes have a practical minimum and region-dependent pricing; a multi-region story (Phase 4) multiplies the storage floor per region a user is pinned to. Out of my lane to design, but it compounds Condition 2 and should be noted wherever multi-region is specced.
- **DR/snapshot cadence (§10.8) is an unpriced storage line.** Periodic volume snapshots for backup (§6) are themselves billable storage (snapshot $/GB-mo × retention count). Not modeled anywhere in §8. Minor relative to the live-volume floor, but it should appear as a line item so the backup cadence (§10.8) is chosen with its cost visible.
