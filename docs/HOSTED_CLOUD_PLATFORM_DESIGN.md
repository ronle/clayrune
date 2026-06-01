# Hosted Clayrune — Cloud Compute Platform Design

**Status:** DRAFT v1 (2026-06-01)
**Author:** Vector (with Ron)
**Companion docs:** `docs/remote-access/01-architecture.md` (the existing
remote-access platform this builds on), `docs/remote-access/03-control-plane-api.md`,
`docs/remote-access/07-licensing.md`.

---

## TL;DR

Today Clayrune is **bring-your-own-machine**: the Mission Control server +
Claude Code CLI run on the *user's own PC*, and the cloud only provides a
control plane (Firebase auth + Firestore) and a Cloudflare relay so a phone
can reach that PC behind NAT. See `docs/remote-access/01-architecture.md`.

This document designs the next product mode: **hosted compute**. We run the
full Clayrune stack for the user in the cloud — one isolated microVM per user,
spun up and down on demand — so the user needs *no desktop at all*. The phone
(or any browser) is the only device they touch.

We are the **facilitator/invoker**: we own the orchestration, the fleet, the
ingress, and the lifecycle. That orchestration layer is the product and the
revenue stream.

Two decisions are **locked** (Ron, 2026-06-01):

1. **Billing = Bring-Your-Own-Key (BYOK).** The user supplies their own
   Anthropic API key. Anthropic bills *them* for tokens directly; we never
   mark up or front model cost. Our revenue is the **hosting/facilitation
   fee** (managed instance + storage + orchestration), not a token margin.
2. **Idle behavior = sleep/wake per-user microVM.** Each user's instance is a
   microVM that suspends when idle and wakes on demand (incoming request or a
   due scheduled job). This zeroes compute cost when idle; storage is the only
   always-on floor.

The headline finding from grounding this in the codebase: **almost none of the
existing server code has to change.** The mobile app is already a thin client
pointed at a remote server; the server already runs the CLI by inheriting
`ANTHROPIC_API_KEY` from its environment; the single-instance invariant maps
*perfectly* onto one-VM-per-user. The real work is a new **control-plane
compute-hosting plane** (provisioning, lifecycle, key custody, ingress) bolted
onto the control plane that already exists.

---

## 1. How this differs from the existing remote-access platform

The existing platform (`docs/remote-access/`) and this one share a control
plane but differ on **where the compute lives**:

