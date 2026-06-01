# Hosted Clayrune — Cloud Compute Platform — Committee Review Brief

> Status: **OPEN for review**. Authored 2026-06-01 by Vector.
> Design under review: `docs/HOSTED_CLOUD_PLATFORM_DESIGN.md` (DRAFT v1).
> Pattern mirrors the Skills-Curation committee discipline
> (`docs/SKILLS_CURATION_PHASE4_V2_COMMITTEE_BRIEF.md`) — parallel seats,
> code-grounded adversarial review, conditions classified
> design / implementation / soak-gate, strictest-seat synthesis.

---

## 1. Background — what this is and why review now

Clayrune today is **bring-your-own-machine**: Mission Control + the Claude
Code CLI run on the *user's own PC*; the cloud is only a control plane
(Firebase auth + Firestore) plus a Cloudflare relay so a phone can reach
that PC behind NAT (`docs/remote-access/01-architecture.md`).

The design under review proposes a new product mode: **hosted compute** —
we run the full stack for the user in a cloud microVM, one per user,
suspended when idle and woken on demand, so the user needs **no PC at all**.

**Two decisions are LOCKED (Ron, 2026-06-01) — do NOT relitigate them:**
1. **BYOK** — the user supplies their own Anthropic API key; Anthropic bills
   them directly; we never mark up tokens. Our revenue is the hosting fee.
2. **Sleep/wake per-user microVM** — each user's instance suspends when idle
   and wakes on demand. Zeroes compute cost when idle; storage is the floor.

A seat may surface a *consequence* of a locked decision (e.g. "sleep/wake
creates a key-at-rest problem"), but may not propose replacing BYOK with a
token-margin model or replacing sleep/wake with always-on. Work within the
locked frame.

**Why review now:** This is the project's standing discipline (Memory System
§3.A.MID; Skills Curation v2 committee gate) — **no compute-plane code lands
until this design clears committee.** The design is at the spec stage; this
is the cheapest point to catch a structural flaw.

**Document under review:** `docs/HOSTED_CLOUD_PLATFORM_DESIGN.md` — read it
end to end before assessing. Particularly §4 (microVM lifecycle + the
scheduler bridge), §5 (BYOK key handling), §6 (persistence), §7 (networking /
mc-tunnel deprecation), §8 (revenue/cost), §9 (what changes in code), §10
(risks), §11 (phases).

**Also read for grounding (do not re-review these):**
- `docs/remote-access/01-architecture.md` — the platform this extends.
- `docs/remote-access/03-control-plane-api.md` — the control-plane contract.
- `docs/remote-access/07-licensing.md` — the moat/licensing story to restate.
- `docs/remote-access/04-abuse-prevention.md` — abuse posture to reuse.
- `docs/remote-access/02-attestation-protocol.md` — the EXISTING enrollment
  is attestation-of-a-user-PC; hosted mode has no PC. Reconcile.
- `control_plane/api_spec.yaml` (35KB) + `control_plane/README.md` — the
  control plane that **already exists**; the new hosted plane must fit or
  consciously diverge from it.

---

## 2. Verified code anchors (use these; they're confirmed real)

The design's load-bearing code claims were spot-checked before this brief.
All confirmed present:

- **Single-instance invariant:** `_check_port_conflict()` at `server.py:13282`
  (called at `16488`). Binds `0.0.0.0:5199`; second instance fatal.
- **Scheduler loop:** `_scheduler_loop()` at `server.py:12534`; wait at
  `12712` (`_scheduler_stop.wait(30)` — 30s cadence). **Verify the loop's
  check-vs-wait ORDER** (does it wait 30s *then* check, or check-then-wait?
  — bears directly on cold-wake job latency).
