# Mission Control Remote Access — Attestation Protocol

**Status:** Draft
**Owner:** Ron Levy
**Last updated:** 2026-04-27
**Depends on:** `01-architecture.md`
**Feeds into:** `03-control-plane-api.md`

This document specifies the wire formats, handshake sequences, and verification logic that bind a `mc-tunnel` instance to a genuine, unmodified MC install and to a single enrolled user. It is the security core: every other piece of the remote-access design either depends on these guarantees or relaxes constraints that this document tightens.

---

## 1. Protocol versioning

All envelopes carry an explicit `proto: 1` field. The control plane refuses any envelope whose `proto` is below the registered build's `min_protocol`. Bumps to `proto` are reserved for backwards-incompatible changes; additive fields do not bump the version.

A connected device that receives a `version_floor_exceeded` error from the control plane stops attesting and surfaces "Update required" to the user. Auto-update kicks in if enabled.

---

## 2. Cryptographic primitives

Fixed across the protocol; not negotiable per session.

| Use | Algorithm |
|---|---|
| Device identity | Ed25519 |
| Build manifest signature | Ed25519 (key in Cloud KMS) |
| Release binary signature | Authenticode (Windows) / notarytool (macOS) — separate trust chain |
| Hashing | SHA-256 |
| Random nonces | 32 bytes from CSPRNG |
| Encoding | Canonical JSON (RFC 8785, JCS) for any payload that gets signed |
| Transport | TLS 1.3, HTTPS only, control-plane cert pinned (post-launch) |

Why Ed25519: small keys (32 bytes), small signatures (64 bytes), constant-time, no parameter choices that can be wrong, mature libraries on all target platforms (`ring` in Rust, `cryptography` in Python).

Why JCS for canonicalization: any signed JSON must serialize byte-identically across implementations. JCS is RFC-tracked, deterministic, and supported by `rfc8785` (Python) and `serde_jcs` (Rust).

---

## 3. Keys

Five key materials, with distinct lifecycles.

### 3.1 Release signing key (operator)

- **Holder:** operator (offline cold storage, e.g. YubiKey).
- **Use:** Authenticode / notarytool signs every MC binary release.
- **Rotation:** annually or on suspected compromise.
- **Embedded in `mc-tunnel`:** no — handled by OS code-signing chain.

### 3.2 Build attestation key (operator)

- **Holder:** Cloud KMS, accessible only by the build pipeline service account.
- **Use:** signs `build_manifest.json` once per release.
- **Rotation:** every 6 months. Old keys remain valid until explicit revocation; `build_manifest.json` carries `signing_key_id`.
- **Embedded in `mc-tunnel`:** yes, as a small **set** of allowed pubkeys (not just one) so rotation doesn't require a `mc-tunnel` rebuild for already-shipped binaries to keep working.

```rust
const TRUSTED_BUILD_KEYS: &[(&str, [u8; 32])] = &[
    ("build-key-2026a", [/* 32 bytes */]),
    ("build-key-2026b", [/* 32 bytes */]),  // future rotation slot, populated on next release
];
```

### 3.3 Control plane TLS pin

- **Holder:** the platform's TLS termination (Cloud Run + GCP-managed cert, or Cloudflare in front).
- **Use:** `mc-tunnel` validates `api.PLATFORM_DOMAIN` cert against pinned SPKI hash.
- **Rotation:** pin **two** SPKIs at all times (current + next); rotate the cert by deploying with the second SPKI active, then update the pin set on next `mc-tunnel` release.
- **Embedded in `mc-tunnel`:** yes, when domain is chosen. Until then, validate against public CA chain only.

### 3.4 Device keypair (per install)

- **Holder:** user's PC. Private key in OS keystore (`keyring` library: Windows Credential Manager / macOS Keychain / Linux Secret Service). Public key sent to control plane at enrollment.
- **Use:** signs every attestation envelope from this device.
- **Rotation:** none under normal operation. Rotate on user request ("re-enroll this device") or on detected compromise.
- **Storage path:** `keyring` service name `mission-control-remote`, keys `device_priv_pem`, `device_pub_b64`, `enrollment_token`, `username`.

### 3.5 Session/handshake secrets

