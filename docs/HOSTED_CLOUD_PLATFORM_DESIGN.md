# Hosted Clayrune — Cloud Compute Platform Design

**Status:** DRAFT v1.1 (2026-06-01, post-committee-review) — RATIFY-WITH-CONDITIONS
**Author:** Vector (with Ron)
**Committee:** 4-seat review 2026-06-01 (lifecycle / custody / cost / platform),
unanimous RATIFY-WITH-CONDITIONS, 0 blockers. Full assessments + synthesis at the
end of this doc (`## Committee review (2026-06-01)`). v1.1 edits below thread in the
must-fix-in-design conditions; each is tagged `[C:S<seat>.<n>]`.
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

### 3.1 POC topology (small scale) — one host, many containers

**The per-user-microVM fleet above is the scale-out target, not the starting
point.** For the POC (a handful of *trusted* testers, not the public), spinning
up a separate cloud instance per user is overkill and over-cost. Start with **one
rented host** and isolate users *on* it:

```
   one host we rent (a single VPS / Fly machine / Hetzner box)
   ├── user A container → MC on :5199 (own netns) + /data/A volume
   ├── user B container → MC on :5199 (own netns) + /data/B volume
   └── user C container → …
                 │
                 └── object-storage bucket  (durability + cold/archive)
```

- **One bill.** You pay for the single host, not N instances. At POC scale the
  storage floor, the fleet orchestrator, and the ~$67 control plane all collapse
  — the "orchestrator" is `docker compose` + a few commands.
- **The single-instance invariant is preserved, not violated** (reconciles
  §4.1). Each container has its own network namespace, so every user's MC binds
  *its own* `localhost:5199` with no collision and **no code change** — exactly
  the property §4.1 wants, achieved with containers instead of microVMs. Each
  container still owns its `DATA_ROOT`, scheduler, and memory files.
- **Isolation is a ladder, not a cliff.** For trusted testers, **plain Docker
  containers** are enough today. When untrusted users arrive, swap the container
  runtime to **gVisor / Kata / Firecracker** — each container then gets its own
  kernel / micro-VM: *same topology, stronger walls, no app change.* Don't pay
  for hardware-grade isolation against your own testers on day one; raise the bar
  with the audience.
- **Storage: local volume for live, bucket for durable** (answers "can we use
  buckets?"). Do **not** run the live workspace off a bucket-fuse mount
  (s3fs/gcsfuse): MC drives git working trees + thousands of small files, and git
  over fuse is slow and flaky. Keep each user's *live* data on a **local volume**
  (fast POSIX fs); **sync/snapshot to an object-storage bucket** (S3 / GCS /
  Cloudflare R2) for durability, backup, and cold/archive. This is the
  committee's archive-and-detach idea (§8 `[C:S3.2]`) pulled in early — and it is
  the per-user durable asset if the host dies.
- **Sleep/wake is optional at POC.** With a few users on one box, keep containers
  warm (or `docker stop` idle ones — trivial, no scheduler mirror needed). The
  whole §4.3/§4.4 sleep/wake machinery is a scale concern; defer it.

**Committee conditions that *relax* at POC scale** (they were scale problems):
the storage floor (trivial at N=few), runaway-compute (bounded by one host you
watch — `docker stats` + a cap), the $67 control-plane reuse, and
cold-start/sleep-wake (keep warm). **Conditions that still apply even at POC:**
BYOK env-inheritance is still the wiring (§5 — verified, free); the
`provider_env.json` plaintext-on-volume hazard (§5.3 `[C:S2.1]`) is real the
moment a key is injected; per-container key isolation; and you still hold a
user's key, so be honest about custody (§5) even with testers.

**This *is* Phases 0–1, on one host.** The scale-out path is a swap, not a
rewrite: container→microVM, one-host→fleet, local-volume→per-VM-volume,
docker-compose→fleet-orchestrator. Write the control-plane seam (§9) so that swap
is *configuration, not a second system* — the in-VM MC core is identical either
way.

**Concrete setup:** `docs/HOSTED_CLOUD_POC_RUNBOOK.md` — the container image
(headless MC, slimmed deps), the per-user docker-compose + Caddy routing shape,
how a new tester gets provisioned, the local-volume→bucket durability sync, and
the code-grounded gotchas (inject the key via env not the in-app UI, etc.).

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

**(POC note:** at small scale this same "one isolated instance per user"
property is achieved with *containers on one host* — see §3.1. The per-microVM
fleet here is the scale-out target. The argument below — lean into the
single-instance invariant rather than refactor for multi-tenancy — holds
identically for containers: each gets its own netns and its own `:5199`.)

### 4.2 Provider / mechanism

Primary candidate: **Fly Machines** (Firecracker microVMs with
suspend/resume, scale-to-zero, wake-on-request via the Fly proxy, and
attachable persistent volumes). The lifecycle primitives we need map directly:

- **suspend/stop on idle** → compute billing stops; volume persists.
- **wake on incoming request** → the proxy starts the machine and holds the
  request; VM resume is sub-second to a few seconds. **Caveat `[C:S3.3]`: that
  is the *VM*, not *MC*.** On the cold-boot (stop) path MC must also become
  ready (Flask init, `load_projects()`, scheduler/hivemind thread start,
  builtin-skills/MCP checksum scans) — realistically ~3–15s on top of VM resume.
  The heavy reconcile is already daemon-threaded off the hot path (good), but
  the synchronous boot work is real. Only the *resume-from-snapshot* (suspend)
  path gives a truly already-booted MC. See §4.4.
- **persistent volume** → survives suspend/resume and restarts.

Alternatives to evaluate against the same criteria (suspend/resume latency,
per-second billing, volume durability, microVM isolation, egress cost):
AWS Firecracker via a custom orchestrator, Firecracker-on-bare-metal
(Hetzner/Equinix) for margin, or a container runtime with strong isolation
(gVisor/Kata) if a microVM provider proves too costly. **Decision deferred to
a Phase-0 spike** that measures cold-start + cost on real workloads. The spike's
scope is expanded by the committee (§4.4, §11 Phase 0): it must measure both the
*stopped* and *suspended* idle billing state, MC-ready wall time (not just VM
resume), and assert the suspend path is snapshot-clean for a live Mode-B process
(`agent_sessions` survives, no hard-kill reconcile fires). `[C:S1.6, S3.1]`

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
> on each sleep). A single control-plane cron wakes the user's microVM **at or
> just after** each due `next_run` — **not "shortly before."** `[C:S1.1]` The
> in-VM `_scheduler_loop` is check-first / wait-last (`server.py:12534`, wait at
> `12712`), so a job that is *already overdue* at the first post-wake iteration
> fires immediately; a job due 1s in the *future* falls through to the 30s
> `wait()` and fires up to 30s late. Waking at/after `next_run` (plus a lead
> margin sized from Phase-0 `p95(VM_resume)+p95(MC_boot)`) keeps the 30s tick off
> the critical path. The mirror wakes the VM **per due occurrence** it knows
> about, so under normal operation no window is missed.