| | Existing: remote-access | This: hosted compute |
|---|---|---|
| MC server + Claude CLI run on… | the user's own PC | a cloud microVM we run |
| Cloud's job | auth + relay (tunnel broker) | auth + relay **+ run the compute** |
| User needs a PC? | yes (always-on) | **no** |
| NAT traversal needed? | yes (mc-tunnel + cloudflared) | **no** (VM is routable) |
| Who pays for compute? | user's electricity | us (minimized by sleep/wake) |
| Who pays for tokens? | user (their Claude auth) | user (BYOK) — unchanged |
| Our revenue | $0 (it's their PC) | **hosting subscription** |

So this is **additive**: it reuses the control plane, the Firebase auth, the
Cloudflare Access edge, and the device-enrollment concepts. It *replaces* the
"reach the user's home PC over a tunnel" data path with "route to the user's
microVM in our fleet." The mc-tunnel NAT-traversal role disappears (see §7).

---

## 2. Locked decisions (Ron, 2026-06-01)

- **BYOK.** User supplies their Anthropic API key. Implications threaded
  through §5 (custody) and §8 (revenue).
- **Sleep/wake per-user microVM.** Implications threaded through §4
  (lifecycle) and the scheduler problem in §4.3.

---

## 3. Topology

```
                         ┌─────────────────────────────────────────┐
   Phone / Browser  ───► │  Cloudflare edge (TLS, Access auth gate) │
                         └───────────────────┬─────────────────────┘
                                             │  routes <user>.clayrune.io
                                             ▼
                    ┌──────────────────────────────────────────────┐
                    │  CONTROL PLANE  (we run this — the product)    │
                    │  • Firebase auth / accounts / subscription     │
                    │  • Key vault (encrypted BYOK keys)             │
                    │  • Fleet orchestrator (provision/wake/sleep)   │
                    │  • External scheduler mirror (wake-on-cron)    │
                    │  • Ingress router → user's microVM             │
                    └───────────────────┬────────────────────────────┘
                                        │  wake / route / inject key
                                        ▼
        ┌───────────────────────────────────────────────────────────┐
        │  COMPUTE PLANE  —  fleet of per-user microVMs               │
        │                                                             │
        │   ┌── user A microVM ──────────┐   ┌── user B microVM ──┐   │
        │   │  MC Flask server (:5199)   │   │  (suspended)        │   │
        │   │  Claude Code CLI            │   │                     │   │
        │   │  scheduler + hivemind loops │   │   persistent vol    │   │
        │   │  ── persistent volume ──    │   │   detached/retained │   │
        │   │   data/  +  project repos   │   └─────────────────────┘   │
        │   └─────────────────────────────┘                            │
        └───────────────────────────────────────────────────────────┘
```

Three planes:

- **Edge (Cloudflare):** TLS termination + Access auth gate. Reused from the
  existing design largely as-is.
- **Control plane (we own — the moat):** accounts, subscription/billing,
  BYOK key vault, the **fleet orchestrator** (new), the **external scheduler
  mirror** (new), and the ingress router that maps an authenticated request to
  the right microVM and wakes it if asleep.
- **Compute plane (new):** a fleet of microVMs, **one per user**, each running
  the unmodified MC server + Claude CLI against a per-user **persistent
  volume**. Suspended when idle.

---

## 4. The microVM and its lifecycle (the core of the sleep/wake decision)

### 4.1 Why one microVM per user (not multi-tenant)

The server enforces a **single-instance invariant**: it binds `0.0.0.0:5199`
at boot and a second instance on the same port is fatal by exit code
(`server.py:13220–13343`, `_check_port_conflict`). The whole codebase assumes
it *owns* its `DATA_ROOT`, its projects dir, its scheduler, its memory files.

Rather than fight that with a multi-tenant refactor (huge, risky, touches
every subsystem), we **lean into it**: each user gets their own instance in
their own microVM. The invariant becomes a *feature* — strong isolation,
no blast radius across users, and **zero server code changes** for tenancy.
MicroVMs (Firecracker-class) give us hardware-grade isolation, which is the
right posture for running arbitrary agent code + holding a user's API key.

### 4.2 Provider / mechanism

Primary candidate: **Fly Machines** (Firecracker microVMs with
suspend/resume, scale-to-zero, wake-on-request via the Fly proxy, and
attachable persistent volumes). The lifecycle primitives we need map directly:

- **suspend/stop on idle** → compute billing stops; volume persists.
- **wake on incoming request** → the proxy starts the machine and holds the
  request; cold-start is sub-second to a few seconds.
- **persistent volume** → survives suspend/resume and restarts.

Alternatives to evaluate against the same criteria (suspend/resume latency,
per-second billing, volume durability, microVM isolation, egress cost):
AWS Firecracker via a custom orchestrator, Firecracker-on-bare-metal
(Hetzner/Equinix) for margin, or a container runtime with strong isolation
(gVisor/Kata) if a microVM provider proves too costly. **Decision deferred to
a Phase-0 spike** that measures cold-start + cost on real workloads.

### 4.3 The scheduler problem (and why it's solvable cleanly)

This is the one place sleep/wake interacts non-trivially with existing code.

**Finding:** the scheduler and hivemind are **in-process daemon threads** with
local wait loops — scheduler every 30s (`server.py:_scheduler_loop`,
`_scheduler_stop.wait(30)`), hivemind every 10s
(`_hivemind_orchestrator_loop`). They MUST be alive in the process for a job
to fire. There is **no external trigger** and **no missed-job queue** — on
resume, the first loop iteration compares `now >= next_run` and fires any
overdue schedule (fire-or-skip), then recomputes `next_run`.

So a suspended VM fires nothing — but the moment it wakes, the existing
catch-up logic runs any job that came due while it slept. That gives us a
clean bridge that requires **no change to the in-VM scheduler**:

> **External scheduler mirror.** The control plane keeps a lightweight mirror
> of each user's schedule (just the `next_run` timestamps, synced from the VM
> on each sleep). A single control-plane cron wakes the user's microVM shortly
> *before* a job is due. On wake, the in-VM loop sees the overdue job and fires
> it exactly as it does today. After the job completes and the idle window
> passes, the VM suspends again.