- **Hivemind loop:** `_hivemind_orchestrator_loop()` at `server.py:10342`.
- **BYOK env-inheritance:** `ANTHROPIC_API_KEY` appears in `agent_runtime.py`
  ONLY in provider health-check / error-explain paths (lines ~3310, 3444,
  3566, 3716) — **not** in the Claude dispatch spawn. This is *consistent*
  with "the CLI inherits the key from the process env," but a seat must
  CONFIRM the dispatch subprocess does not pass a curated `env=` dict that
  would strip the inherited key. The "zero dispatch change" claim rests
  entirely on this.
- **Client is domain-agnostic:** `mc_remote/config.py:26` —
  `PLATFORM_DOMAIN = os.environ.get("MC_REMOTE_PLATFORM_DOMAIN", "clayrune.io")`,
  `control_plane_base_url()` at `:37`. No hardcoded `API_BASE` in the static
  frontend. Pointing the app at a hosted instance is config, not a rebuild.
- **Existing control plane:** `control_plane/` has `api_spec.yaml`,
  `Dockerfile`, `app/`, `tests/`, enrollment + attestation demos. It is built
  around attesting a user's machine and minting tunnel tokens
  (`mc_tunnel/src/main.rs` POSTs attestations to `api.PLATFORM_DOMAIN/v1/attest`).

**Rule: verify before you rely.** If your assessment hinges on a code claim,
open the file and confirm it. Cite `file:line`.

---

## 3. The four seats

Per the design's §12. **No cross-seat communication during review.** Each
seat reads the design end to end and grounds findings in code + the
remote-access docs.

### Seat 1 — Concurrency & lifecycle (sleep/wake vs. the in-process scheduler)

**Scope:** §4 (microVM lifecycle), §4.3 (external scheduler mirror), the
in-VM scheduler + hivemind daemon loops, suspend/resume of live agent
sessions and SSE.

**Core questions / failure paths to pressure-test:**
- **Cold-wake job latency.** `_scheduler_loop` does `_scheduler_stop.wait(30)`.
  Determine the loop ORDER: if it waits 30s *before* the first check after a
  cold start, every wake-for-a-due-job pays up to 30s before firing. Does the
  scheduler-mirror "wake shortly before due" (§4.3) actually land the job on
  time, or does the 30s tick + MC boot time stack into minutes of skew?
- **Fire-or-skip drops missed runs.** §4.3 leans on today's fire-or-skip:
  on resume the loop fires *one* overdue instance and recomputes `next_run`.
  A VM asleep across 3 nightly windows fires the job **once**, not three
  times. Is that the intended hosted semantic, or silent loss of runs a
  user expects? Today on an always-on PC the same job fires nightly.
- **In-flight agent across suspend.** A microVM *suspend* snapshots RAM +
  process state + open sockets. A Claude CLI subprocess mid-turn (an open
  HTTPS request to Anthropic) at snapshot time — does it resume cleanly or
  error on a dead socket? §4.3 treats a mid-flight hivemind workstream as a
  "scheduled wake," but the deeper question is whether suspend can even
  happen mid-agent-turn (idle detection should forbid it — verify the guard).
- **Idle detection correctness.** §4.3 idle = no SSE **AND** no running agent
  **AND** no due job in lookahead. Mobile SSE is known-flaky (Doze parks
  sockets — see memory). If the phone backgrounds mid-session, SSE drops; is
  "no running agent" a reliable enough second guard to prevent suspending a
  live headless run? What measures "running agent" and "next_run" for the
  orchestrator — §9's "tiny in-VM agent or reuse health endpoints"? Confirm
  an endpoint actually exposes running-session + next_run today
  (`/api/system/status`, `/api/schedules`, `/api/project/<id>/agent/status`).