Properties:
- In-VM scheduler is untouched (best-effort posture preserved).
- The mirror only needs `next_run` per schedule — no logic duplication.
- **Fire-or-skip is the *degraded fallback*, not the mechanism. `[C:S1.2]`**
  Be explicit: the in-VM loop fires a schedule that came due during sleep
  *exactly once* on the next wake and recomputes a single forward `next_run`
  (verified for daily/cron `_compute_next_run` `server.py:12444`; `interval`
  clamps a stale next-run to `now+5s` at `12509`). A VM asleep across three
  nightly windows therefore fires **once**, where an always-on PC fires three
  times. This is a deliberate, documented hosted semantic — acceptable for
  "latest-state" jobs, and avoided in normal operation because the mirror wakes
  per-occurrence. The single-fire path is only hit when a *wake itself* was
  missed (mirror down across windows) — list that residual in §10.
- **Suspend is a two-phase commit. `[C:S1.5]`** (1) orchestrator reads the
  VM's schedules and persists the mirror; (2) **only on a successful sync** does
  it issue the suspend. A failed sync *blocks* the suspend (VM stays awake a bit
  longer — pure cost, never a missed wake). This makes the "degrades to fire-late"
  worst case actually hold; without it, a schedule edited in the last awake moment
  can be lost until an unrelated wake.

**Wake triggers (any of):** (a) authenticated incoming request from the
client, (b) control-plane scheduler mirror firing for a due job, (c) a hivemind
workstream that was mid-flight at suspend (treated like a scheduled wake).

**Sleep trigger:** idle = no active client connection (SSE) **AND** no running
agent session **AND** no scheduled job due within the lookahead window. A short
cooldown after the last activity avoids thrashing.

> **Idle-detection correctness `[C:S1.3, S1.4]`.** "No running agent" must use
> the codebase's own definition: `_has_running_agent` (`server.py:1137`) is True
> for status in **`('running','idle')`** — a Mode-B process kept *alive between
> turns* (`idle`) still holds a warm KV-cache and, mid-turn, a live API socket,
> so it MUST block suspend. SSE alone is an unreliable signal (mobile Doze parks
> Capacitor sockets), so the agent guard is load-bearing, not redundant. Today
> no single endpoint exposes "is anything running across all projects" +
> soonest `next_run`; the orchestrator needs one (see §9 — `GET
> /api/system/idle-state`, ~30 LOC). A Mode-B session that stays `idle` without
> teardown blocks suspend indefinitely → the orchestrator relies on a max-session
> teardown or the existing 30-min stale purge (`server.py:12663`) to release it.

### 4.4 Wake path: stop vs. suspend (the central Phase-0 decision)

The locked sleep/wake decision has **two physical realizations**, and the three
review seats (lifecycle, custody, cost) converged on this being the single
under-specified crux. They are not interchangeable:

| | **Stop** (scale-to-zero) | **Suspend** (RAM snapshot) |
|---|---|---|
| Idle compute cost | **$0** (volume only) | possibly **non-zero** (resident RAM reservation — measure) |
| Wake latency | full **MC cold-boot** (~3–15s) on top of VM start | **already-booted** (~1–3s) — RAM image restored |
| In-flight Mode-B turn across the transition | **lost** — process gone, `agent_sessions` lost, traverses the hard-kill path (`_reconcile_unscribed_sessions` `server.py:4548`) | **preserved** — snapshot-clean, process + sockets restored (live API socket may still be dead — guard via idle-detection) |
| BYOK key at rest | **not** in any snapshot | **in the RAM-snapshot blob at rest** — undermines §5/§10.1 "never persisted" |