Properties:
- In-VM scheduler is untouched (best-effort posture preserved).
- The mirror only needs `next_run` per schedule — no logic duplication.
- Worst case (mirror stale / wake missed) degrades to "job fires late on next
  wake," which is exactly today's fire-or-skip behavior — never a hard break.

**Wake triggers (any of):** (a) authenticated incoming request from the
client, (b) control-plane scheduler mirror firing for a due job, (c) a hivemind
workstream that was mid-flight at suspend (treated like a scheduled wake).

**Sleep trigger:** idle = no active client connection (SSE) **AND** no running
agent session **AND** no scheduled job due within the lookahead window. A short
cooldown after the last activity avoids thrashing.

---

## 5. BYOK key handling

**Why it's nearly free to implement:** the server does **not** manage Claude
credentials. When it spawns the CLI it sets no explicit `env=`; the CLI simply
inherits `ANTHROPIC_API_KEY` from the process environment
(`agent_runtime.py` / `server.py` dispatch, ~`6897–6912`). So BYOK reduces to
**"launch the MC process in the microVM with the user's key in the env."**
No dispatch-path code change.

**Lifecycle:**
1. **Capture.** User pastes their Anthropic API key in the hosted onboarding
   UI (control plane), over TLS.
2. **Store.** Encrypted at rest in a control-plane key vault (KMS-backed —
   the existing design already uses Cloud KMS; reuse it). Never written to the
   VM image or to any logs.
3. **Inject at wake.** The orchestrator injects the key as `ANTHROPIC_API_KEY`
   into the microVM's MC process environment at start/resume. Prefer a
   tmpfs/secret-mount or per-boot env injection over baking it into the
   persistent volume, so the key is not at rest inside the user's disk image.
4. **Rotate / revoke.** Key is updatable from the UI; rotation just re-injects
   on next wake. Revoke = clear vault entry + force a restart so the live
   process drops it.

**Trust/liability surface (call out loudly):** holding a user's API key makes
us a credential custodian. Mitigations: KMS encryption, per-user isolation
(microVM, not shared process), no plaintext at rest in the VM, no key in logs,
and a clear ToS on custody. **This is the single biggest non-technical risk of
BYOK** — see §10.

**ToS note:** BYOK-with-an-API-key is the clean path. The user pays Anthropic
directly under their own account; we are infrastructure. (This sidesteps the
"running a personal Claude *subscription* through a hosted service" gray area
that an OAuth-subscription model would raise.)

---

## 6. Persistence — the per-user volume

The microVM's persistent volume must hold everything the server treats as
durable state. From `DATA_ROOT` (`server.py:26–41`) plus project workspaces:

- `data/projects/*.json` — project records + backlog (and the sidecar
  `_agent_log.json`, `_router_stats.json`, `_scribe_stats.json`,
  `_skill_stats.json` files — **load-bearing exclusion rules apply**, see
  CLAUDE.md DATA_DIR pollution note).
- `data/schedules.json`, `data/settings.json`, `data/config.json`.
- `data/memory/`, `data/uploads/`, `data/hiveminds/`, `data/skills/`,
  `data/mcp/`, session/label/restart/status sidecars.
- **Each project's `project_path`** — the actual codebases, including
  `.claude/memory/MEMORY.md`, `.claude/projects/` transcripts,
  `.claude/skills/`. This is the big one: in hosted mode the *codebases live
  on the volume*, not on a laptop.