- **Mirror sync race.** §4.3 syncs `next_run` "from the VM on each sleep."
  A schedule edited in the last awake moment then immediate suspend — is the
  sync ordered before the suspend completes? What if sync fails? (Doc says
  degrades to fire-late — confirm that's actually the worst case.)
- **suspend ≠ kill.** Note where the design conflates VM suspend (state
  preserved) with process kill (the hard-kill gap the memory system's
  `_reconcile_unscribed_sessions` exists to close). Which lifecycle
  transitions are snapshot-clean and which risk the hard-kill path?

**Block authority:** if sleep/wake can drop a scheduled job that fires today,
corrupt or lose an in-flight agent/hivemind turn, or make scheduled-job
timing so unreliable the feature is worse than the always-on PC it replaces.

### Seat 2 — Security & custody (BYOK key handling + microVM isolation)

**Scope:** §5 (key lifecycle), §4.1 (isolation), §10.1 (custody liability),
§10.5 (egress).

**Core questions / failure paths:**
- **Confirm the env-inheritance claim (load-bearing).** §5 says BYOK = "launch
  the MC process with the user's key in the env; no dispatch change." Open the
  Claude dispatch spawn and confirm it inherits the parent env (no `env=` that
  strips `ANTHROPIC_API_KEY`). If dispatch passes a curated env, the whole
  "nearly free" claim is false and §9's "reused unchanged" list is wrong.
- **Where does MC persist provider keys?** §5.3 promises "no plaintext at rest
  in the VM" via tmpfs/secret-mount injection. But MC has a Settings → Agent
  Providers surface (per `agent_runtime.py` error strings) and writes
  `data/settings.json` / `data/config.json` to the **persistent volume**. If a
  key set via Settings lands in settings.json on the volume, the no-plaintext
  promise is broken. Trace where MC reads/writes provider keys and whether the
  injected env key can leak onto the volume.
- **Key at rest in the suspend snapshot (cross-seat with Seat 1).** A suspended
  microVM = a RAM snapshot persisted to disk. The injected `ANTHROPIC_API_KEY`
  lives in process memory → it is now in the snapshot blob at rest. "Inject at
  wake, never persisted" (§10.1) is undermined by the sleep/wake mechanism
  itself. Is the snapshot encrypted at rest by the provider? Does **revoke**
  (§5.4 "clear vault + force restart") destroy the snapshot, or can a stale
  snapshot resurrect a revoked key?
- **Key in logs / transcripts.** MC logs agent stdout, stores transcripts,
  runs Scribe over them. Can the key ever surface in a log line, a transcript
  jsonl, a scribe summary, or a crash dump (CLI echoing env, an error printing
  argv/env)? The agent_runtime explain-paths reference keys but don't print
  them — verify nothing else does.
- **Arbitrary agent code + the key.** The agent has a Bash tool and outbound
  internet (§10.5). It can read its own env → read the key → POST it anywhere.
  It is the user's *own* key, so the blast radius is the user's own account —
  but state the trust model explicitly and confirm one user's VM cannot reach
  another's (one-VM-per-user isolation; verify no shared control-plane secret
  is reachable from inside a user VM).
- **Custody liability delta.** How is holding a user's Anthropic API key
  materially different (legally/operationally) from the existing platform
  already holding CF service tokens? Is the incident plan / ToS hook real, or
  hand-waved?

**Block authority:** if the env-inheritance claim is false; if keys end up at
rest on the volume, in a suspend snapshot a revoke can't reach, or in logs; or
if isolation is insufficient for running arbitrary agent code with a live key.

### Seat 3 — Cost & ops (unit economics, storage floor, cold-start, abuse)

**Scope:** §8 (revenue/cost), §6 (storage), §4.2 (provider), §10.2/3/4/6.

**Core questions / failure paths:**
- **Pressure-test the headline unit economics.** §8 claims a user running a
  20-min daily session + one nightly job costs "minutes of compute + a small
  volume." Put real numbers on it: Fly Machine awake billing (vCPU/RAM-sec),
  *suspended* billing (is a stopped/suspended machine truly $0 or is there a
  reservation charge?), volume $/GB-mo, egress $/GB. Does the margin survive a
  modest subscription, or is it thinner than the doc implies?