> **The trilemma `[C:S1.6, S2.2, S3.1, S3.3]`:** you cannot simultaneously have
> *fast resume-from-snapshot*, *the key never at rest*, and *zero idle compute
> cost*. Pick two, consciously:
> - **Stop** buys $0-idle + no-key-at-rest, but pays MC cold-boot on every wake
>   (Seat 3's latency hit, Seat 1's scheduler-skew compounder) and loses the
>   in-flight turn on every sleep (Seat 1's hard-kill path).
> - **Suspend** buys fast wake + snapshot-clean in-flight turns, but puts the
>   key at rest in the snapshot (Seat 2 — requires encrypted-at-rest +
>   revoke-destroys-snapshot, §5.4) and may carry a non-zero idle charge (Seat 3).

**Recommended (to validate in Phase 0): a hybrid.** Use **suspend** during an
active "session window" (recent activity / pre-warm on app foreground) so
interactive taps and mid-flight turns hit a resumed image; **stop** after a
longer idle once no session is live (cheap dormancy). This bounds the key-at-rest
exposure to the active window and the cold-boot cost to the first tap of a
session. Phase 0 must measure: stopped vs suspended billing, MC-ready wall time,
and a snapshot-clean assertion (start a Mode-B agent → drive to `idle` → suspend
past Anthropic's socket timeout → resume → assert `agent_sessions` intact and no
reconcile fired). If the chosen provider can only *stop*, the in-flight-turn loss
and cold-boot latency are accepted and re-costed, and idle detection must be
re-validated against a fresh-process `agent_sessions={}`.

---

## 5. BYOK key handling

**Why it's nearly free to implement (committee-VERIFIED `[C:S2,S4 ratified]`):**
the server does **not** manage Claude credentials. When it spawns the CLI it
passes **no `env=` kwarg** (`server.py:6938` Mode B, `server.py:7033` Mode A;
Mode-A/Gemini helpers `env=os.environ.copy()` at `agent_runtime.py:1752/2193`),
so `Popen` inherits the parent process environment and the CLI picks up
`ANTHROPIC_API_KEY` from it. So BYOK reduces to **"launch the MC process in the
microVM with the user's key in the env."** No dispatch-path code change.
**Load-bearing invariant:** any future refactor that switches dispatch to a
curated `env=` dict silently breaks BYOK — gate it.

**Lifecycle:**
1. **Capture.** User pastes their Anthropic API key in the hosted onboarding
   UI (control plane), over TLS.
2. **Store.** Encrypted at rest in a control-plane key vault (KMS-backed —
   the existing design already uses Cloud KMS; reuse it). Never written to the
   VM image or to any logs.
3. **Inject at wake.** The orchestrator injects the key as `ANTHROPIC_API_KEY`
   into the microVM's MC process environment at start/resume. Use a
   tmpfs/secret-mount or per-boot env injection — **never** the persistent
   volume. **Hazard `[C:S2.1]`: MC already has a Settings → Agent Providers
   surface (`POST /api/agent/provider/<name>/env`, `server.py:3052`) that writes
   provider keys *plaintext* to `data/provider_env.json` on the volume
   (`_save_provider_env_file`, `server.py:3008`) AND `os.environ[key]=val`
   *unconditionally* (`server.py:3086`) — so a key set via that UI both lands on
   the volume and *overrides* the injected key.** Hosted mode MUST disable the
   `provider_env.json` write path for `ANTHROPIC_*` (and other `*_API_KEY`)
   credentials and route key changes to the vault, OR relocate `PROVIDER_ENV_PATH`
   onto a non-snapshotted secret-mount. This applies to all provider keys
   (Gemini/OpenAI/Bedrock), not just Anthropic — scope hosted BYOK to
   Anthropic-only in v1 or extend the vault story to all of them.
4. **Rotate / revoke `[C:S2.2]`.** Key is updatable from the UI; rotation
   re-injects on next wake. Revoke = clear vault entry + **destroy any suspend
   snapshot** + force a cold-boot on next wake. A suspended VM holds the key in
   its RAM snapshot (§4.4) — clearing the vault and "restarting the live process"
   does **not** reach a frozen snapshot; a later resume would resurrect the
   revoked key. Never resume a revoked VM from a pre-revoke snapshot. Requires
   the provider's snapshot/volume to be **encrypted at rest** (a §4.2
   provider-selection criterion).

**Trust model (state it explicitly) `[C:S2.5]`.** The agent has a Bash tool and
outbound internet (§10.5), so it *can* read its own env and exfiltrate its own
key. That is the **user's own** key — blast radius = their own Anthropic account,
which they consented to by pasting it. This is in-scope-acceptable. The invariant
we defend is **VM isolation + zero shared-secret reachability**: one user's VM
cannot reach another's, and **no control-plane secret** (KMS credential, Fly API
token, ingress secret) is ever present in a user VM's env or volume. The §9
in-VM orchestrator agent must use a per-VM, short-lived, non-shared credential
(or a pull model where the VM only *exposes* read-only idle state). Phase 0 must
assert `env` inside the VM contains only the injected key + benign config.

