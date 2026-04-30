# Mission Control Remote Access — Rollout Plan

**Status:** Draft
**Owner:** Ron Levy
**Last updated:** 2026-04-27
**Depends on:** all of `01–05` + `error_codes.md`

This document is the milestone-by-milestone plan from "design docs frozen" through "first 50 active remote-access users." It assumes the descoped v1 (per `feedback_no_paid_code_signing.md`): no install flow, no code-signing, already-running MC instances on user PCs.

---

## 1. Goals & non-goals for v1

**v1 done = a non-technical user can:**
1. Click "Enable Remote Access" inside an already-running MC.
2. Sign in with email OTP at `clayrune.io` in their browser.
3. Pick a username; their MC becomes reachable at `<username>.clayrune.io`.
4. Open that URL from any browser (phone, friend's laptop, hotel PC), sign in, see their dashboard.
5. Reach +50% of MC's local features remotely (the read-only-from-mobile path; agent dispatch and terminal pop-out can be deferred features).
6. Disconnect a device from a settings page if needed.

**v1 explicitly does not include:**
- Install flow / installer / SmartScreen handling.
- Code-signing (any tier).
- Auto-update.
- macOS or Linux releases.
- Paid tiers / Stripe billing.
- Agent transcripts streaming end-to-end-encrypted (v1 trusts CF TLS only — see `04-` §7).
- Operator dashboard (Cloud Logging + Firestore queries are sufficient for <50 users).
- Native mobile apps (browser-only).
- Microsoft Store distribution.
- Per-binary attestation hash check (`05-` §1 — descoped).

**Success metrics:**
- Time from "click Enable Remote Access" to "see dashboard from a different device" ≤ 5 minutes for a non-technical user.
- Tunnel uptime ≥ 99% over 30 days, modulo user PC offline.
- Zero confirmed abuse incidents in the first 90 days (or one incident with a recovered post-mortem).
- Operator on-call burden < 5 hours/week.

---

## 2. Dependencies (acquire before M0)

External accounts and resources, in order of how long they take to procure:

| Item | Lead time | Cost | Notes |
|---|---|---|---|
| `clayrune.io` DNS pointed at Cloudflare | minutes | already owned | Set NS records at registrar |
| Cloudflare account | minutes | free | Pro/Enterprise not needed for v1 |
| GCP project | minutes | free tier | Project: `clayrune-prod` (and `clayrune-staging`) |
| Firebase Auth project | minutes | free | Linked to the GCP project |
| Cloud Run + Firestore + Memorystore enabled | minutes | usage-based | Memorystore Basic 1GB ≈ $35/mo — only one needed; consider deferring (see §13.2) |
| Cloudflare API token (zone + tunnel + Access scopes) | minutes | free | Scoped to `clayrune.io` zone |
| Workload Identity Federation (GitHub → GCP) | hour | free | Lets CI auth without keys |
| GitHub Actions | already exists | free tier | |
| Email transactional sender (for OTP) | hour | free tier (Firebase) | Firebase Auth handles email-link out of the box |
| Sentry / error reporting | optional | free tier | |
| Stripe account (deferred) | days | free | Only needed when paid tier launches |

**Domain decisions (from `project_remote_access_domains.md`):**
- `clayrune.io` — primary user-facing (`<username>.clayrune.io`, `api.clayrune.io`, `app.clayrune.io`)
- `clayrune.com`, `clayrune.dev`, `clayrune.ai` — defensive registrations; Cloudflare redirects to `.io`

---

## 3. Milestone overview

```
M0  Foundations           — accounts, DNS, KMS, CI scaffolding             (week 1)
M1  Control plane stub    — Firebase Auth, /v1/health, /v1/enroll          (week 2-3)
M2  mc-tunnel + MC wiring — Settings UI, enrollment, first attestation     (week 4-5)
M3  Happy path E2E        — dogfooding on operator's own MC                (week 5)
M4  Abuse prevention v1   — Worker, path allowlist, traffic caps           (week 6)
M5  Private beta          — 5–10 trusted users, real-world signal          (week 7-8)
M6  Public alpha          — open signup, 50 users cap, full monitoring     (week 9+)
```

Calendar weeks. Solo execution. Slip is expected; the plan is intentionally not loaded with stretch goals.

---

## 4. M0 — Foundations (week 1)

**Deliverable:** infra bootstrapped, CI can deploy a "hello world" to Cloud Run and a static page to CF Pages.

### Tasks

- [ ] Move `clayrune.io` DNS to Cloudflare (registrar nameserver change)
- [ ] Set up `clayrune-prod` and `clayrune-staging` GCP projects
- [ ] Enable: Cloud Run, Firestore (Native mode), Cloud KMS, Secret Manager, Cloud Logging
- [ ] Create Firebase Auth tenant in each project; enable Email Link sign-in
- [ ] Provision GitHub Actions Workload Identity Federation
- [ ] Reserve `api.clayrune.io` and `app.clayrune.io` (CNAMEs to placeholder)
- [ ] Create CF API token (zone-scoped: tunnel CRUD, DNS CRUD, Access CRUD)
- [ ] Stash CF token in GCP Secret Manager
- [ ] Scaffold `control_plane/` repo (or directory in main repo) with FastAPI + uvicorn skeleton; `/v1/health` returns `{ ok: true }`
- [ ] Wire Cloud Build → Cloud Run deploy on push to `main`
- [ ] Sanity check: `curl https://api-staging.clayrune.io/v1/health` returns 200

### Exit criteria
- `curl https://api.clayrune.io/v1/health` and the staging equivalent both return 200.
- Operator can deploy a code change to staging in < 5 minutes.
- All secrets in GCP Secret Manager, none in repos.

### Risks
- DNS propagation can be slow (up to 48h on first NS change). Plan around it.
- Workload Identity Federation has a finicky setup; budget half a day.

---

## 5. M1 — Control plane stub (week 2–3)

**Deliverable:** working enrollment flow against staging. No `mc-tunnel` yet — operator can hit endpoints with `curl` and Firebase ID tokens.

### Tasks

#### Backend
- [ ] Implement `POST /v1/signin/start` and `POST /v1/signin/complete` (thin Firebase Auth wrappers — Firebase does the heavy lifting via email link)
- [ ] Implement `GET /v1/connect` (HTML signin page; CSRF nonce → `enrollment_intents`)
- [ ] Implement `POST /v1/enroll`:
  - Validate Firebase ID token
  - Burn `enrollment_intents` row
  - Username uniqueness check (Firestore transaction)
  - Cloudflare orchestration: create tunnel → create DNS → create Access app
  - Issue + persist `enrollment_token` (hash only)
  - Return enrollment response
- [ ] Implement `GET /v1/account`, `GET /v1/devices`, `POST /v1/devices/{id}/revoke`
- [ ] `/v1/admin/versions` (renamed from `/builds`, per `05-` §4) — register a `mc_version` allowlist entry
- [ ] Reserved-username blocklist (admin, api, app, mc, claude, anthropic, support, help, www, ftp, mail, root, admin, ron — short list to start)

#### Auth
- [ ] Firebase Admin SDK integration; verify ID token, audience, expiry
- [ ] Operator JWT (Google IAP) for admin endpoints; ingress IP allowlist for `/v1/admin/*`

#### Schema
- [ ] Firestore collections: `users`, `devices`, `versions`, `attestation_log` (TTL=30d), `enrollment_intents` (TTL=15m)
- [ ] Indexes: `users.username` (unique), `devices.device_pub_b64` (unique), `devices.user_id`, `versions.mc_version`

#### Test rig
- [ ] Manual `curl` script: `signin/start` → email-link → `signin/complete` → `/v1/connect` → `/v1/enroll` against staging
- [ ] Verify the CF tunnel actually appears in CF dashboard
- [ ] Verify `<username>.clayrune.io` resolves and shows a friendly "tunnel offline" page

### Exit criteria
- Operator can run the curl script and end up with a `<username>.clayrune.io` hostname provisioned in CF.
- Visiting `<username>.clayrune.io` while no `mc-tunnel` is running shows a friendly offline page (CF returns its default 502 OR our Worker — for M1 the CF default is acceptable).
- `GET /v1/devices` returns the freshly enrolled device.
- `POST /v1/devices/{id}/revoke` deletes the CF tunnel + DNS + Access app and invalidates the device.

### Risks
- CF API rate limits during testing (1200 req / 5 min). Run cleanup between iterations.
- Firestore transaction logic for username allocation has a known race shape — write the test for it first.
- Email-link signin requires correctly setting up the action URL in Firebase Console; easy to misconfigure.

---

## 6. M2 — `mc-tunnel` + MC wiring (week 4–5)

**Deliverable:** running MC can enroll itself; outbound tunnel is established; `<username>.clayrune.io` shows the dashboard from another device on the same WiFi.

### Tasks

#### `mc-tunnel` Rust crate
- [ ] Crate skeleton (`mc_tunnel/Cargo.toml`, `src/main.rs`, deps: `tokio`, `reqwest`, `ring` or `ed25519-dalek`, `serde`, `serde_json`, `tracing`)
- [ ] Argument parsing: `--mc-pid`, `--mc-port`
- [ ] Stdin handshake-secret read; env-var fallback
- [ ] Localhost handshake to MC (`/api/tunnel-handshake`); 401-handling, retry/backoff
- [ ] Attestation envelope build + Ed25519 sign (canonical JSON via `serde_jcs` or hand-rolled)
- [ ] `/v1/nonce` and `/v1/attest` HTTP clients with TLS validation against public CA chain (pinning deferred)
- [ ] Cloudflared subprocess management: download/embed `cloudflared`, start with the issued tunnel token, monitor stdout/stderr, restart on exit
- [ ] Token rotation loop (every 10 min)
- [ ] Directive handling: `force_logout`, `update_required`, `notify_user`, `pause`
- [ ] Graceful shutdown on SIGTERM / parent process death

#### MC Python integration (`mc_remote/`)
- [ ] `mc_remote/config.py` — `PLATFORM_DOMAIN = "clayrune.io"` and friends, env-var override
- [ ] `mc_remote/device_keys.py` — Ed25519 keypair generation, OS keystore I/O via `keyring` library (Windows Credential Manager)
- [ ] `mc_remote/enrollment.py` — open browser to `/v1/connect`, run a short-lived HTTP listener on `/api/mc-callback` to receive the redirect, persist token+username to keystore
- [ ] `mc_remote/tunnel_supervisor.py` — spawn/monitor `mc-tunnel` subprocess; restart on crash; surface status to MC frontend
- [ ] Server endpoints: `GET /api/tunnel-handshake`, `GET /api/mc-callback`, `GET /api/remote/status`, `POST /api/remote/enable`, `POST /api/remote/disable`, `POST /api/remote/disconnect-this-device`

#### Frontend
- [ ] Settings → Remote Access panel (replaces the current placeholder, if any):
  - Disabled state with "Enable Remote Access" button
  - Enrollment state (browser flow in progress)
  - Enrolled state: show username, hostname, copy-link button, "Disconnect this PC" button
  - Live tunnel status badge (online / reconnecting / offline)
  - Bandwidth / quota meter (read from latest attestation response caps)
  - Error states keyed off `error_codes.md`

#### Bundling
- [ ] Update `build.spec` (or PyInstaller config) to include `mc-tunnel.exe` in the dist directory
- [ ] Update `BUILD_INSTRUCTIONS.md` with the cargo-build step

### Exit criteria
- Operator's MC, started fresh, can run through enrollment without touching curl.
- Within 30 seconds of "Enable Remote Access," `<username>.clayrune.io` returns the dashboard from a different device on the same network.
- Stopping MC results in `<username>.clayrune.io` showing offline within 30 seconds.
- Restarting MC reconnects within 30 seconds without re-enrollment.
- `Disconnect this PC` revokes cleanly; subsequent attempts to reach the URL return offline; re-enabling generates a fresh enrollment.

### Risks
- `mc-tunnel` is the largest new code surface — Rust + crypto + subprocess management. Budget liberally; this is where slip is most likely.
- Bundling `cloudflared` (~30 MB binary) inflates MC's release size. Mitigation: download on first remote-access enable, cache in MC data dir. Adds latency on first use but keeps installer light.
- Windows Credential Manager via the `keyring` library has known quirks under PyInstaller's frozen-app mode. Test early; fall back to mode-0600 file in MC's data dir if needed.
- Localhost callback on enrollment requires MC to listen on a fixed port. If user has another instance / process on `:5199` it'll fail. Document; show a clear error.

---

## 7. M3 — Happy path E2E (week 5, overlapping M2)

**Deliverable:** dogfooding. Operator uses remote access daily for one week. Bugs surfaced, ranked, fixed.

### Tasks
- [ ] Operator (Ron) enrolls personal MC against staging
- [ ] Use `<username>.clayrune.io` from phone for one full work week
- [ ] Track every bug + every UX papercut in a flat list (no triage yet)
- [ ] At end of week, sort by severity; fix all P0/P1 before M4
- [ ] Add E2E test: spin up a Windows VM, install MC, enroll, hit the URL from a different device, verify dashboard loads. Run nightly in CI (manual is fine for v1).

### Exit criteria
- One week of operator daily-use without unrecoverable failures.
- Mean time-to-reconnect after PC sleep + wake < 30 seconds.
- No "session not found" type errors that require restart.

### What "good enough" looks like
- Refresh on the phone reliably reloads the dashboard.
- Dispatching an agent from the phone arrives in MC and produces output (even if styling on phone is rough).
- Operator stops manually checking whether the tunnel is up.

---

## 8. M4 — Abuse prevention v1 (week 6)

**Deliverable:** the Worker from `04-abuse-prevention.md` deployed; basic caps enforced.

### Tasks

- [ ] Cloudflare Worker scaffold (Wrangler, deployed to all routes matching `*.clayrune.io`)
- [ ] User lookup: hostname → `users/{user_id}` via Workers KV (CP writes on enrollment / suspend / username change)
- [ ] Path allowlist (per `04-` §2 Layer 2, regex sourced from MC route table)
- [ ] Method allowlist + CONNECT/Upgrade rejection
- [ ] Header/body size caps
- [ ] Per-user request rate limiter (Durable Object, sliding window)
- [ ] Bandwidth metering: Worker pushes byte counts to control plane every 60s, CP aggregates into `users.bandwidth_used_period_bytes`
- [ ] Free-tier defaults baked in: 5 GiB/mo, 60 rps, 20 concurrent, 10 MiB response, 64 MiB request, 6h WS sessions
- [ ] Friendly offline page (custom HTML when tunnel is down or unknown_hostname)
- [ ] Manual cap-override endpoint: `PATCH /v1/admin/users/{id}/caps` — operator can extend a user's caps if needed

### Defer to v2
- Risk-score job (Layer 5 in `04-`)
- Step-up auth flow (Layer 4)
- Stripe card pre-auth
- Fingerprint sampling
- Reserved-words / brand-similarity username detection beyond the basic blocklist
- Operator dashboard (use Firestore queries + Cloud Logging until volume justifies)

### Exit criteria
- Worker is in front of every `<user>.clayrune.io` request.
- Path-allowlist denies `/admin`, `/anything-else` with 404 `path_not_allowed`.
- Sustained 100 rps from a test client gets rate-limited (429) within seconds.
- Bandwidth quota counter visible in operator's view of own user record; matches actual traffic ±5%.

### Risks
- Workers KV propagation delay (~60s) on cap changes — acceptable for v1 (revocation already has 10-min window via attestation).
- Durable Object eventual-consistency edge cases. Use the official rate-limiter recipe; don't roll bespoke.
- Forgot-to-allowlist-a-path bug. Mitigate by sourcing the allowlist from MC's actual `app.url_map` and committing the generated regex.

---

## 9. M5 — Private beta (week 7–8)

**Deliverable:** 5–10 trusted users (existing MC users you know personally) running on remote access in production.

### Pre-flight checklist
- [ ] All control-plane endpoints behind production Cloud Run with autoscaling configured (min 1 instance)
- [ ] Cloud Logging retention 30d, Firestore TTL policies live
- [ ] Operator JWT setup (IAP) live; ad-hoc `gcloud` access for emergencies
- [ ] CHANGELOG.md entry: "Remote access (private beta) — Settings → Remote Access. Invite-only."
- [ ] Invite list: 5–10 people; brief one-pager email with username-picking expectations + a Discord/Slack channel for feedback
- [ ] Disable open signup (`/v1/signin/start` 403s for emails not on an allowlist; allowlist managed via admin endpoint)

### Cadence
- Daily check-in for first week (Cloud Logging, attestation_log, user-reported issues)
- Weekly dogfood-feedback sync (asynchronous, written)
- Hard rule: any P0 bug ships a fix within 24h or remote access is paused for everyone via `/v1/admin/maintenance`

### Exit criteria for moving to M6
- ≥ 7 days where no P0 bug surfaced
- ≥ 3 of the beta users have used remote access from outside their own home network
- No abuse incidents
- Operator burden (support replies + investigation) < 5 hr/week
- One full revocation tested in production (Disconnect this PC + re-enable on a real user)

---

## 10. M6 — Public alpha (week 9+)

**Deliverable:** open signup with a 50-user cap. CHANGELOG announcement; small public mention.

### Tasks
- [ ] Remove email allowlist (or set very permissive default)
- [ ] User cap: hard limit at 50 (server returns `quota_exceeded` on signup attempt 51); waitlist via Firebase Auth's allowlist toggle when full
- [ ] Public-facing landing page on `clayrune.io` (single static HTML page; describes what it is, who it's for)
- [ ] Privacy policy + TOS published (drafted from `04-` §7 + §8 — boilerplate for v1, refined later)
- [ ] Operator dashboard scaffold (a single Streamlit / Gradio page over Firestore queries — sufficient for 50 users)
- [ ] Status page (statuspage.io free tier or a CF Worker reading `/v1/health`)
- [ ] Support email aliased to operator's inbox: `support@clayrune.io`

### Hard rules during alpha
- Any user can be revoked at operator discretion if abuse suspected
- Operator commits to publishing post-mortems for any user-visible incident > 30 minutes
- v1 is free; no paid tier yet

### Exit criteria for "M6 over, plan M7"
- 50 users hit
- < 1% weekly attestation failure rate (excluding PC offline)
- Cost of running stays < $50/mo (see §13)
- No more than one P1 bug reported in the most recent week
- Two abuse incidents handled cleanly via existing tooling, OR zero abuse incidents

When M6 stable: plan paid tier, mac/linux, install flow, code-signing in a fresh planning round.

---

## 11. Risk register

Ordered by likelihood × impact.

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| 1 | `mc-tunnel` Rust development eats more time than budgeted | High | High | Start with the smallest possible Rust binary that works (just attestation + cloudflared subprocess); resist feature creep until working |
| 2 | Cloudflared subprocess instability | Medium | High | Run cloudflared with auto-restart; monitor exit codes; have a "report a bug" button that captures the last 100 lines of cloudflared stderr |
| 3 | A user finds a path past the path-allowlist that lets them serve arbitrary files (Layer 2 in `04-`) | Medium | Medium | Source allowlist from MC's route table; review on every release; the `/data/uploads/.+` content-type tightening (per `04-` §9.3) is a known action item |
| 4 | Privacy concern from a user about CF seeing their dashboard traffic | Medium | Medium | Be transparent in TOS; ship E2E encryption in v2; for v1, argue parity with all CF-fronted SaaS |
| 5 | DNS issues at registrar / CF make `*.clayrune.io` flaky | Low | High | Test DNS during M0; have a documented manual override path |
| 6 | Firebase Auth pricing surprise above free tier | Low | Medium | Free tier supports 50k MAU; v1 alpha is 50 users; not a concern until much later |
| 7 | A beta user runs MC on a corporate laptop and triggers their security team | Medium | Medium | Document in TOS that remote access opens an outbound tunnel; make it clearly opt-in |
| 8 | Operator (you) burns out | Medium | High | Strict cadence; private beta is bounded at 10 users; don't widen until private beta is stable |
| 9 | A second person signs up for the same `username` race | Low | Low | Firestore transaction in `/v1/enroll`; tested in M1 |
| 10 | Cloud Run cold start latency on `/v1/attest` causes user-visible reconnect delays | Medium | Low | Set `--min-instances=1` in production; ~$15/mo extra |

---

## 12. Decision points (kill criteria)

Hard checkpoints. If a decision point fails, the project pauses or pivots — it doesn't quietly slide.

| When | Question | If "no" → |
|---|---|---|
| End of M2 | Can the operator personally enroll their MC and get a working remote dashboard? | Pause; root-cause whichever component is broken; do not start M3 |
| End of M3 | Did dogfooding produce more than three P0 bugs in one week? | Stop M4 work; spend an extra week stabilizing |
| End of M5 | Can a non-operator user enroll without operator help? | Either improve the flow OR scope down v1 to "operator-assisted enrollment only" |
| End of M5 | Operator burden > 5 hr/week support? | Don't open signup at M6; first reduce burden |
| Anytime in M5/M6 | A confirmed abuse incident causes platform-level harm (CF account warning, hosting takedown notice, etc.) | Pause new signups; complete the post-mortem; harden before reopening |
| Anytime | Total monthly run cost > $200 with < 50 users | Re-investigate Memorystore, Cloud Run min-instances, KV usage; identify the leak |

---

## 13. Cost projection

### 13.1 Steady-state v1 (M6-stable, 50 users)

| Service | Monthly | Notes |
|---|---|---|
| Cloud Run (control plane + workers) | ~$10 | min-instances=1 in prod, 0 in staging |
| Firestore reads/writes | ~$2 | Mostly attestation_log writes (50 users × 6 attestations/hour ≈ 200k writes/mo) |
| Cloud KMS | ~$0.10 | Practically free |
| Memorystore Redis (nonces, rate limits) | ~$35 | Smallest Basic tier; biggest line item |
| Cloud Logging retention 30d | ~$5 | |
| Workers + KV + Durable Objects | ~$5 | CF free tier covers most of this |
| Bandwidth (Cloudflare → users) | $0 | CF free tier |
| Firebase Auth | $0 | < 50k MAU |
| Domain renewals (4 TLDs) | ~$10 | amortized monthly |
| **Total** | **~$67/mo** | |

### 13.2 Memorystore alternative

Memorystore Basic 1GB at $35/mo is the largest single cost. For < 50 users, consider:

- **Use Firestore for nonces** with a TTL field. Eventual consistency means small risk of replay within sub-second windows; mitigated by short TTL (30s) and the timestamp-skew check (60s window). Acceptable for v1.
- **Use Firestore for rate-limit counters** with sharded counters pattern. Higher write cost per request but no fixed monthly fee.

If Memorystore is dropped: total ~$32/mo. If kept: ~$67/mo. Recommend dropping Memorystore in M0; revisit when the volume justifies it.

### 13.3 What scales the cost

- **Bandwidth** is free-on-CF up to "reasonable" levels (CF historically tolerates gigabytes-per-user for free-tier; abusive spikes get noticed).
- **Cloud Run** scales mostly with attestation volume. 6 attestations/hour/device × N devices is the dominant write workload.
- **Firestore writes** are the second-largest line. Compaction-friendly schema (TTL collections, batched writes) keeps this in line.
- **Cloud Logging** if not retention-capped will surprise. Set 30d hard.

---

## 14. Deferred to post-v1 (when M6 is stable)

In rough priority order:

1. **Install flow + code-signing.** Picks back up `05-build-pipeline.md` original draft; restores binary attestation hash check.
2. **Stripe paid tier.** Card pre-auth as the abuse killer (Layer 4 in `04-`); 100 GiB/mo + 100 concurrent on paid.
3. **macOS support.** Notarization, Mac OS keystore (Keychain), Apple Developer cert.
4. **Linux support** (probably AppImage).
5. **End-to-end encryption** between browser and MC. Removes "CF can see dashboard traffic" from the trust model.
6. **Native mobile apps.** Wraps the dashboard in a Tauri-Mobile / Capacitor shell.
7. **Operator dashboard** (real one, not Streamlit). Multi-user audit log review, abuse signal triage.
8. **Auto-update** for `mc-tunnel` independently of MC.
9. **Microsoft Store** distribution path.

---

## 15. Open questions to resolve before M0 starts

1. **Single repo or split repo for control plane?** Recommendation: keep in `mission-control/control_plane/` for v1; split into its own repo if/when CI surface diverges meaningfully.
2. ~~**`mc-tunnel` written by you in Rust, or use cloudflared directly?**~~ **Resolved 2026-04-27 (open-core decision):** keep the Rust `mc-tunnel` binary. With MC core going open source, the Rust binary becomes the closed-source platform-binding component — it carries the baked-in `CLIENT_SECRET_PRIV` (`02-` §3.6) that distinguishes "real platform install" from "fork that re-implemented the protocol." This restores the ~2 weeks of Rust work to the M2 budget but is load-bearing security work, not throwaway. See `07-licensing.md` and `feedback_no_paid_code_signing.md`.
3. **TOS jurisdiction.** Defer until paid tier launches; use a simple "as-is, no warranty" boilerplate for v1.
4. **Pricing of paid tier.** Out of v1; don't pre-commit.
5. **What's the public face of the project?** `clayrune.io` landing page copy needs a name decision (is the product "Clayrune" or "Mission Control"? Probably MC, with Clayrune as the platform/company name).

---

## 16. Cross-references

- Architecture context: `01-architecture.md`
- Wire formats and verification: `02-attestation-protocol.md` (updated for open-core: §3.6 client secret, §7 dual-signature envelope)
- API surface: `03-control-plane-api.md` + `control_plane/api_spec.yaml`
- Edge defense: `04-abuse-prevention.md`
- Error codebook: `error_codes.md`
- Build details: `05-build-pipeline.md` (open-core: two streams, two licenses)
- Licensing model: `07-licensing.md`
- Scope decision (open-core, no code-signing): `feedback_no_paid_code_signing.md` (memory)

---

## 17. What to do tomorrow

Concrete first actions to begin M0:

1. Move `clayrune.io` DNS to Cloudflare.
2. Create `clayrune-prod` and `clayrune-staging` GCP projects.
3. ~~Decide §15.2~~ — already resolved (open-core, keep Rust `mc-tunnel`).
4. Set up the second (private) repo `mc-remote/` for the proprietary Stream B code (or use a private subdirectory in `mission-control/` with a separate `LICENSE.proprietary` for v1 — split out when CI surface justifies).
5. Start the "Settings → Remote Access" panel as a disabled stub in MC's frontend (item already on the planned-todos list) — surface the button now, wire it up later. Lets you iterate on copy and UX while infra is bootstrapped.
6. Stand up `clayrune.io` and `api.clayrune.io` as placeholder pages so the operator can curl `/v1/health` against staging by end of week 1.