- **Storage floor at scale.** Every signup = a permanent volume (§6 puts the
  user's **codebases** on it — potentially many GB each). A user who signs up,
  makes one project, and never returns is a perpetual storage cost against $0
  revenue if there's a free tier. Estimate the floor at, say, 1,000 dormant
  users. Is the "free/dormant tier" (§8) viable, or is the mitigation
  (archive-and-detach) actually **Phase-0-blocking** rather than Phase 4?
- **Cold-start includes MC boot, not just VM resume.** §4.2/§10.2 cite
  "sub-second to a few seconds" for VM wake — but that's the VM. MC itself
  must boot: Flask init, `load_projects()`, `_reconcile_unscribed_sessions`,
  scheduler/hivemind thread start, `_check_port_conflict`, builtin-skills
  checksum scan. Measure / estimate MC's cold-boot wall time. If MC takes tens
  of seconds to be ready, real wake latency ≫ the doc's claim and Seat 1's
  scheduler-wake compounds it. (Resume-from-snapshot may avoid re-boot — but
  then see Seat 2's snapshot-key problem. State which wake path the economics
  assume.)
- **Egress is ours even though tokens are theirs.** BYOK moves *token cost* to
  the user, but the *bytes* of every model API call still egress our VM. Large
  contexts = large egress. Is egress a material cost line at scale?
- **Runaway compute (§10.4).** A user's `/loop`, runaway hivemind, or endless
  agent burns *our* compute on *their* tokens. "Reuse the abuse posture" is
  not a mechanism. How is a runaway detected and capped inside a single-tenant
  VM we deliberately don't inspect? Propose a concrete wake-budget / compute-cap
  primitive or flag its absence as a gap.
- **Provider lock-in vs. margin (§10.6).** Fly's suspend/resume + proxy-wake +
  volume API are the design's spine (§4.2). Is the §9 "orchestrator abstracts
  the VM lifecycle" claim realistic, or does Fly's wake-on-request proxy leak
  so deeply that a later move to Firecracker-on-bare-metal is a rewrite?
- **The reused $67/mo figure.** §8 cites "~$67/mo at 50 users" from
  `06-rollout-plan.md`. Confirm that figure is for the EXISTING relay-only
  control plane and whether it can possibly still hold once the fleet
  orchestrator + key vault + per-VM compute are added.

**Block authority:** if unit economics are upside-down at realistic scale, if
the storage floor makes any free/dormant tier a money pit without a mitigation
that must ship in Phase 0, or if cold-start (VM + MC boot) breaks the
"24/7 from a phone" UX promise.

### Seat 4 — Platform & licensing (moat restatement, mc-tunnel, existing plane)

**Scope:** §1, §7, §9, §10.7, §10.9; reconciliation with `control_plane/` and
the remote-access docs.

**Core questions / failure paths:**
- **Reconcile with the EXISTING control plane.** `control_plane/api_spec.yaml`
  + the attestation protocol (`02-attestation-protocol.md`) are built around
  *attesting a user's machine* and minting tunnel tokens. Hosted mode has **no
  user machine to attest.** Does the hosted provisioning/vault/scheduler-mirror
  surface extend the existing api_spec cleanly, or does it require a parallel
  enrollment path that bypasses attestation? Is enrollment-by-attestation vs.
  enrollment-by-provisioning a fork the design hasn't acknowledged?
- **Is the restated moat actually defensible?** Today's moat (`07-licensing.md`)
  is "the closed Rust tunnel binary binds you to our platform"
  (open MC core + closed mc-tunnel — see `feedback_no_paid_code_signing`).
  §7 drops per-VM mc-tunnel for hosted and asserts the moat *shifts* to "we run
  the whole stack." Pressure-test: the MC core is open-core and the
  orchestration is, concretely, Fly Machines API calls + a KMS-backed key vault
  + an ingress proxy. What stops a competitor (or the user) from self-hosting
  the open core on their own Fly account and replicating the orchestration in a
  weekend? Is "we run it for you" a *moat* or merely a *convenience*? If it's
  convenience, say so — that changes the §8 pricing-power story.
- **Open/closed boundary cleanliness.** §9 adds control-plane components (all
  closed/proprietary) and a "hosted mode" flag in the open MC core (§9
  modified-lightly). Does any hosted-specific logic leak into the open core
  beyond a benign flag? Where exactly is the open/closed line drawn, and does
  dropping mc-tunnel for hosted instances weaken the open-core licensing story
  for the *existing* BYO-machine product that still relies on it?
- **iOS unblock (§10.9).** The doc claims cloud-side compute finally makes iOS
  a pure client. Is that real — does removing NAT/tunnel actually remove the
  native-bridge requirement, or does CF Access service-token onboarding still
  need native code (per the existing mobile machinery)? Don't design iOS, but
  validate or puncture the "newly unblocked" claim.
- **Onboarding fork.** §7 says the app is domain-agnostic so "pointing it at a
  hosted instance is config." True for the data path — but the *onboarding* UX
  diverges (existing = "enroll your PC via attestation"; hosted = "we provision
  your VM, paste your key"). Is that two-onboarding-flows reality captured, or
  glossed?

**Block authority:** if the hosted plane conflicts irreconcilably with the
existing attestation/tunnel control plane, if the moat restatement is hollow
(open core is trivially self-hostable so "we run it" is convenience not moat),
or if the open/closed boundary is muddied in a way that erodes the existing
product's licensing.

---

## 4. Review rules

- **Read `docs/HOSTED_CLOUD_PLATFORM_DESIGN.md` end to end.** Don't review off
  this brief alone.
- **Ground in code + the remote-access docs.** Verify any code claim your
  assessment relies on; cite `file:line`.
- **Work within the two LOCKED decisions** (BYOK, sleep/wake). Surface their
  consequences; do not propose replacing them.
- **No cross-seat communication during review.**
- **Specific failure paths over abstract concerns.** "Under condition X, step Y
  produces outcome Z" beats "this seems risky."
- **Evidence over assertion.** Line references in the design, precedent in the
  remote-access docs / existing control_plane, or confirmed code.

---

## 5. Required output (per seat)

Write a single markdown file in this shape:

```markdown
## Seat <N> — <name> — <DECISION>

**Decision:** RATIFY / RATIFY-WITH-CONDITIONS / BLOCK

**Summary (1-2 sentences):** <high-level assessment>

### Blockers (if any)
1. <Specific issue with line/code reference. What fails, under what conditions.>
   - Fix required: <concrete change to the design before compute-plane code lands>

### Conditions (RATIFY-WITH-CONDITIONS only)
Numbered, each with:
- **Condition N (must-fix-in-design / must-fix-in-implementation / soak-gate):**
- **Why:** <failure mode this prevents, concretely>
- **Proposed fix:** <specific change>
- **Gate phase:** which build phase (§11: Phase 0/1/2/3/4) this gates

### Ratifications
- <Things the design got right, worth preserving across future changes.>

### Out-of-scope but flagged
- <Concerns adjacent to but outside this seat's focus, for the synthesizer to route.>
```

Classification meaning:
- **must-fix-in-design** — blocks the design moving to v1.1 (a spec edit).
- **must-fix-in-implementation** — blocks a specific build phase, not the design.
- **soak-gate** — blocks a default-on flip / GA, not the initial build.

Write to: `docs/_committee/HOSTED_CLOUD_seat<N>_<name>.md`
(`<name>` = `lifecycle` / `custody` / `cost` / `platform` for seats 1–4.)

---

## 6. Synthesis (post-review)

After all four seats report:
1. Append all four assessments + a synthesis to the design doc under
   `## Committee review (2026-06-01) — <overall decision>`.
2. Overall decision = **strictest** of the four (one BLOCK ⇒ overall BLOCK).
3. Group conditions into design / implementation / soak-gate buckets.
4. If RATIFY(-WITH-CONDITIONS): address must-fix-in-design conditions by
   editing the design → v1.1; re-mark status.
5. If BLOCK: address blockers, revise, re-launch the committee.