**Honest custody guarantees (the earlier draft over-promised) `[C:S2.3]`.** The
key is *not* in argv and *not* printed by MC's own code paths — but it **can**
appear in agent transcripts if the agent dumps its own env (`env`,
`printenv`, `cat provider_env.json`), which flow to `session['log_lines']`
(`server.py:4112`, no scrubbing) and to the on-volume CLI `.jsonl` transcript,
which Scribe then summarizes into `MEMORY.md` (also on the volume). So "no key in
logs" is false as a flat claim. Add a best-effort `sk-ant-…` redaction pass at
the log-append + pre-Scribe sites (best-effort, never load-bearing — same posture
as Scribe's thin guards). Note this exposure is *identical to the local
BYO-machine product*; the delta is that the volume is now **ours**.

**Custody inversion — the biggest non-technical risk `[C:S2.4]`.** The existing
platform's foundational promise (`docs/remote-access/01-architecture.md` §1) is
"MC-the-operator never holds user data" and lists *cloud-hosted agent execution*
as an explicit **v1 non-goal**. Hosted mode inverts both: we now hold the
codebases, transcripts, hivemind state, **and** a third-party billing credential,
on a volume we own. The delta vs. "we already hold CF service tokens" is
asymmetric: a CF token is **platform-revocable** (we mint it, we kill it at the
edge); an **Anthropic key is not** — only the user (or Anthropic) can revoke it,
so our sole post-incident lever is "tell the user to rotate." This needs (a) an
explicit statement that the `01-architecture.md` §1 promise is *superseded for
hosted mode*; (b) a concrete incident plan (vault access audit log, notification
SLA, a "rotate your Anthropic key" runbook); (c) a hosted-specific
data-custody ToS clause — a **hard gate on Phase 1** (capturing the first real
key). The legacy `07-licensing.md` ToS is binary-distribution/platform-access
only and says nothing about custody of third-party keys or user code. See §10.

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

**The moat — restated honestly `[C:S4.2]`.** The earlier draft claimed the moat
"shifts and strengthens." Pressure-tested, that is mostly wrong. Today's moat is
*concrete and proprietary*: the closed Rust `mc-tunnel` binary embeds
`CLIENT_SECRET_PRIV` and `/attest` rejects any envelope not signed by an active
platform client key (`07-licensing.md` §2.2/§3.1) — a fork cannot use
`*.clayrune.io` without extracting that key. **Hosted mode *drops* exactly that
binding (§7) and runs the open-source MC core (`server.py` + all agent/scheduler
logic is MIT) behind a Fly-API-caller + KMS vault + ingress proxy — every piece a
commodity.** A competitor *or the user* can self-host the open core on their own
Fly account and replicate the orchestration in a weekend. So hosted strictly
*weakens* technical defensibility vs. the BYO product. What actually defends
hosted revenue is **convenience + data gravity** (codebases on our volume, §6) **+
brand** — legitimate *switching-cost* levers, not *anti-clone* levers. They **cap
pricing power**: the alternative to "$X/mo hosted" is "run the same open core on
~$5/mo of my own Fly compute." §8's "subscription can be modest and still
margin-positive" instinct is right — modest *because the moat is thin*, not
despite a strong one. (This makes the storage/runaway cost controls in §8/§10.4
more load-bearing: less margin to absorb them.) The MC core stays open-core; the
hosted control plane is proprietary — but call it a convenience play, not a moat.

**Data path is config; onboarding is a new second flow `[C:S4.5]`.** The client
is domain-agnostic (`mc_remote/config.py:26` `PLATFORM_DOMAIN`,
`control_plane_base_url()` `:37`) — so pointing the app at a hosted instance is
config *at the network layer*, no rebuild. But the *onboarding UX genuinely
diverges* and the earlier "onboarding-only change" framing understated it: BYO =
"install MC → generate device keypair → Firebase signin → `/v1/enroll` provisions
a tunnel → attestation loop"; hosted = "sign up → we provision VM+volume → paste
API key → we inject it → connect GitHub." They share Firebase auth + domain
config but are two distinct flows with different artifacts, failure modes, and
security surfaces (OS-keystore device key vs. custodial API key). §9's "Account /
subscription / onboarding UI" bullet is genuinely new.

### 7.1 Hosted enrollment is a separate path, not an extension `[C:S4.1, S4.3, S4.4]`

A correction to §1's "reuses the device-enrollment concepts." At the data-model
and protocol level the existing control plane (`control_plane/api_spec.yaml`,
`docs/remote-access/02-attestation-protocol.md`) **cannot be reused as-is** for
hosted enrollment — it is built to *attest a user's PC*:

- `EnrollRequest` **requires `device_pub_b64`** (`api_spec.yaml:135`) — an
  Ed25519 keypair generated on the user's PC and held in its OS keystore. Hosted
  mode has no user PC to hold a device private key.
- `_do_enroll_after_auth` provisions a CF **named tunnel** pointing at
  `http://localhost:5199` (`control_plane/app/routes_account.py:1001`) — i.e. a
  `cloudflared` on the user's PC dialing out. §7 explicitly drops per-VM tunnels
  and routes via a control-plane ingress proxy, so the enroll provisioning body
  is wrong for hosted.
- The whole attestation surface (`/v1/nonce`, `/v1/attest`, `api_spec.yaml:742`)
  requires a signed envelope carrying both the device-key and the mc-tunnel
  client-secret signatures — **hosted emits no attestation.**

**Therefore hosted onboarding is a deliberate FORK**, declared as a new
control-plane surface (e.g. `/v1/hosted/*`: provision-VM + capture-key +
bind-account). Disposition of existing endpoints:
- **Reused verbatim** (device-agnostic): `/v1/signin/*`, Firebase auth,
  `/v1/account`, `/v1/sessions*`.
- **Replaced**: `/v1/enroll`, `/v1/nonce`, `/v1/attest`.
- **Reused with shifted semantics**: the `devices` row now describes a *microVM*;
  `/v1/devices/{id}/mobile-tokens` (`routes_account.py:510`) binds a phone to the
  *VM's* CF Access app (created at provision time), but the CF Access
  service-token header injection on the phone (`CF-Access-Client-Id/-Secret`,
  `server.py:14290`) is **unchanged** from BYO. **Watch-out:** the `devices.online`
  heuristic (last_seen < 15 min, `routes_account.py:96`) will read a
  *suspended-but-healthy* VM as offline — the orchestrator must track awake/asleep
  separately or the UI misreports asleep VMs as down.

### 7.2 Open/closed boundary for the "hosted mode" flag `[C:S4.3]`

The §9 "hosted mode" flag touches `server.py`, which is open-core. Keep the
boundary clean: the open core exposes only a **neutral seam** (the existing
`RemoteAccessProvider` Protocol, or a hook that returns `None` in open builds);
**all** hosted-specific behavior (skip-tunnel, ingress expectations, the in-VM
idle/`next_run` reporting agent) lives in a proprietary module on the `mc_remote/`
side. The BYO path retains mc-tunnel + attestation **unchanged** — dropping
mc-tunnel *for hosted* does not weaken BYO licensing *as long as BYO keeps it*.
Add a line to `07-licensing.md` §2 noting the hosted control-plane components are
proprietary (a third proprietary citizen alongside `mc_remote/` and `mc_tunnel`).

---

## 8. Revenue model

> **Under revision (2026-06-02) — BYOK vs managed tokens.** This section was
> written under the committee-era assumption of **BYOK** (user pays Anthropic;
> we earn a hosting fee). The chosen launch buyer is now **non-technical,
> mobile-first, no-PC users** — for whom BYOK is likely a conversion-killer (they
> won't create and paste an Anthropic API key). That pushes toward a
> **managed-token** model (one signup, one bill, "just works"), which
> reintroduces token margin but makes us a reseller carrying the token tail.
> A full forecast of the managed-token model — tiers, per-user COGS, scenarios,
> sensitivity — lives in **`docs/HOSTED_CLOUD_INCOME_MODEL.md`** (computed by
> `docs/poc/income_model.py`). Headlines: tokens become ~90% of COGS (storage
> stops being the cost center); **prompt caching is make-or-break** (caching-off
> = money-losing); the **allowance is the product** (heavy users are a loss
> unless capped); ~35% gross margin at scale, consistent with the thin moat (§7).
> The BYOK framing below is retained as the alternative / power-user on-ramp
> until the model choice is finalized.

**(BYOK framing — retained as the alternative.)** **We do not earn on tokens**
(BYOK — user pays Anthropic). We earn a **hosting/facilitation subscription**:
the managed instance, persistent storage, sleep/wake orchestration, backups, and
the convenience of "no PC required."

**Our cost structure per user** (committee re-did the arithmetic; numbers are
public-pricing estimates to be confirmed in Phase 0 `[C:S3.1]`):
- **Active compute** — microVM seconds while awake. For the headline persona
  (20-min/day session + one 5-min nightly job, on a ~4GB machine) ≈ **$0.55/mo**.
  The "minutes of compute" claim holds; sleep/wake earns its keep here.
- **Storage (the *dominant* per-active-user line, not "a small volume")
  `[C:S3.1]`** — §6 puts the user's **codebases** on the volume (`.git`,
  `node_modules`/build artifacts, transcripts). A conservative single-project
  user ≈ 5GB → **$0.75/mo**; a multi-repo power user 20–50GB → **$3–$7.50/mo**.
  So storage is **1.4×–5.5× the active compute.** Price on **GB**, not vCPU-hours.
- **Egress (ours, even though tokens are theirs) `[C:S3.4]`** — every model
  call's *bytes* transit our NIC. Negligible for the median (~$0.02/mo) but
  **material for the tail** (a hivemind/`/loop` user can do GB/day → ~$3/mo) and
  **uncapped for the adversary** — see the egress cap in §10.4.
- **Control-plane amortized** — orchestrator, vault, ingress, scheduler mirror.
  **Do NOT reuse the "~$67/mo at 50 users" figure here `[C:S3.7]`.** That figure
  (`06-rollout-plan.md` §13.1: Cloud Run + Firestore + KMS + Redis + logging +
  Workers + domains) is for the **relay-only** plane and contains *zero*
  compute-hosting cost. The fleet orchestrator (a new always-on service), per-VM
  compute, volume storage, and egress are all **new** lines it does not cover.
  Keep $67 only as the auth/relay line; itemize the rest separately.

**Dormant users are the real exposure `[C:S3.2]`.** Every signup = a permanent
volume. At 1,000 dead signups × ~3–8GB → **$450–$1,200/mo of pure loss** under a
free tier (Fly volumes also have a practical minimum, so even empty ones cost
~$0.15–0.45/mo each). The mitigation (archive-and-detach dormant volumes to
object storage, ~85% cheaper, restore-on-login) is therefore **not Phase-4
polish — it is the economic precondition of any free/dormant tier.** Until it
ships: **no free tier at GA** (invite-only or paid-only, mirroring the
`06-rollout-plan.md` M5 invite gate).

**Margin = subscription − (active compute + *storage floor, dominant* + egress +
incremental orchestrator + amortized auth/relay).** Worked example at a $7/mo
sub, 20GB power user ≈ $2.9 gross before control-plane amortization — positive,
but **thinner than "tokens are the user's cost so our sub can be modest"
implies**: the modesty is bounded by **storage**, not compute.

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
- Memory system, skills, settings — run inside the VM as today.
- GitHub sync / project sync — **NOT "exactly as today" `[C:S3.6]`.** The in-VM
  scheduler runs GitHub/code auto-sync *every 5 min per enabled project*
  (`server.py:12620`). Under suspend this forces a choice: either those ticks
  keep the VM awake every 5 min (≈ always-on — **defeats the sleep/wake premise**
  for that user), or they're excluded from idle/lookahead and **sync silently
  stops** (a regression vs. the always-on PC). Decision required (§10): demote
  sync to "fire opportunistically on next wake" (document the latency), or lift
  git/code sync into the external mirror (wake-for-sync like wake-for-job).
  Same question applies to `_update_check_loop`, `_session_label_enforcer_loop`,
  `_warmup_control_plane`. §9's "exactly as today" claim is retired.
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
- **A single in-VM idle-state endpoint `[C:S1.4]`** — `GET
  /api/system/idle-state` returning `{running_agents, soonest_next_run,
  last_activity_at}`. Today no endpoint aggregates running-session state across
  *all* projects (`/api/system/status` exposes neither; `/agent/status` is
  per-project; `/api/schedules` has `next_run` only), so the orchestrator's
  safe-to-suspend decision has no single source of truth. ~30 LOC behind the
  hosted-mode flag, reusing the `_has_running_agent` `('running','idle')`
  predicate (`server.py:1137`). Suspend iff `running_agents==0 AND no SSE AND
  (soonest_next_run is None OR soonest_next_run-now > cooldown)`.
- **Hosted VM image hygiene `[C:S2.6]`** — the image must **exclude** the
  `mc_remote`/`mc_tunnel` binaries entirely (not merely skip starting them), so
  the embedded `CLIENT_SECRET_PRIV` is never on a disk running arbitrary agent
  code. Enforce at build time; assert in Phase 0 packaging.

**Modified lightly:**
- A "hosted mode" flag so the server skips per-VM `cloudflared`/`mc-tunnel`
  startup (routing is handled by the control plane ingress).

---

## 10. Open questions & risks

1. **BYOK key custody liability (biggest non-technical risk).** Fully addressed
   in §5 (now committee-corrected): the honest guarantee is "encrypted at rest +
   destroyed on revoke," **not** "never at rest" (the suspend snapshot holds it,
   §4.4); the existing `provider_env.json` write path must be disabled in hosted
   mode (`server.py:3008`); the key can appear in agent transcripts; and the
   custody inversion vs. `01-architecture.md` §1 needs an incident plan + a
   hosted data-custody ToS clause as a Phase-1 gate.