- **Handshake secret** (MC ↔ `mc-tunnel`, localhost): 32 random bytes, regenerated every MC start, passed to `mc-tunnel` at spawn time, used once.
- **Tunnel token** (CP → `mc-tunnel`): short-lived (15 min) opaque credential. Format is Cloudflare-specific; the protocol treats it as a black box bearer token.

### 3.6 Client secret (open-core moat)

- **Holder:** baked into the proprietary `mc-tunnel` binary at build time. Each release embeds an Ed25519 keypair; the **private** half is compiled into the binary, the **public** half is registered with the control plane.
- **Use:** signs every attestation envelope alongside the device key. Proves "this attestation came from a real platform-issued `mc-tunnel`, not from a fork that re-implemented the protocol."
- **Rotation:** every `mc-tunnel` release ships a new keypair. The control plane keeps the last N (≥3) public keys in an active set so deployed-but-not-yet-updated clients still attest successfully.
- **Threat model:** an adversary who reverse-engineers a released `mc-tunnel` and extracts the private half can forge `client_signature` until that key rotates out of the active set (typical lifetime: 60–180 days). Mitigation: ship binaries stripped of symbols, minimize exploitable surface area, treat extraction as an inevitable nuisance (not a catastrophe — server-side enforcement still bounds damage). When extraction is observed, accelerate rotation and revoke the affected key.
- **License coupling:** this is what makes the open-core split meaningful. MC core can be open source freely; the closed-source `mc-tunnel` is what claims authority to use `*.PLATFORM_DOMAIN`. See `07-licensing.md`.

```rust
// inside the proprietary mc-tunnel crate
const CLIENT_SECRET_KEY_ID: &str = "mc-tunnel-2026a";
const CLIENT_SECRET_PRIV: [u8; 32] = [/* 32 bytes — REGENERATED EVERY RELEASE */];
```

The control plane stores the corresponding pubkeys in a `client_secret_keys` collection: `{ key_id, pubkey, released_at, revoked_at? }`. The active set is the most recent 3 non-revoked entries.

---

## 4. Build manifest

Emitted by the build pipeline (see `05-build-pipeline.md`), shipped alongside the MC binary at a known path. Read by `mc-tunnel` at startup.

### 4.1 Schema

```json
{
  "proto": 1,
  "build_manifest_id": "2026-04-27-build-714",
  "mc_version": "1.4.2",
  "mc_executable": {
    "win32": { "path": "MissionControl.exe", "sha256": "ab12...", "size": 18234567 },
    "darwin": { "path": "MissionControl.app/Contents/MacOS/MissionControl", "sha256": "cd34...", "size": 17234567 },
    "linux": { "path": "missioncontrol", "sha256": "ef56...", "size": 16234567 }
  },
  "min_protocol": 1,
  "signed_at": "2026-04-27T12:34:56Z",
  "signing_key_id": "build-key-2026a",
  "signature": "<base64 Ed25519 signature over canonical-JSON of all fields except `signature`>"
}
```

### 4.2 Verification (in `mc-tunnel`)

1. Parse `build_manifest.json` from the install directory.
2. Look up `signing_key_id` in `TRUSTED_BUILD_KEYS`. If unknown → fail.
3. Recompute canonical-JSON of the manifest minus `signature`. Verify Ed25519 signature with the looked-up pubkey. If invalid → fail.
4. If `signed_at` is older than 365 days → fail (forces re-release of trustworthy versions).
5. If `min_protocol > MC_TUNNEL_PROTO` → fail (the binary is from a future protocol; `mc-tunnel` itself needs updating).
6. Pick the platform-appropriate entry from `mc_executable`. Resolve absolute path of the *parent* process (the spawning MC binary) and SHA-256 it.
7. Compare with `mc_executable.<platform>.sha256`. If mismatch → fail.

Failure at any step → `mc-tunnel` exits with a coded reason on stdout, MC surfaces "Tunnel refused to start: <reason>" in Settings.

---

## 5. Localhost handshake (MC ↔ `mc-tunnel`)

Purpose: prove that `mc-tunnel` was spawned by *this* MC instance (not by some other process that happens to be running concurrently), and give MC a way to refuse stray attestation attempts.

### 5.1 Spawn

MC launches `mc-tunnel` as a child process with these inputs:

```
argv:    mc-tunnel --mc-pid <ppid> --mc-port 5199
stdin:   <handshake_secret_b64>\n         # 32 random bytes, base64; consumed and closed
env:     MC_HANDSHAKE_TOKEN=<same secret> # belt-and-suspenders, also consumed
```

`mc-tunnel` reads the secret from stdin (preferred) or env, then immediately zeroes the env var.

### 5.2 Liveness + identity check (`mc-tunnel` → MC)

`mc-tunnel`, after parent-process verification (§4.2), calls:

```
GET http://127.0.0.1:5199/api/tunnel-handshake
Headers:
  Authorization: Bearer <handshake_secret_b64>
  X-MC-Tunnel-Version: 1.0.0
```

MC verifies:

- The connection arrived on `127.0.0.1` (not a non-loopback IP).
- The remote port belongs to a process whose PID is the child it just spawned. (On Windows: `GetTcpTable2`; on Linux: `/proc/net/tcp`. If the OS doesn't permit this check, fall back to PID-from-`mc-tunnel`-self-report and warn.)
- `Authorization` matches the secret it just generated. Constant-time compare.

If all checks pass, MC responds:

```json
{
  "ok": true,
  "mc_version": "1.4.2",
  "mc_pid": 12345,
  "challenge": "<32 random bytes, base64>",
  "device_pub_b64": "<from keystore>",
  "username": "ron",
  "enrollment_token": "<opaque>",
  "control_plane_url": "https://api.PLATFORM_DOMAIN"
}
```

The `challenge` is what `mc-tunnel` will sign with `device_priv` to prove it has access to MC's keystore-held credentials. The `enrollment_token` is short-lived inside this handshake — `mc-tunnel` does not persist it; it forwards it to the control plane in the next attestation step and forgets it.

### 5.3 Failure handling

- Wrong handshake secret → MC returns 401, logs `tunnel_handshake_rejected`. `mc-tunnel` exits.
- MC unreachable → `mc-tunnel` retries with backoff (250 ms, 500 ms, 1 s, 2 s, 4 s, max 10 s). After 60 seconds → exit; supervisor in MC respawns.
- MC running but no remote access enabled → returns 503 + `{ "remote_access_enabled": false }`. `mc-tunnel` exits cleanly; MC supervisor will not respawn until user re-enables.

---

## 6. Enrollment (one-time per install)

### 6.1 Sequence

```
┌──────────┐                    ┌─────────────┐               ┌────────────────┐
│  MC app  │                    │  Browser    │               │ Control plane  │
└────┬─────┘                    └──────┬──────┘               └────────┬───────┘
     │                                 │                                │
 1.  ├ keypair = Ed25519.generate()    │                                │
     ├ store device_priv in keystore   │                                │
     │                                 │                                │
 2.  ├ open browser →                  │                                │
     │   https://PLATFORM_DOMAIN/connect                                │
     │     ?device_pub=<b64>           │                                │
     │     &nonce=<csrf_b64>           │                                │
     │     &redirect=http://127.0.0.1:5199/api/mc-callback              │
     │                                 │                                │
 3.  │                                 ├─── GET /connect?...  ─────────►│
     │                                 │                                │  validate nonce
     │                                 │                                │  set CSRF cookie
     │                                 │◄── 200 (signin form) ──────────┤
     │                                 │                                │
 4.  │                                 │  user authenticates            │
     │                                 │  (Firebase Auth: Google / OTP) │
     │                                 │                                │
 5.  │                                 ├── POST /v1/enroll ────────────►│
     │                                 │   { device_pub, csrf,          │
     │                                 │     username_pref, fb_id_tok } │
     │                                 │                                │
     │                                 │                       verify Firebase ID token
     │                                 │                       reserve username
     │                                 │                       create users/devices rows
     │                                 │                       create CF Tunnel + DNS + Access
     │                                 │                       sign enrollment_token
     │                                 │                                │
     │                                 │◄── 200 ─────────────────────────
     │                                 │   { enrollment_token,          │
     │                                 │     username,                  │
     │                                 │     device_id,                 │
     │                                 │     hostname,                  │
     │                                 │     control_plane_pubkey_id }  │
     │                                 │                                │
 6.  │                                 ├── 302 →                        │
     │                                 │   http://127.0.0.1:5199/api/mc-callback?token=...&username=...&...
     │                                 │                                │
 7.  │◄── browser redirect ────────────┤                                │
     │   verify nonce, store           │                                │
     │   { enrollment_token,           │                                │
     │     username,                   │                                │
     │     device_id,                  │                                │
     │     hostname }                  │                                │
     │   in keystore                   │                                │
     │                                 │                                │
 8.  ├ spawn mc-tunnel — first         │                                │
     │   attestation cycle begins      │                                │
```

### 6.2 Why a browser-mediated flow

Alternatives considered:

- **Device code flow** (user enters a 6-digit code on the platform site). Works but adds a manual typing step that non-technical users fumble.
- **PKCE OAuth direct from MC** (no browser hop). Requires MC to handle the OAuth redirect and token exchange, and we'd still need to display a Firebase signin UI somewhere.
- **Browser hop** (chosen). Lets Firebase Auth's well-tested signin UI handle MFA, account recovery, social login, etc. The localhost redirect at the end is the standard OAuth-on-desktop pattern and survives across all reasonable browsers. Risk: redirect to `http://127.0.0.1` is a known phishing vector — mitigated by the per-flow `nonce` (CSRF token bound to `device_pub` server-side) that MC verifies on the callback.

### 6.3 Enrollment token semantics

- Opaque to `mc-tunnel`. Treated as a bearer token only inside the local handshake → attestation forwarding.
- Stored server-side as a row in `devices/{device_id}` with `enrollment_token_hash` (sha256), not plaintext.
- Revocable independently from the device record (e.g. "force re-enroll" without deleting device history).
- TTL: long-lived (1 year). Re-issued automatically on each successful attestation if older than 30 days.

The token's only role is to bind a *physical install* of MC to a *specific user account*. The actual security comes from the device key, which the token complements.

---

## 7. Attestation envelope

Sent on every tunnel session start and every 10 minutes during a session.

### 7.1 Schema

```json
{
  "proto": 1,
  "envelope_type": "attestation_request",
  "device_pub_b64": "<base64 32-byte Ed25519 pubkey>",
  "device_id": "<server-issued at enrollment>",
  "enrollment_token": "<opaque>",
  "build_manifest_id": "2026-04-27-build-714",
  "mc_version": "1.4.2",
  "mc_binary_sha256": "ab12...",
  "mc_tunnel_version": "1.0.0",
  "client_secret_key_id": "mc-tunnel-2026a",
  "os": "win32-11-26200",
  "hostname_claim": "ron.PLATFORM_DOMAIN",
  "timestamp": "2026-04-27T13:45:00Z",
  "nonce": "<32 bytes base64, from CP>",
  "challenge_response": "<base64 Ed25519 signature over `challenge` from §5.2>",
  "previous_token_id": "<id of the tunnel token being rotated, or null>"
}

// Wrapped in an outer signed object with TWO signatures:
{
  "envelope": <the JSON above>,
  "envelope_canonical_sha256": "<sha256 hex of canonical-JSON of envelope>",
  "signature_b64":          "<Ed25519 sig over envelope_canonical_sha256, by device_priv (proves which user/device)>",
  "client_signature_b64":   "<Ed25519 sig over envelope_canonical_sha256, by CLIENT_SECRET_PRIV (proves it's a real platform mc-tunnel)>"
}
```

The two signatures answer **two different questions**: `signature_b64` proves *which enrolled user/device* this came from. `client_signature_b64` proves *that it came from a platform-issued `mc-tunnel`*, not a fork that re-implemented the protocol. Both are required (see §3.6 and §7.4 step 4.5).

### 7.2 Why two layers

The outer wrapper is a stable shape that the control plane parses first to extract the device pubkey, look it up, and verify the signature. The inner envelope is the actual claim. This split lets us evolve the inner envelope (additive fields) without touching the wrapper, and makes the verifier code path uniform.

### 7.3 Nonce protocol

```
mc-tunnel        →  GET /v1/nonce?device_id=<id>           →  control plane
mc-tunnel        ←  { nonce, expires_at (now+30s), nonce_id }  control plane
... build envelope using nonce ...
mc-tunnel        →  POST /v1/attest { envelope, signature } →  control plane
```

Server side: nonces stored in a Redis-equivalent (Firestore short-TTL document or Memorystore) keyed by `(device_id, nonce_id)`. Burned on use, expire automatically. Replay attempts return `nonce_used` or `nonce_expired`.

### 7.4 Verification (in control plane)

In order; first failure returns the corresponding error code.

| # | Check | Failure code |
|---|---|---|
| 1 | Outer wrapper parses; both `signature_b64` and `client_signature_b64` present | `bad_envelope` |
| 2 | `envelope_canonical_sha256` matches recomputed hash | `bad_canonicalization` |
| 3 | `device_pub_b64` matches a non-revoked `devices` row | `unknown_device` / `revoked_device` |
| 4 | `signature_b64` verifies under `device_pub_b64` (device signature) | `bad_signature` |
| 4.5 | `client_secret_key_id` is in the active set; `client_signature_b64` verifies under that pubkey (platform-binding) | `bad_client_signature` / `unknown_client_key` / `revoked_client_key` |
| 5 | `enrollment_token` hash matches the stored hash for this device | `bad_enrollment_token` |
| 6 | `proto >= devices.min_protocol` and `proto >= versions[mc_version].min_protocol` | `version_floor_exceeded` |
| 7 | `mc_version` is in `versions` collection and `revoked == false` (per `05-` §1 the per-binary-hash check is descoped; we track versions, not builds) | `unknown_version` / `revoked_version` |
| 8 | *(reserved — was binary hash check; restored when code-signing comes back into scope)* | — |
| 9 | `nonce` exists and unexpired and unburned for this device | `nonce_used` / `nonce_expired` / `nonce_unknown` |
| 10 | `timestamp` within `now ± 60s` (server clock) | `timestamp_skew` |
| 11 | Per-device rate limit (10 attestations/min, 100/hr) | `rate_limited` |
| 12 | Per-account device-online count within tier cap | `device_cap_exceeded` |
| 13 | `hostname_claim == devices.hostname_claim` | `hostname_mismatch` |
| 14 | If `previous_token_id` non-null, it's a token previously issued to this device | `unknown_previous_token` |

On success: burn the nonce; emit a tunnel-token issuance.

### 7.5 Tunnel token issuance

```json
{
  "envelope_type": "attestation_response",
  "result": "ok",
  "tunnel_token": "<opaque cf token>",
  "tunnel_token_id": "<uuid>",
  "tunnel_token_expires_at": "2026-04-27T14:00:00Z",
  "next_attestation_after": "2026-04-27T13:55:00Z",
  "caps": {
    "bandwidth_bytes_remaining_period": 5368709120,
    "rate_limit_rps": 60,
    "max_response_bytes": 10485760,
    "max_concurrent_connections": 20
  },
  "directives": []
}
```

- Tunnel token TTL: 15 minutes.
- Renewal cadence: 10 minutes (5-minute safety margin).
- `caps` are echoed so the user-facing UI can show a progress bar; enforcement is at the Cloudflare Worker, not in `mc-tunnel`.
- `directives` is a list of zero or more action signals (see §8).

### 7.6 Directives

Server-driven asynchronous instructions. Interpreted by `mc-tunnel`:

| Directive | Effect |
|---|---|
| `force_logout` | Tear down tunnel, exit; MC re-enrollment required. |
| `update_required` | Tear down tunnel, exit; MC surfaces update prompt. |
| `notify_user`, `text` | MC displays the text in the Remote Access panel. |
| `pause`, `seconds` | Stop attesting and disconnect for N seconds (used during planned maintenance). |
| `step_up_auth`, `reason` | MC opens browser to `/connect/step-up?reason=...` for re-auth. |

---

## 8. Revocation propagation

Three revocation triggers, one mechanism: the next attestation returns a directive instead of a token.

```
                            User revokes device
                            Operator revokes account
                            Operator revokes build hash
                                       │
                                       ▼
                          Firestore: devices.revoked_at = now
                          (or builds.revoked = true,
                           or users.suspended = true)
                                       │
                                       │ takes effect immediately;
                                       │ no propagation delay
                                       ▼
                          Next attestation (≤10 min later)
                                       │
                                       ▼
                          Verification step 3, 7, or precomputed
                          → returns { result: "revoked",
                                       directive: "force_logout" }
                                       │
                                       ▼
                              mc-tunnel exits cleanly,
                              MC shows "Disconnected"
```

**Worst-case latency:** one attestation cycle = 10 minutes.

For instant revocation (e.g., active abuse), the operator can additionally push a directive via the existing CF tunnel (the control plane has the tunnel UUID) — but this is a v2 optimization. In v1, 10-minute worst-case is acceptable and we keep the protocol unidirectional.

---

## 9. Attack matrix

What each layer of the protocol blocks, and what it doesn't.

| Attack | Blocking step |
|---|---|
| Fork open-source MC, re-implement protocol, abuse `*.PLATFORM_DOMAIN` | **Client signature check** (§7.4 step 4.5) — fork doesn't have `CLIENT_SECRET_PRIV`; CF Worker traffic-shape caps (`04-` Layer 3) bound damage if extracted |
| Run platform `mc-tunnel` standalone, point at own server | MC handshake fails (§5.2); even if bypassed, attestation succeeds → CF tunnel forwards only to `127.0.0.1:5199` (path allowlist `04-` Layer 2) |
| Reverse-engineer `mc-tunnel` to extract `CLIENT_SECRET_PRIV` | Strip symbols + obfuscate (raises bar); rotate per release (60–180 day key lifetime); revoke compromised key on detection |
| Modify open-source MC core to proxy non-MC traffic | CF Worker path allowlist (`04-` Layer 2); response shape sampling; traffic-shape caps |
| Steal `enrollment_token` from disk | Useless without `device_priv` (OS keystore) AND `CLIENT_SECRET_PRIV` (in proprietary `mc-tunnel` binary on the same machine) |
| Steal `device_priv` from keystore | OS keystore typically requires user session; if compromised, user revokes device — propagates within 10 min |
| Replay captured attestation | Nonce single-use (step 9); timestamp ±60s (step 10) |
| Forge attestation with attacker key | Step 3: `device_pub_b64` must match enrolled device; step 4.5: `client_signature_b64` must verify under an active platform key |
| Compromise control plane → forge tunnel tokens | Attacker can impersonate users with new tunnels; **cannot read existing dashboard traffic** (CP doesn't proxy data); cannot decrypt past sessions; v2 E2E encryption removes even this impersonation risk |
| Compromise control plane → steal device pubkeys / client_secret pubkeys | Pubkeys are public by design; not a vulnerability |
| Run old MC with known CVE | Version allowlist (step 7); `min_protocol` floor (step 6) |
| Share account across many devices | `device_cap_exceeded` (step 12) |
| MITM control plane | TLS pinning (post-launch); public CA chain (pre-launch — accepted risk during private beta) |
| MITM tunnel between `mc-tunnel` and CF | Cloudflare's TLS handles this; tunnel client uses CF-issued certs |
| Hijack localhost handshake from another local process | Bound to specific PID/port (§5.2); 401 on bad secret; MC supervisor logs and exits |
| Phishing attack: trick user into visiting `http://127.0.0.1:5199/api/mc-callback?token=evil` | CSRF nonce bound server-side to `device_pub`; MC rejects callback if nonce doesn't match outstanding enrollment |

---

## 10. Error codes (definitive list)

`mc-tunnel` and MC both ship with a localized string table keyed by these codes. The control plane returns the code; the client decides how to render it.

```
ok
bad_envelope
bad_canonicalization
unknown_device
revoked_device
bad_signature
bad_client_signature       # NEW: client_signature_b64 didn't verify
unknown_client_key         # NEW: client_secret_key_id not in active set
revoked_client_key         # NEW: client_secret_key_id revoked (rotation, suspected leak)
bad_enrollment_token
version_floor_exceeded
unknown_version            # was unknown_build (renamed per 05- §1)
revoked_version            # was revoked_build
nonce_used
nonce_expired
nonce_unknown
timestamp_skew
rate_limited
device_cap_exceeded
hostname_mismatch
unknown_previous_token
account_suspended
quota_exceeded
maintenance_mode
internal_error
```

User-facing messages live in the MC frontend, not in `mc-tunnel`. `mc-tunnel` writes the code + a one-line English summary to its log; MC translates.

---

## 11. Logging and telemetry

What the protocol writes, where, and for how long.

| Source | Sink | Retention | Contents |
|---|---|---|---|
| MC | local log (existing `agent_log` neighbor) | rotating, 30 MB cap | enrollment events, attestation results (success/code), tunnel-token rotations, directives received. **No keys, no token contents.** |
| `mc-tunnel` | stderr → MC's Tauri terminal | live | parent-verify result, handshake result, attestation timing |
| Control plane | `attestation_log` Firestore | 30 days (TTL policy) | `device_id`, `timestamp`, `result`, `mc_binary_sha`, `ip_hash` (sha256, salted). **Not raw IPs.** |
| Control plane | Cloud Logging | 30 days | structured JSON of every API request; redacted of envelope contents and tokens |

Privacy intent: nothing identifying about the *user's traffic* ever lands in our logs. Only the *attestation events* — which device, when, did it succeed.

---

## 12. Testing strategy (for the protocol)

Independent of v1 implementation; documented here so it isn't lost.

1. **Static manifest verification tests** — fuzz-test `mc-tunnel`'s parser against malformed/truncated/oversized manifests; fuzz-signed-but-tampered cases.
2. **Replay tests** — capture a real attestation, replay it, expect `nonce_used`. Replay after burn → expect `nonce_unknown`.
3. **Clock-skew tests** — simulate ±5 minute skew, verify rejection.
4. **Revocation race tests** — revoke during an in-flight attestation, verify next cycle picks it up.
5. **Build-rotation tests** — issue manifest with `signing_key_id = build-key-2026b`, verify `mc-tunnel` accepts it once that key is in the trusted set.
6. **End-to-end test rig** — spin up a local control-plane instance, a local CF-stub, and a real MC build; run a 30-minute session through 3 attestation rotations.
7. **Negative tests for parent-verify** — spawn `mc-tunnel` from an unsigned wrapper; expect parent-verify failure.

CI placement: the static tests and unit tests live in the `mc_tunnel` crate. The end-to-end rig is a separate `e2e/` directory invoked nightly, not on every commit.

---

## 13. Open questions for v1

1. **Do we use Firestore TTL or a separate Memorystore for nonces?** Firestore has TTL but eventual-consistency on reads; nonces benefit from strong consistency. Leaning toward Memorystore (Redis) — adds a dependency but simplifies correctness.
2. **Should `mc-tunnel` cache the last successful attestation response and resume on transient CP failure?** Yes for short windows (≤30s) — prevents brief CP blips from disconnecting users. Spec'd in §11 in `01-architecture.md` as "existing tunnel keeps working until token expires."
3. **Should the device key live in OS keystore or a flat file with restrictive permissions?** OS keystore is more secure but introduces install-time prompts on macOS (Keychain) that confuse users. Tentatively: OS keystore with a fallback to an XDG-compliant file mode 0600 if keystore is unavailable, but warn the user.
4. **What's the exact format of `enrollment_token`?** Recommendation: 256-bit random, base64url, no embedded claims. Server-side opaque lookup. Avoids JWT complexity.
5. **How does `mc-tunnel` self-update?** Out of v1 scope; it ships with each MC release and doesn't update independently. Side effect: a `mc-tunnel` bug requires a full MC release. Acceptable for v1.

---

## 14. Glossary

- **`mc-tunnel`** — small Rust binary shipped inside MC; talks to the control plane and runs the actual tunnel client.
- **Build manifest** — `build_manifest.json` shipped alongside the MC binary, signed by the operator's build key, listing the expected MC binary hash.
- **Device key** — Ed25519 keypair generated at first enrollment, identifying a specific MC install. Private half lives in OS keystore.
- **Enrollment token** — opaque server-issued credential that ties a device to an account.
- **Attestation envelope** — signed claim from a device proving it is genuine, current, and authorized.
- **Tunnel token** — short-lived (15 min) Cloudflare-issued credential used to start the actual tunnel.
- **Directive** — server → client instruction returned alongside (or instead of) a tunnel token.
- **Nonce** — single-use, server-issued random value preventing replay of attestations.

---

Next up: `03-control-plane-api.md` — concrete OpenAPI for every endpoint referenced in this doc.