**Backups:** periodic volume snapshots (the orchestrator's job). DR story:
snapshot + restore re-attaches to a fresh microVM. The volume — not the VM —
is the durable user asset.

**Storage is the cost floor.** Because compute scales to zero on idle, the
always-on cost per dormant user is essentially *just the volume*. Right-sizing
and tiering cold volumes is a margin lever (§8).

---

## 7. Networking, ingress, auth — and what happens to mc-tunnel

In the existing design, `mc-tunnel` (the proprietary Rust binary) + `cloudflared`
exist to punch out of a home PC's NAT and to bind the open-core to our platform
(`docs/remote-access/07-licensing.md`). **In hosted mode the NAT problem
vanishes** — the microVM has a routable address in our fleet.

So the data path simplifies:

- Keep **Cloudflare Access** at the edge for auth (email/OTP or, for the app,
  service tokens — reuse the existing mobile-tokens machinery).
- The **control-plane ingress router** maps an authenticated request for
  `<user>.clayrune.io` to that user's microVM, **waking it if suspended**, and
  proxies to `:5199`.
- Per-VM `cloudflared`/`mc-tunnel` is **not required** and should be dropped
  for hosted instances (one fewer moving part, lower latency, no token-rotation
  loop per VM).

**The moat shifts and strengthens.** The licensing moat was "the closed tunnel
binary binds you to our platform." In hosted mode, **we run the entire stack** —
orchestration, fleet, lifecycle, key custody. That *is* the moat; it's far
harder to replicate than a tunnel binary. The MC core can stay open-core
(`07-licensing.md`) while the hosted control plane remains proprietary.

**App change is trivial:** the client is already domain-agnostic
(`mc_remote/config.py` — `PLATFORM_DOMAIN`, `control_plane_base_url()`); there
is no hardcoded `API_BASE` in the static frontend. Pointing the app at a hosted
instance is a configuration/onboarding difference, not a rebuild.

---

## 8. Revenue model

**We do not earn on tokens** (BYOK — user pays Anthropic). We earn a
**hosting/facilitation subscription**: the managed instance, persistent
storage, sleep/wake orchestration, backups, and the convenience of "no PC
required."

**Our cost structure per user:**
- **Active compute** — microVM seconds while awake. Minimized by sleep/wake;
  proportional to actual usage (interactive sessions + scheduled-job wakes).
- **Storage (the floor)** — persistent volume, always on even when dormant.
- **Egress** — agent traffic + model API calls leaving the VM.
- **Control-plane amortized** — orchestrator, vault, ingress, scheduler mirror
  (largely fixed; the existing remote-access plane already runs ~\$67/mo at 50
  users per `06-rollout-plan.md`, and scale-to-zero keeps this low).

**Margin = subscription − (active compute + storage floor + egress + amortized
control plane).** The sleep/wake decision is precisely what makes the unit
economics work: a user who runs a 20-minute session a day and one nightly
scheduled job costs us ~minutes of compute + a small volume, not a 24/7 VM.

**Pricing shapes to evaluate (Phase 3):**
- Flat monthly per managed instance (simplest; predictable margin).
- Tiered by resources (VM size / vCPU-hours cap / storage GB / concurrent
  agents) for power users.
- A free/cheap "dormant" tier (storage-only, wake-metered) to keep the floor
  cost recoverable for inactive users.

Because tokens are the user's cost (and usually their largest cost), our
subscription can be modest and still margin-positive — the value proposition is
"we make your own Claude key usable from a phone, 24/7, with no machine to
babysit."

---

## 9. What actually changes in the codebase

**Reused unchanged (the happy surprise):**
- MC server (`server.py`) — runs as-is inside the VM. Single-instance
  invariant is a feature here, not a bug.
- Claude CLI dispatch — BYOK works via inherited `ANTHROPIC_API_KEY`; no
  dispatch change.
- Scheduler + hivemind in-process loops — untouched; the external mirror wakes
  the VM and the existing catch-up logic fires the job (§4.3).
- GitHub sync / project sync, memory system, skills, settings — all run inside
  the VM exactly as today.
- The mobile/web client — domain-agnostic already; onboarding-only change.

**New (control plane — the build):**
- **Fleet orchestrator** — provision/start/suspend/destroy microVMs, attach
  volumes, inject the BYOK key at boot.
- **Key vault integration** — capture/encrypt/inject/rotate the user's API key
  (KMS reuse).
- **External scheduler mirror** — sync `next_run` from each VM on sleep; cron
  to wake before a due job.
- **Ingress router** — auth → resolve user's VM → wake-if-asleep → proxy to
  `:5199`.
- **Account / subscription / onboarding UI** — signup, paste key, connect
  GitHub, instance status ("awake/asleep/last active").
- **A tiny in-VM agent** (or reuse the health endpoints) so the orchestrator
  can read idle state + `next_run` and signal safe-to-suspend.

**Modified lightly:**
- A "hosted mode" flag so the server skips per-VM `cloudflared`/`mc-tunnel`
  startup (routing is handled by the control plane ingress).

---

## 10. Open questions & risks

1. **BYOK key custody liability (biggest non-technical risk).** We hold users'
   Anthropic keys. Need clear ToS, KMS encryption, no-plaintext-at-rest,
   incident plan. Consider offering a "key never leaves your browser → injected
   only at wake, never persisted in VM image" guarantee.
2. **Cold-start UX.** First request after sleep pays wake latency. Measure on
   the chosen provider (Phase 0). Mitigation: keep-warm for N seconds after
   activity; pre-wake on app foreground.
3. **Storage cost floor for dormant users.** Always-on volume per user even if
   they never log in. Mitigation: cold-tier dormant volumes; a storage-only
   pricing tier; archive-and-detach after long inactivity.
4. **Abuse / runaway compute.** A user could run heavy/endless agents (their
   token cost, but our compute cost). Need per-user compute caps / wake-budget,
   reusing the abuse-prevention posture in
   `docs/remote-access/04-abuse-prevention.md`.
5. **Egress & network policy.** Agents need outbound internet to be useful, but
   that's a risk surface. Per-VM egress policy + isolation.
6. **Provider lock-in.** Fly Machines is the leading candidate but the
   orchestrator should abstract the VM lifecycle so we can move to
   Firecracker-on-bare-metal for margin later.
7. **What stays of mc-tunnel.** Confirm we're comfortable dropping per-VM
   tunneling for hosted instances and that the licensing/moat story
   (`07-licensing.md`) is restated around the control plane (it strengthens —
   §7 — but the doc should be updated).
8. **DR / data durability.** Snapshot cadence, restore drills, "export my
   workspace" for user trust (and anti-lock-in promise).
9. **iOS.** This design makes the *server* cloud-side, so iOS finally works as
   a pure client — confirm the app shell story for iOS (out of scope here but
   newly unblocked).

---

## 11. Phased build plan

- **Phase 0 — Spike & prove the primitives.** Package the MC server as a
  microVM image. On the chosen provider (start with Fly Machines): launch one
  VM with a persistent volume, inject a test `ANTHROPIC_API_KEY`, run a real
  agent session end-to-end from a phone. Measure cold-start latency and
  per-session cost. **Decision gate:** provider + rough unit economics.
- **Phase 1 — Control-plane provisioning (single user, always-on).** Account +
  onboarding (paste key → encrypted vault), orchestrator that provisions a
  per-user VM + volume + key injection, ingress router with CF Access in front.
  No sleep yet — prove the full path works awake.
- **Phase 2 — Sleep/wake + external scheduler mirror.** Idle detection +
  suspend; wake-on-request via the proxy; the scheduler mirror that wakes the
  VM before a due job (§4.3). Validate that scheduled + hivemind jobs fire
  correctly across suspend/resume.
- **Phase 3 — Productize.** Subscription/billing, GitHub-connect onboarding,
  volume snapshots/backups, instance-status UI, per-user compute caps
  (abuse §10.4), pricing tier(s).
- **Phase 4 — Polish & scale.** Cold-tier dormant storage, keep-warm tuning,
  multi-region, iOS client, "export my workspace," provider abstraction for a
  later margin move.

---

## 12. Process / discipline

Per the project's standing discipline (Memory System §3.A.MID, Skills Curation
v2 committee gate), **no compute-plane code lands until this design clears a
committee review.** The strongest seats to staff: **concurrency/lifecycle**
(sleep/wake correctness vs. the in-process scheduler), **security/custody**
(BYOK key handling + microVM isolation), **cost/ops** (unit economics +
storage floor), and **platform/licensing** (moat restatement + mc-tunnel
deprecation for hosted).

**Resumability anchors:**
- This doc — `docs/HOSTED_CLOUD_PLATFORM_DESIGN.md` (authoritative).
- `docs/remote-access/01-architecture.md` — the platform this extends.
- `docs/remote-access/03-control-plane-api.md` — control-plane contract to
  extend (provisioning + vault + scheduler-mirror endpoints).
- `docs/remote-access/07-licensing.md` — moat/licensing to restate (§7).
- Scheduler bridge rationale — `server.py:_scheduler_loop` /
  `_hivemind_orchestrator_loop` (in-process daemon threads; fire-or-skip on
  resume; no missed-job queue).
- BYOK wiring point — CLI inherits `ANTHROPIC_API_KEY` from process env
  (no dispatch change).