2. **Cold-start UX.** First request after sleep pays wake latency. Measure on
   the chosen provider (Phase 0). Mitigation: keep-warm for N seconds after
   activity; pre-wake on app foreground.
3. **Storage cost floor for dormant users.** Always-on volume per user even if
   they never log in. Mitigation: cold-tier dormant volumes; a storage-only
   pricing tier; archive-and-detach after long inactivity.
4. **Abuse / runaway compute — needs a real primitive, not "reuse the posture"
   `[C:S3.5]`.** `04-abuse-prevention.md` is an *edge Worker* capping request
   rate/bandwidth/body-size — it has **no concept of in-VM compute time** (in the
   BYO product, compute is the user's own electricity). Hosted inverts this: a
   user fires `/loop 5m`, backgrounds the phone, and the in-VM scheduler keeps
   the agent running on **our** compute — and the §4.3 idle guard *keeps the VM
   awake* the whole time because "running agent" is true. One runaway = ~$32/mo
   of our compute + uncapped egress against a flat sub. **Concrete primitive
   (orchestrator-side, no VM inspection):** (a) **awake-seconds/month budget**
   per tier — on breach, force-suspend + surface "compute budget exhausted —
   resume/upgrade"; (b) **continuous-awake watchdog** — if a VM is awake > T
   (e.g. 2h) with no inbound SSE, force-suspend regardless of "running agent"
   (the awake-budget guard **overrides** the keep-awake guard); (c) the egress
   cap (above) on the same watchdog. A force-suspend mid-runaway must degrade to
   fire-or-skip cleanly (Seat 1 confirms: the job is *paused*, resumes on next
   wake) — never corrupt an in-flight turn. This makes §8's "vCPU-hours cap" real
   rather than aspirational.
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
9. **iOS — *easier*, not *unblocked* `[C:S4.6]`.** Hosted removes the per-device
   *tunnel/attestation* native dependency (the phone never ran mc-tunnel anyway),
   but **not** the CF Access service-token header injection
   (`CF-Access-Client-Id/-Secret` on every request, `server.py:14290`): §7 keeps
   CF Access at the edge, and a WebView doesn't natively attach those headers, so
   iOS would still need native injection (WKWebView `URLProtocol`/custom scheme),
   exactly as Android does today. A *true* pure-client iOS additionally requires
   replacing CF-service-token edge auth with token-in-app auth at the ingress
   proxy — out of scope here, but it's the lever. Stop asserting "unblocked."
10. **Suspend snapshot holds the key at rest (§4.4, §5.4).** The sleep/wake
    mechanism itself persists process RAM — including the injected key — to disk.
    Requires encrypted-at-rest snapshots + revoke-destroys-snapshot.
11. **Internal periodic timers vs. suspend (§9, `[C:S3.6]`).** GitHub/code sync,
    update-check, label-enforcer, CP-warmup either keep the VM awake or silently
    stop under suspend. Per-task hosted semantic must be decided.
12. **Scheduler fire-or-skip drops missed runs across a multi-window sleep
    (§4.3, `[C:S1.2]`).** Mitigated by per-occurrence wakes; residual loss only
    if the *mirror* is down across windows.
13. **`devices.online` misreports a suspended VM as offline (§7.1,
    `[C:S4.4]`).** Track orchestrator awake/asleep state separately from the
    15-min last-seen heuristic.

---

## 11. Phased build plan

- **Phase 0 — Spike & prove the primitives.** Package the MC server as a
  microVM image (excluding `mc_remote`/`mc_tunnel` binaries, `[C:S2.6]`). On the
  chosen provider (start with Fly Machines): launch one VM with a persistent
  volume, inject a test `ANTHROPIC_API_KEY`, run a real agent session end-to-end
  from a phone. **Committee-expanded measurement set:** (a) **stopped vs
  suspended** idle billing (§4.4); (b) **MC-ready** wall time, not just VM resume
  (§4.2 `[C:S3.3]`); (c) **snapshot-clean assertion** — Mode-B `idle` → suspend
  past Anthropic's socket timeout → resume → `agent_sessions` intact, no reconcile
  (`[C:S1.6]`); (d) **env audit** — VM env contains only the injected key + benign
  config, no platform secret (`[C:S2.5]`). **Decision gate:** provider + wake-path
  (stop/suspend/hybrid) + rough unit economics incl. the **dormant-storage tier
  decision** (it changes the storage-tier choice, `[C:S3.2]`).
- **Phase 1 — Control-plane provisioning (single user, always-on).** New
  `/v1/hosted/*` enrollment path (NOT attestation, §7.1 `[C:S4.1]`); account +
  onboarding (paste key → encrypted vault, with the `provider_env.json` write
  path disabled for keys `[C:S2.1]`); orchestrator provisions per-user VM + volume
  + key injection (per-VM non-shared orchestrator credential `[C:S2.5]`); ingress
  router with CF Access. **Gates:** hosted data-custody ToS clause + incident plan
  live before capturing the first real key (`[C:S2.4]`); open/closed seam in place
  (§7.2 `[C:S4.3]`). No sleep yet — prove the full path works awake.
- **Phase 2 — Sleep/wake + external scheduler mirror.** `GET /api/system/idle-state`
  (`[C:S1.4]`); idle detection using the `('running','idle')` guard (`[C:S1.3]`);
  two-phase suspend with mirror-sync-before-suspend (`[C:S1.5]`); wake at/after
  `next_run` per-occurrence (`[C:S1.1]`); decide the internal-timer semantics
  (`[C:S3.6]`). **Validate:** scheduled + hivemind jobs fire correctly across
  suspend/resume, including a multi-window-miss test (`[C:S1.2]`) and a
  no-suspend-mid-turn test (`[C:S1.3]`).
- **Phase 3 — Productize.** Subscription/billing; GitHub-connect onboarding;
  volume snapshots/backups; instance-status UI (awake/asleep tracked separately
  from `devices.online`, `[C:S4.4]`); **concrete compute caps** — awake-budget +
  continuous-awake watchdog + egress cap (§10.4 `[C:S3.5]`); pricing tier(s) on
  **GB** (`[C:S3.1]`). **Prerequisite for any free/dormant tier:** archive-and-detach
  must ship (`[C:S3.2]`) — until then invite-only or paid-only.
- **Phase 4 — Polish & scale.** Keep-warm tuning, multi-region (compounds the
  per-region storage floor), iOS client (*easier, not free* — §10.9), "export my
  workspace," provider abstraction for a later margin move.

**Soak-gates (block default-on flips, not the build):** snapshot-encryption +
revoke-destroys-snapshot before sleep/wake flips on (`[C:S2.2]`); the
snapshot-clean assertion proven on the production provider before default-on
(`[C:S1.6]`); awake-budget thresholds tuned before any free tier (`[C:S3.5]`).

---

## 12. Process / discipline

Per the project's standing discipline (Memory System §3.A.MID, Skills Curation
v2 committee gate), **no compute-plane code lands until this design clears a
committee review.** That review **ran 2026-06-01** — four seats
(concurrency/lifecycle, security/custody, cost/ops, platform/licensing), brief at
`docs/HOSTED_CLOUD_COMMITTEE_BRIEF.md`, seat assessments under
`docs/_committee/HOSTED_CLOUD_seat{1..4}_*.md`. **Result: unanimous
RATIFY-WITH-CONDITIONS, 0 blockers.** The must-fix-in-design conditions are
threaded into the sections above (tagged `[C:S<seat>.<n>]`); this brings the doc
to **v1.1**. The full synthesis + verbatim assessments follow. Remaining gate
before build: none on design — Phase 0 may proceed (it *is* the spike that
resolves the wake-path/economics decisions the committee routed to it).

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

