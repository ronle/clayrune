# Mission Control Remote Access — Architecture

**Status:** Draft
**Owner:** Ron Levy
**Last updated:** 2026-04-24

This document describes the end-to-end architecture for letting a Mission Control (MC) user reach their local dashboard from outside their home network. It is the foundation document; protocol details live in `02-attestation-protocol.md`, the control-plane API surface lives in `03-control-plane-api.md`, and abuse controls live in `04-abuse-prevention.md`.

The platform domain is **not yet chosen**. Everywhere the platform domain appears, treat it as a configuration value `PLATFORM_DOMAIN` (placeholder: `example.tld`). A single rename PR should flip the real domain in one commit.

---

## 1. Goals and non-goals

### Goals

- A non-technical user can enable remote access in one click from MC Settings and reach their dashboard from any browser on any device.
- The remote URL is **stable** — same URL forever, across reboots, ISP changes, and MC restarts.
- The user has a **recovery path** that does not require them to remember a URL: signing in at `PLATFORM_DOMAIN` from any device leads them to their dashboard.
- MC-the-operator **never holds user data**: no dashboard contents, no project JSON, no agent transcripts, no hivemind state pass through or get stored on our infrastructure.
- Abuse (using the service to proxy non-MC traffic) is impractical enough that free-tier economics work.
- Any deployed device can be revoked by the operator within one attestation cycle (≤10 minutes).

### Non-goals (v1)

- Cross-user shared state (team workspaces, shared hiveminds).
- Mobile-native apps.
- Offline remote editing / sync.
- Cloud-hosted agent execution.
- End-to-end encryption where the relay cannot see ciphertext — **deferred to v2** (see §9).

---

## 2. High-level architecture

```
                                               ┌─────────────────────────┐
                                               │  User's phone / laptop  │
                                               │  (any browser, anywhere)│
                                               └───────────┬─────────────┘
                                                           │ HTTPS
                                                           ▼
                         ┌────────────────────────────────────────────────┐
                         │  Cloudflare edge                               │
                         │   ─ PLATFORM_DOMAIN wildcard cert              │
                         │   ─ Cloudflare Access (email OTP / Google)     │
                         │   ─ Worker: path allowlist + traffic caps      │
                         │   ─ Cloudflare Tunnel terminator               │
                         └───────────────┬──────────────────┬─────────────┘
                                         │                  │
                    control-plane API    │                  │   tunnel traffic
                    (CP ↔ CF admin)      │                  │   (CF ↔ user PC)
                                         ▼                  ▼
   ┌─────────────────────────────────┐                ┌──────────────────────────┐
   │  Control plane (GCP Cloud Run)  │                │  User's PC               │
   │  api.PLATFORM_DOMAIN            │                │                          │
   │   ─ signup / signin             │                │   ┌──────────────────┐   │
   │   ─ device enrollment           │                │   │  MC (Flask)      │   │
   │   ─ attestation                 │   localhost    │   │  port 5199       │   │
   │   ─ short-lived tunnel tokens   │◄──────────────►│   │                  │   │
   │   ─ device / build registry     │   handshake    │   └──────┬───────────┘   │
   │   ─ Cloudflare API orchestrator │                │          │ spawns        │
   │                                 │                │          ▼               │
   │  Firestore + Secret Manager     │                │   ┌──────────────────┐   │
   │  + Cloud KMS (build key)        │                │   │  mc-tunnel       │   │
   └──────────────┬──────────────────┘                │   │  (Rust binary)   │   │
                  │                                   │   │  cloudflared     │   │
                  │  attestation                      │   │  subprocess      │   │
                  └───────────────────────────────────┼───┤                  │   │
                                                      │   └──────────────────┘   │
                                                      └──────────────────────────┘
```

Four planes of traffic, intentionally separated:

1. **Browser ↔ Cloudflare edge ↔ mc-tunnel ↔ MC** — dashboard traffic. Does not touch our servers.
2. **mc-tunnel ↔ Control plane** — attestation and tunnel-token rotation. Never carries dashboard data.
3. **Browser ↔ Control plane** — signin, account management, "find my dashboard" recovery.
4. **Control plane ↔ Cloudflare API** — provisioning tunnels, DNS records, Access policies on the operator's behalf.

---

## 3. Components

### 3.1 Local MC (`mc_remote/` Python module)

Lives inside the existing MC Flask app. New responsibilities:

- Generate and persist the **device keypair** (Ed25519) in the OS keystore at first enrollment (Windows Credential Manager via `keyring`; macOS Keychain; Linux Secret Service).
- Own the **enrollment token** and `username` after signin.
- Serve the localhost OAuth callback at `/api/mc-callback` during enrollment.
- Spawn and supervise the `mc-tunnel` subprocess.
- Expose a localhost handshake endpoint (`/api/tunnel-challenge`) that proves MC is alive and holds the matching handshake secret.
- Surface remote-access state to the UI: disconnected / enrolling / online / offline / revoked / version-floored.

### 3.2 `mc-tunnel` binary (Rust)

Small static binary shipped inside the MC install. Lives in `mc_tunnel/` (separate Rust crate). Responsibilities:

- Verify parent process: PID must match spawn-parent, parent binary SHA256 must match a hash in the signed build manifest.
- Handshake with MC over localhost using a one-shot secret (passed via env var or stdin from MC at spawn time).
- Build attestation envelopes and send them to the control plane.
- On success, start `cloudflared` as a subprocess with the received short-lived tunnel token.
- Rotate tokens every 10 minutes; reconnect with exponential backoff on failure.
- Forward only to `127.0.0.1:5199` — hardcoded, not configurable.

Language choice rationale: tiny static binary, trivial cross-compile for Windows/macOS/Linux, memory-safe (this code holds keys and talks to the network), mature crypto ecosystem (`ring`, `ed25519-dalek`).

### 3.3 Control plane (`control_plane/`, separate repo eventually)

Cloud Run service (Python FastAPI or Node — TBD in `03-control-plane-api.md`). Stateless compute + Firestore for data. Responsibilities:

- User signup and signin (Firebase Auth as the identity provider).
- Device enrollment: bind `device_pub` to user, issue enrollment token.
- Attestation verification: signature, enrollment token, version floor, build-hash allowlist, nonce freshness, rate limits.
- Short-lived tunnel token issuance (15-minute TTL).
- Cloudflare API orchestration: create/delete tunnels, DNS records, Access policies.
- Device registry: online/offline state, last-seen, revocation.
- Build registry: active/revoked build hashes, minimum supported protocol version.

Explicitly **does not**:

- See, proxy, or store any dashboard traffic.
- Hold symmetric keys for user sessions.
- Have access to user project contents.

### 3.4 Cloudflare edge

Configured by the control plane via Cloudflare API. Pieces in use:

- **Cloudflare Tunnel** — the actual relay. Each user's MC runs a named tunnel; the tunnel UUID is persistent and stored in the device registry.
- **DNS**: wildcard `*.PLATFORM_DOMAIN` points at CF; per-user CNAME `<username>.PLATFORM_DOMAIN` resolves to the tunnel.
- **Cloudflare Access** — sits in front of every `<username>.PLATFORM_DOMAIN`. Policy: email OTP to the address tied to that account, or Google sign-in with matching email.
- **Cloudflare Worker** — runs on every request to `*.PLATFORM_DOMAIN`:
  - Path allowlist (deny anything MC doesn't serve).
  - Response size cap.
  - Rate limit / concurrency / bandwidth cap.
  - Serves the friendly "offline" page when the tunnel reports unreachable.

### 3.5 GCP footprint

- **Cloud Run** — the control plane service. Scales to zero; pay per request.
- **Firestore** — collections: `users`, `devices`, `builds`, `attestation_log` (30-day TTL).
- **Cloud KMS** — holds the build-manifest signing key. Access scoped to the build pipeline service account only.
- **Secret Manager** — holds the Cloudflare API token, Firebase admin credentials.
- **Cloud Logging + Monitoring** — attestation logs, rate-limit hits, revocation events.

Estimated baseline cost: **~$5–15/mo** with no users (Cloud Run scale-to-zero, Firestore free tier, KMS pennies). Scales linearly with attestation volume; dashboard traffic cost is $0 because CF carries it, not us.

---

## 4. Trust model

Four things are trusted:

1. **The signed MC binary** — Windows Authenticode / macOS notarization guarantees the bits on the user's disk are what the operator built. The build pipeline is the root of trust.
2. **The device private key** — generated on-device, stored in OS keystore, never transmitted. Used to sign attestations.
3. **The build-manifest signing key** — held in Cloud KMS, used once per release to sign `build_manifest.json`. Its public half is embedded in every `mc-tunnel` binary.
4. **The control plane's TLS cert** — pinned inside `mc-tunnel` (pinning added once `PLATFORM_DOMAIN` is chosen; v0 uses public CA chain only).

Three things are **not** trusted:

1. **The relay** (Cloudflare). Dashboard TLS is terminated there, so v1 relies on CF confidentiality. This is an accepted risk, documented in TOS, and the v2 E2E encryption design eliminates it (see §9).
2. **The user's local network.** All attestation traffic is mutually authenticated.
3. **The control plane** — compromise does not leak user data, because no user data flows through it. A compromised CP can issue tunnel tokens (impersonation) but cannot read existing traffic or decrypt past sessions.

---

## 5. Data model (Firestore)

```
users/{user_id}
  email_hash:      <sha256 for dedup/lookup; plaintext in Firebase Auth only>
  username:        "ron"
  created_at:      <ts>
  tier:            "free" | "paid"
  risk_score:      <0..100>
  suspended:       bool

devices/{device_id}
  user_id:         <ref>
  device_pub:      <base64 Ed25519 pubkey>
  enrolled_at:     <ts>
  revoked_at:      <ts | null>
  last_seen:       <ts>
  os:              "win32-11" | "darwin-14" | ...
  mc_version:      "1.4.2"
  cf_tunnel_uuid:  <CF's tunnel id>
  hostname_claim:  "<username>.PLATFORM_DOMAIN"
  device_name:     "Ron's Desktop"   # user-editable

builds/{build_manifest_id}
  mc_version:      "1.4.2"
  mc_sha256:       <hex>
  min_protocol:    1
  signed_at:       <ts>
  revoked:         bool
  revoke_reason:   <string | null>

attestation_log/{id}
  device_id:       <ref>
  timestamp:       <ts>
  result:          "ok" | "bad_signature" | "unknown_build" | "rate_limited" | ...
  mc_binary_sha:   <hex>
  ip_hash:         <sha256>
  # 30-day TTL via Firestore TTL policy
```

Dashboard data — projects, agent state, transcripts, hivemind, uploads — is **not present** in any collection. It never leaves the user's PC.

---

## 6. User flows (happy paths)

### 6.1 First enrollment

1. User opens Settings → Remote Access → "Enable".
2. MC generates an Ed25519 device keypair, stores private half in OS keystore.
3. MC opens the system browser to `https://PLATFORM_DOMAIN/connect?device_pub=…&nonce=…&redirect=http://127.0.0.1:5199/api/mc-callback`.
4. User signs in (Google or email OTP via Firebase Auth).
5. User picks a username (validated for uniqueness, DNS-safety, profanity).
6. Control plane creates `users/{user_id}` and `devices/{device_id}`, issues an **enrollment token** (opaque, revocable) bound to `device_pub`.
7. Browser redirects to `http://127.0.0.1:5199/api/mc-callback?enrollment_token=…&username=…`.
8. MC stores token + username in the OS keystore.
9. MC spawns `mc-tunnel`. First attestation succeeds. Dashboard live at `<username>.PLATFORM_DOMAIN`.

### 6.2 Everyday use

1. User boots PC. MC auto-starts (Windows Run key). MC spawns `mc-tunnel`. `mc-tunnel` attests, receives tunnel token, connects to CF. Dashboard live.
2. User, from a café, opens `PLATFORM_DOMAIN` in any browser, signs in, clicks "My dashboard". CF Access gates, then forwards to `<username>.PLATFORM_DOMAIN`. Worker checks path allowlist. Tunnel delivers request to MC. Response streams back.

### 6.3 Reconnect after network blip

- `cloudflared` handles the CF-side reconnect (seconds).
- If the attestation cycle lapses, `mc-tunnel` re-attests before the token expires.

### 6.4 PC offline

- Tunnel disconnects when `mc-tunnel` loses connectivity or MC shuts down.
- CF detects tunnel-unreachable state.
- Worker intercepts requests to `<username>.PLATFORM_DOMAIN` when tunnel is down and serves a friendly offline page (last-seen time, wake instructions, link to account dashboard).

### 6.5 Revocation

Operator action (abuse) or user action (lost laptop) marks the device revoked:

- Next attestation within ≤10 minutes returns a signed revocation directive.
- `mc-tunnel` exits cleanly and signals MC.
- MC shows: "This device was disconnected from remote access. Sign in again to re-enroll."

---

## 7. Failure modes and UX

| Failure | User-visible state | What MC does |
|---|---|---|
| Tunnel client crashes | "Reconnecting…" | supervisor respawns, exponential backoff |
| Attestation fails (network) | "Reconnecting…" | retry |
| Attestation fails (revoked) | "Device disconnected" | stop tunnel, show re-enrollment link |
| Attestation fails (build floor) | "Update required" | link to auto-update / download |
| Username conflict during enrollment | inline error | user retries |
| Control plane unreachable | "Remote access unavailable" | retry; existing tunnel keeps working until token expires |
| Cloudflare outage | "Remote access unavailable" | wait; MC itself is unaffected locally |

Local MC always works regardless of remote-access state. Remote is strictly additive.

---

## 7a. Seamless multi-client invariant

**Invariant:** at any moment there is exactly **one** MC process on the user's PC, owning exactly **one** `agent_sessions` dict, exactly **one** `ProjectAgentManager` registry, and exactly **one** Flask server bound to `127.0.0.1:5199`. *N* simultaneous clients (Tauri webview, mobile browser via tunnel, friend's laptop via tunnel, the local network device on the same WiFi, …) all read and write *the same* MC state.