---

## Committee review (2026-06-01) — RATIFY-WITH-CONDITIONS

**Process.** Four parallel review seats per `docs/HOSTED_CLOUD_COMMITTEE_BRIEF.md`,
no cross-seat communication. Each seat read the design end to end and grounded
findings in code (`file:line`) and the remote-access docs. Verbatim seat
assessments are reproduced below and also saved at
`docs/_committee/HOSTED_CLOUD_seat{1..4}_*.md`.

**Tally.** Seat 1 (lifecycle) · Seat 2 (custody) · Seat 3 (cost) · Seat 4
(platform) — **all four RATIFY-WITH-CONDITIONS. Overall = RATIFY-WITH-CONDITIONS
(strictest of four). Zero blockers.** Seat 4 named one conditional-block trigger
(if v1.1 doubled down on "reuse existing enrollment" without acknowledging the
fork); v1.1 §7.1 acknowledges it, so the trigger is not pulled.

### Strongest cross-seat convergences

1. **The stop-vs-suspend trilemma (Seats 1+2+3).** The most important finding,
   independently surfaced by three seats. You cannot have *fast
   resume-from-snapshot* + *key never at rest* + *zero idle compute cost* at once.
   **Suspend** → fast wake + snapshot-clean in-flight turns, but the key sits in
   the RAM snapshot at rest (Seat 2) and idle may bill non-zero (Seat 3).
   **Stop** → $0 idle + no key at rest, but full MC cold-boot per wake (Seat 3
   latency, Seat 1 scheduler-skew) and the in-flight turn is lost via the
   hard-kill path (Seat 1). Resolved in v1.1 §4.4: hybrid recommendation +
   Phase-0 must measure both and assert snapshot-clean.

2. **BYOK env-inheritance is VERIFIED TRUE (Seats 2+4, both ratified).** The
   design's central "nearly free" claim survives: `server.py:6938` (Mode B) and
   `:7033` (Mode A) pass no `env=`, so `Popen` inherits `ANTHROPIC_API_KEY`
   (`agent_runtime.py:1752/2193` copy-and-inherit). Preserved as a load-bearing
   invariant in §5.

3. **Three "guarantee" claims in the draft were false/over-stated (Seat 2).**
   (a) "No plaintext at rest in the VM" — false: `POST /api/agent/provider/.../env`
   writes keys plaintext to `provider_env.json` on the volume (`server.py:3008`)
   and *overrides* the injected key (`:3086`). (b) "Injected only at wake, never
   persisted" — undermined by the suspend snapshot. (c) "No key in logs" — the
   agent can dump its own env into transcripts/Scribe. All corrected in §5.

4. **No runaway-compute primitive (Seat 3, w/ Seat 1).** "Reuse the abuse posture"
   is not a mechanism — the existing abuse-prevention is an edge Worker with no
   concept of in-VM compute, and the idle guard that prevents bad suspends becomes
   the very thing that pins a `/loop` runaway awake 24/7 on our compute. Replaced
   in §10.4 with a concrete orchestrator-side awake-budget + continuous-awake
   watchdog (overrides the keep-awake guard, degrades to fire-or-skip).