**Consequences:**

- Switching between clients is **not a migration**. There is no "session" object that moves; clients just re-read the shared state. A user typing on their phone and then continuing on their desktop is reading the same agent transcript from the same `agent_sessions` row.
- An agent the user dispatched from their phone keeps running when they close the phone tab. It's owned by the MC process, not by any client.
- Killing a client (closing a browser, putting the phone down) never affects agents. Killing **MC** does (intentionally — the dashboard is offline if MC is offline).
- Multi-client coordination (e.g. two clients both viewing the same agent stream) is just two SSE listeners on the same `agent_sessions[sid]['process']`. The `_recentlyStoppedSessions` and `_session_owned_by` machinery already handle this correctly today.

**Anti-pattern that breaks the invariant:** running two MC processes simultaneously (e.g. accidentally launching `python server.py` while Tauri's MC is already running). On Windows, SO_REUSEADDR semantics can let the second bind succeed; traffic then splits between two unrelated `agent_sessions` dicts. Symptoms include "session not found" errors, agents that look like they "moved" between terminals, and irrecoverable failures when one instance is killed.

**Defense:** `_check_port_conflict()` in `server.py` is **fatal** on second-instance startup (`sys.exit(2)`) with a clear explanation. Bypass via `MC_ALLOW_PORT_CONFLICT=1` is for protocol-level testing only; users never see this and shouldn't.

**Why this matters for remote access:** the entire design relies on this invariant. The control plane never sees user data because user data never leaves the user's MC. Cloudflared just terminates TLS at the edge and proxies to `127.0.0.1:5199` — the only MC. Any architecture that requires two MCs (e.g. "headless server alongside Tauri UI") needs separate ports or shared-state coordination, neither of which is in v1 scope.

---

## 8. Security properties (summary)

| Property | Mechanism |
|---|---|
| Only signed MC binaries can run tunnels | Parent-process verification + build-manifest hash check in `mc-tunnel` |
| Stolen enrollment tokens are useless | All attestations must be signed by the device private key |
| Replay attacks blocked | Per-attestation nonce from CP + timestamp |
| Account sharing at scale blocked | Rate limits, concurrent-device caps, one-device-online on free tier |
| Old vulnerable builds forced out | Build hash allowlist + minimum protocol version |
| Revocation within 10 min | Short-lived tunnel tokens + attestation cycle |
| Control plane compromise does not leak user data | No user data ever flows through CP |
| Relay compromise (v1) | Accepted risk; disclosed in TOS. v2 E2E encryption closes this. |

Details and attack matrix in `02-attestation-protocol.md`.

---

## 9. Known gaps / v2 roadmap

- **App-layer E2E encryption** between browser and MC, where the relay sees only ciphertext. Requires a session-key exchange bootstrapped by `device_pub` at enrollment and a browser-side decryption shim served from `PLATFORM_DOMAIN` (so the code that can see plaintext is same-origin with the account, not the tunneled app).
- **Cert pinning** for the control plane inside `mc-tunnel` — deferred until the real domain is chosen.
- **Build-key HSM** — v1 uses Cloud KMS with tight IAM; migrate to dedicated HSM when paid tier goes live.
- **Traffic-shape fingerprinting** at the Worker (detect non-MC traffic patterns) — deferred until abuse patterns emerge.
- **Risk scoring** — deferred until we have enough signal to tune thresholds.
- **Multi-device / team features** — out of v1 scope.
- **Mobile wake-up flow** (send a WoL packet to user's router) — out of v1 scope.

---

## 10. Parameterization: what changes when the domain is picked

Only the following string values change. Everything else in the codebase is domain-agnostic.

```
mc_remote/config.py:
  PLATFORM_DOMAIN          = "example.tld"
  CONTROL_PLANE_HOST       = "api.example.tld"
  MARKETING_HOST           = "example.tld"
  SUPPORT_EMAIL            = "support@example.tld"
  CONTROL_PLANE_CERT_PIN   = None            # set post-launch

static/js/remote_config.js:
  PLATFORM_DOMAIN          = "example.tld"

control_plane config:
  PLATFORM_DOMAIN          = "example.tld"
  CLOUDFLARE_ZONE_ID       = "<set at launch>"
  CLOUDFLARE_ACCOUNT_ID    = "<set at launch>"
```

Build manifest, attestation envelope, and protocol schemas contain **no** domain strings — they only reference a `control_plane_pubkey` identifier.

---

## 11. Open questions

1. **Username policy** — reserved words list, allowed characters, length, change-allowed-N-times-per-year.
2. **Free-tier limits** — exact values for bandwidth / concurrency / device count.
3. **Account deletion** — cascade rules, grace period, whether usernames become available again.
4. **Support channel** — email, Discord, in-app? Ties to TOS and privacy policy.
5. **Which Cloud Run region(s)** — single-region is fine for v1; latency matters for attestation only (user traffic goes through CF edge).
6. **Whether `mc-tunnel` should embed cloudflared or call a system-installed one** — leaning embed for version control and signature stability.

Answers for these land in subsequent docs.

---

## 12. Dependencies between design docs

```
01-architecture.md          (this doc — foundation)
    │
    ├──► 02-attestation-protocol.md    (wire formats, handshake, envelopes)
    │         │
    │         └──► 03-control-plane-api.md   (OpenAPI, endpoints, schemas)
    │
    ├──► 04-abuse-prevention.md        (layered controls, Worker logic)
    │
    ├──► 05-build-pipeline.md          (signing, manifest emission)
    │
    └──► 06-rollout-plan.md            (milestones, v1 scope cut, launch order)
```

Next up: `02-attestation-protocol.md`.