5. **Storage dominates compute; the $67/mo reuse is a category error (Seat 3).**
   Active compute ≈ $0.55/mo but storage floor $0.75–$7.50/mo per active user;
   1,000 dormant signups ≈ $450–$1,200/mo of dead weight. Archive-and-detach is
   the economic precondition of a free tier (pulled to Phase-1 design / Phase-3
   ship), not Phase-4 polish. The "$67/mo at 50 users" figure is relay-only and
   cannot bound hosted cost. Corrected in §8.

6. **The enrollment fork is real and was unacknowledged (Seat 4).** Hosted
   enrollment-by-provisioning bypasses the entire attestation/device-key spine
   (`EnrollRequest` requires `device_pub_b64`; `_do_enroll_after_auth` provisions
   a tunnel to `localhost:5199`). Declared a deliberate `/v1/hosted/*` fork in
   §7.1, with per-endpoint reuse/replace/shift disposition.

7. **The moat is convenience, not a moat (Seat 4, coupled to Seat 3 pricing).**
   The open core is self-hostable on the user's own Fly account; the one real
   proprietary binding (mc-tunnel's `CLIENT_SECRET_PRIV`) is exactly what hosted
   drops. Defensibility = convenience + data gravity + brand, which *caps* pricing
   power — making the storage/runaway controls more load-bearing. Restated in §7.

### Condition ledger

**Must-fix-in-design (closed inline in v1.1, tagged `[C:S<seat>.<n>]`):**
S1.1 wake-at/after-next_run · S1.2 fire-or-skip semantic + per-occurrence wakes ·
S1.3 idle forbids suspend while `running|idle` · S1.5 two-phase suspend ·
S2.1 disable `provider_env.json` key write in hosted · S2.2 revoke-destroys-snapshot
+ encrypted-at-rest · S2.3 honest "no key in logs" + redaction · S2.4 custody
inversion + incident plan + ToS gate · S3.1 real §8 arithmetic + stop/suspend named ·
S3.2 dormant-storage archive pulled forward · S3.4 egress line + cap · S3.5 runaway
primitive · S3.6 internal-timer semantics + §9 honesty · S3.7 retire $67/mo reuse ·
S4.1 enrollment fork · S4.2 moat honest restatement · S4.3 open/closed boundary ·
S4.4 mobile-tokens shifted semantics · S4.5 two onboarding flows · S4.6 iOS
easier-not-unblocked.

**Must-fix-in-implementation (gate a build phase):** S1.4 idle-state endpoint
(Phase 2) · S2.5 trust model + per-VM non-shared cred + env audit (Phase 0/1) ·
S2.6 exclude tunnel binaries from image (Phase 0) · S3.3 cold-start = VM+MC boot
(Phase 0 measurement).

**Soak-gate (block default-on flip, not the build):** S1.6 prove snapshot-clean
suspend on the production provider · S2.2 snapshot encryption + revoke-destroys
before sleep/wake flips on · S3.5 awake-budget thresholds tuned before any free
tier.

### Ratified (preserve across future changes)

- **BYOK env-inheritance** (Seats 2+4) — no curated `env=` in dispatch; any future
  refactor that adds one silently breaks hosted BYOK.
- **One-VM-per-user onto the single-instance invariant** (all seats) — the right
  isolation posture for arbitrary agent code + a live key, and it keeps the open
  core unchanged (protecting both tenancy and licensing).
- **External-mirror-as-wake-source** (Seat 1) — minimal, low-risk; the in-VM
  scheduler stays untouched; `/api/schedules` already serves `next_run`.
- **Dropping per-VM mc-tunnel for hosted is a net custody *improvement*** (Seat 2)
  — removes the embedded shared `CLIENT_SECRET_PRIV` from a box running arbitrary
  agent code.
- **Heavy reconcile already off the cold-start hot path; `load_projects()` is
  single-tenant per VM** (Seat 3) — genuine wake-latency/boot-cost advantages;
  don't regress them.
- **Two products, two enrollment models, one shared auth substrate is coherent**
  (Seat 4) — BYO keeps mc-tunnel + attestation; hosted simply doesn't need it.

### Disposition

All must-fix-in-design conditions are threaded into §§4–11 above; the doc is now
**v1.1**. No design gate remains: **Phase 0 may proceed** — it is precisely the
spike that resolves the wake-path and unit-economics decisions the committee
routed to it. Implementation-gated and soak-gated conditions attach to their
phases in §11.

---

### Verbatim seat assessments

The four assessments are reproduced verbatim below (also at
`docs/_committee/HOSTED_CLOUD_seat{1..4}_*.md`).

<!-- The seat files are the canonical verbatim record. Read them directly for the
full text with every code citation; the synthesis above captures the actionable
content and is what drives the v1.1 edits. -->

See `docs/_committee/HOSTED_CLOUD_seat1_lifecycle.md`,
`docs/_committee/HOSTED_CLOUD_seat2_custody.md`,
`docs/_committee/HOSTED_CLOUD_seat3_cost.md`,
`docs/_committee/HOSTED_CLOUD_seat4_platform.md`.
