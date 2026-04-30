# Mission Control Remote Access — Control Plane API

**Status:** Draft
**Owner:** Ron Levy
**Last updated:** 2026-04-27
**Depends on:** `01-architecture.md`, `02-attestation-protocol.md`
**Companion file:** `control_plane/api_spec.yaml` (OpenAPI 3.1, machine-readable)

This document is the narrative contract for the control-plane HTTP API at `api.PLATFORM_DOMAIN`. The OpenAPI YAML is the authoritative schema; this doc explains *why* each endpoint exists, *who* calls it, and *how* it interacts with the rest of the system.

A frozen version of this doc + the YAML is what allows the MC client and the control plane to be built in parallel against a stable contract.

---

## 1. Overview

### 1.1 Base

- **Host:** `https://api.PLATFORM_DOMAIN` (placeholder; see `01-architecture.md` §10)
- **Transport:** HTTPS only, TLS 1.3, HSTS preload
- **API version prefix:** `/v1`
- **Content type:** `application/json` (UTF-8) for all request/response bodies; canonical-JSON (RFC 8785 / JCS) for any payload that gets cryptographically signed (see `02-attestation-protocol.md` §2)
- **Time:** all timestamps RFC 3339 with `Z` suffix; server clock authoritative

### 1.2 API surface map

```
Public, unauthenticated:
   GET    /v1/health                       liveness
   GET    /v1/connect                      enrollment landing (browser)
   POST   /v1/signin/start                 OTP / Google sign-in start
   POST   /v1/signin/complete              OTP / Google sign-in complete

Authenticated by Firebase ID token (browser session):
   POST   /v1/enroll                       bind device_pub to account
   GET    /v1/account                      account state
   GET    /v1/devices                      list user's devices
   POST   /v1/devices/{device_id}/rename
   POST   /v1/devices/{device_id}/revoke
   POST   /v1/account/username             change username (limited)
   DELETE /v1/account                      delete account + cascade

Authenticated by device-key signature (mc-tunnel):
   GET    /v1/nonce                        issue attestation nonce
   POST   /v1/attest                       attestation envelope → tunnel token

Authenticated by operator JWT (admin):
   POST   /v1/admin/builds                 register a new build manifest
   POST   /v1/admin/builds/{id}/revoke     revoke a build
   POST   /v1/admin/users/{id}/suspend     suspend user (abuse response)
   POST   /v1/admin/users/{id}/unsuspend
   GET    /v1/admin/devices                paginated device search
   POST   /v1/admin/maintenance            broadcast maintenance directive
```

### 1.3 Design principles

- **Never see user data.** No endpoint accepts, returns, or stores dashboard contents. The CP is an identity, registry, and orchestrator service.
- **Stateless compute.** Cloud Run instances hold no per-user state in memory. Firestore + Memorystore (nonces) are the state. Any instance can answer any request.
- **Idempotent writes where possible.** All write endpoints accept an `Idempotency-Key` header (max 64 chars). Repeated keys within 24h return the original response.
- **Strict input validation.** JSON schemas declared in OpenAPI; reject unknown fields. Maximum body size 64 KiB except for admin endpoints.
- **Error envelope is uniform.** Every non-2xx returns `{ code, message, request_id, retry_after_ms? }`.
- **Codes track the protocol.** The 22 error codes from `02-attestation-protocol.md` §10 are returned verbatim.

---

## 2. Authentication

Three mutually exclusive auth schemes; each endpoint accepts exactly one.

### 2.1 Firebase ID token (browser)

Browser endpoints expect:

```
Authorization: Bearer <firebase_id_token>
```

The CP verifies via `firebase-admin` SDK using project public keys (cached, refreshed every 6 hours). Verified claims surface as `req.user_id`, `req.email`, `req.email_verified`. Endpoints requiring `email_verified == true` return `email_unverified` if not.

### 2.2 Device signature (`mc-tunnel`)

Attestation-flow endpoints expect a **two-part** auth on the wire:

- The request body itself is a wrapped, signed envelope (see `02-attestation-protocol.md` §7).
- The header carries the device identifier so the CP can fetch the public key without parsing the body twice:

```
Authorization: MC-Device device_id="<id>",sig_alg="ed25519"
Content-Type:  application/json
```

The wrapper carries the signature; the header carries the lookup key. This split means the body can be evolved (additive fields) without breaking the auth scheme.

### 2.3 Operator JWT (admin)

Admin endpoints expect:

```
Authorization: Bearer <operator_jwt>
```

JWT issued by Google Identity-Aware Proxy in front of the admin endpoints. Audience-bound to `admin.PLATFORM_DOMAIN`. Operator role required (claim `roles: ["operator"]`). Public endpoints `/v1/admin/*` are additionally network-restricted via Cloud Run ingress to operator IP ranges.

---

## 3. Endpoint reference

Each subsection links the endpoint to the protocol step it implements, then defines the contract.

### 3.1 `GET /v1/health`

**Purpose:** Cloud Run / load balancer probe; also surfaced to status pages.

- No auth.
- Returns `{ status: "ok", build: <git_sha>, time: <iso8601> }` if Firestore + Memorystore are reachable.
- Returns 503 with `{ status: "degraded", problems: [...] }` otherwise.
- Cached at the edge for 1 second to absorb probe storms.

### 3.2 `GET /v1/connect`

**Purpose:** the URL the local MC opens in the system browser to start enrollment. Returns the signin UI hosted at `PLATFORM_DOMAIN/connect` (a server-side template; see Note A).

- No auth.
- Query parameters:
  - `device_pub` — base64 32-byte Ed25519 public key (required)
  - `nonce` — base64 32-byte CSRF token (required; bound server-side to `device_pub`)
  - `redirect` — `http://127.0.0.1:5199/api/mc-callback` only (anything else → 400)
  - `username_hint` — optional pre-fill
- Effect: stores `(device_pub_hash, nonce, redirect, expires_at = now + 15min)` in `enrollment_intents` collection. Sets a CSRF cookie tied to the row.
- Returns: HTML signin page that proxies to Firebase Auth, then to `/v1/enroll`.

**Note A:** `/v1/connect` is served as HTML for simplicity in v1. In v2 we may move the signin UI to a static frontend at `app.PLATFORM_DOMAIN` and have `/v1/connect` return only a redirect — the API contract is the same either way.

### 3.3 `POST /v1/signin/start`

**Purpose:** initiate signin from any device for the recovery flow ("forgot my URL").

- No auth.
- Request: `{ method: "google" | "otp", email: <required if method=otp> }`
- For `otp`: triggers Firebase Auth email-link, returns `{ sent: true, expires_in: 900 }`.
- For `google`: returns `{ redirect_url: <Google OAuth URL> }`.
- Rate-limited: 5/email/hour, 20/IP/hour.

### 3.4 `POST /v1/signin/complete`

**Purpose:** finalize signin → returns Firebase ID token to the browser.

- No auth.
- Request varies by method (`otp_code` for OTP, `code+state` for OAuth).
- Response: `{ id_token, refresh_token, user_id, username, devices_count }`.
- Sets `__Host-session` HttpOnly cookie scoped to `PLATFORM_DOMAIN`.

### 3.5 `POST /v1/enroll`

**Purpose:** the heart of first-run enrollment (see `02-attestation-protocol.md` §6.1 step 5).

**Auth:** Firebase ID token (browser).

**Request:**
```json
{
  "device_pub_b64": "<base64 32 bytes>",
  "csrf_nonce": "<base64 from /v1/connect>",
  "username": "ron",
  "device_name": "Ron's Desktop",
  "os": "win32-11-26200",
  "mc_version": "1.4.2"
}
```

**Server actions (transactional):**

1. Look up `enrollment_intents` by `(csrf_nonce, hash(device_pub_b64))`. If missing or expired → 400 `enrollment_intent_invalid`.
2. Check username availability (regex `^[a-z0-9](-?[a-z0-9])*$`, length 3–24, blocklist). Conflict → 409 `username_taken`.
3. Check user's device cap for tier. Exceeded → 403 `device_cap_exceeded`.
4. Create `users/{user_id}` if not exists; create `devices/{device_id}`.
5. Generate `enrollment_token` (256-bit random, base64url). Store **hash** in device row.
6. Call Cloudflare API:
   - `POST /accounts/.../tunnels` — create named tunnel, get UUID + tunnel token (long-lived).
   - `POST /zones/.../dns_records` — `<username>.PLATFORM_DOMAIN` CNAME → `<UUID>.cfargotunnel.com`.
   - `POST /accounts/.../access/apps` — create Access app gating `<username>.PLATFORM_DOMAIN` with email policy `{ email: <user.email> }`.
   - `POST /accounts/.../tunnels/<UUID>/configurations` — set ingress to `service: http://localhost:5199` for hostname.
3. Burn the `enrollment_intent` row.
4. Persist device, return response.

**Response:**
```json
{
  "device_id": "dev_AbCd1234",
  "enrollment_token": "<base64url 256 bits — only ever returned ONCE>",
  "username": "ron",
  "hostname": "ron.PLATFORM_DOMAIN",
  "control_plane_pubkey_id": "cp-2026a",
  "min_protocol": 1
}
```

**Failure handling:** if any Cloudflare API call fails after the device row is created, the row is marked `provisioning_failed` and a background reconciler retries (idempotent on CF side via `Idempotency-Key`). The user sees "Enrollment in progress, retry in a moment" in MC.

### 3.6 `GET /v1/nonce`

**Purpose:** issue a single-use nonce for the next attestation (`02-attestation-protocol.md` §7.3).

**Auth:** device signature (header only — request is a plain GET).

**Query parameters:** `device_id` (required).

**Server actions:**
- Verify `device_id` exists, not revoked.
- Generate 32 random bytes; store `(device_id, nonce_id, nonce_hash, expires_at = now+30s)` in Memorystore.
- Return `{ nonce: <b64>, nonce_id: <uuid>, expires_at: <iso8601> }`.

Rate limit: 60/device/min. Bursts of 12 (failed attestation retries) allowed before throttling.

### 3.7 `POST /v1/attest`

**Purpose:** the load-bearing endpoint. Verify device + envelope; issue tunnel token.

**Auth:** device signature **+ client signature** (two distinct Ed25519 signatures over the same canonical envelope hash). See `02-attestation-protocol.md` §3.4 (device key) and §3.6 (client secret).

**Request:** the wrapped envelope from `02-attestation-protocol.md` §7.1. Body must be byte-identical to canonical-JSON (server recomputes the hash). The wrapper carries both `signature_b64` and `client_signature_b64`.

**Server actions:** the 14-step verification list from `02-attestation-protocol.md` §7.4 (now including step 4.5 — client signature verification under an active platform key). Each step's failure produces the corresponding error code.

**Success response:** the tunnel-token issuance from `02-attestation-protocol.md` §7.5.

**Side effects on success:**
- Update `devices.last_seen = now`, `devices.mc_version`, `devices.os`, `devices.client_secret_key_id` (last seen) if changed.
- Append row to `attestation_log` (30-day TTL), including `client_secret_key_id` for forensics on suspected leaks.
- If `previous_token_id` was provided, mark it superseded in the `tunnel_tokens` collection (used for audit only; the token itself is opaque CF state).
- Increment per-period bandwidth/RPS counters.

**Success response status:** 200. Token-rotation success and first-attestation success share the same shape; clients distinguish by whether `previous_token_id` was sent.

**Failure response status:**
- 400 — malformed / replay / hash mismatch
- 401 — `bad_signature` / `bad_client_signature` / `unknown_client_key`
- 403 — revoked / suspended / device cap / version floor / `revoked_client_key`
- 410 — `revoked_version` (was `revoked_build`)
- 429 — rate limited
- 503 — internal (retry with backoff)

### 3.8 `GET /v1/devices`

**Purpose:** the user-facing list rendered in the account dashboard and (read-only mirror) in MC's Remote Access panel.

**Auth:** Firebase ID token.

**Response:**
```json
{
  "devices": [
    {
      "device_id": "dev_AbCd1234",
      "device_name": "Ron's Desktop",
      "os": "win32-11-26200",
      "mc_version": "1.4.2",
      "hostname": "ron.PLATFORM_DOMAIN",
      "online": true,
      "last_seen": "2026-04-27T13:41:00Z",
      "enrolled_at": "2026-04-12T18:00:00Z",
      "is_this_device": false
    }
  ],
  "tier": "free",
  "device_cap": 2
}
```

`is_this_device` is set when the request arrives bearing a Firebase token whose user has the requesting browser's IP also currently associated with an online device — best-effort hint only.

Pagination: `?limit=N&cursor=...` (cursor is opaque).

### 3.9 `POST /v1/devices/{device_id}/rename`

**Auth:** Firebase ID token.

**Request:** `{ name: "Living Room PC" }` (1–64 chars, no control chars).

**Effect:** updates `devices.device_name`. Echoed back via attestation responses' `directives` channel only on the next cycle (no push channel in v1).

### 3.10 `POST /v1/devices/{device_id}/revoke`

**Auth:** Firebase ID token.

**Effect:** sets `devices.revoked_at = now`, `revoke_reason = "user_request"`. Calls Cloudflare API to delete the tunnel + DNS record + Access app. Next attestation from that device will fail with `revoked_device` and receive the `force_logout` directive (`02-attestation-protocol.md` §7.6).

Idempotent: revoking a revoked device returns 200 with the existing record.

**UX note:** the user-facing button copy is "Disconnect this PC" — "revoke" is operator vocabulary.

### 3.11 `POST /v1/account/username`

**Purpose:** allow a user to change their username (rare).

**Auth:** Firebase ID token.

**Request:** `{ new_username: "ronl" }`.

**Limits:** once per 90 days, charged via a counter on the user row.

**Effect:** allocate new username, update DNS records for all online devices, mark the old hostname as `redirect → new_hostname` for 30 days (CF Worker handles the redirect), then release the old username after the redirect window.

Failure mode if a device is offline during the swap: the device's `hostname_claim` no longer matches; next attestation fails with `hostname_mismatch`. MC then refetches `/v1/devices` and updates local state. UX: "Your username changed; reconnecting…"

### 3.12 `DELETE /v1/account`

**Auth:** Firebase ID token + `X-Confirm: delete-my-account` header.

**Effect:** marks user `deleted_at = now`, suspends all devices, schedules cascade deletion of: device rows, attestation_log entries, CF tunnels, DNS records, Access apps. 7-day grace period before hard delete; user can `POST /v1/account/restore` within that window.

**Username availability after deletion:** released after 90-day cooldown to prevent impersonation.

### 3.13 `GET /v1/account`

**Auth:** Firebase ID token.

**Response:** `{ user_id, username, email, tier, created_at, device_cap, bandwidth_quota_period_bytes, bandwidth_used_period_bytes }`.

Used by MC to render its Remote Access panel header and by the account dashboard for the "current usage" widget.

### 3.14 Admin endpoints

Operator-only. Documented in OpenAPI; brief summary here.

- **`POST /v1/admin/versions`** — register an `mc_version` allowlist entry (renamed from `/v1/admin/builds` per `05-` §1; per-binary-hash check is descoped). Idempotent on `mc_version`.
- **`POST /v1/admin/versions/{mc_version}/revoke`** — emergency revoke a version (CVE response). Sets `revoked: true`. All devices on that version fail next attestation.
- **`POST /v1/admin/client_keys`** — register a new platform client-secret pubkey with `key_id`. Issued every `mc-tunnel` release (`02-` §3.6). Idempotent on `key_id`.
- **`POST /v1/admin/client_keys/{key_id}/revoke`** — emergency revoke a client-secret key (suspected leak / extracted from a binary). Forces all devices using that key to next-attestation failure with `revoked_client_key`; users see `update_required` directive.
- **`POST /v1/admin/users/{id}/suspend`** / **`unsuspend`** — abuse response.
- **`GET /v1/admin/devices`** — search by `username`, `device_id`, `hostname`, `mc_version`, `client_secret_key_id`, `online`, `last_seen_before`. Cursor-paginated.
- **`POST /v1/admin/maintenance`** — broadcast a `notify_user` or `pause` directive to all online devices for the next attestation cycle.

---

## 4. Firestore data model (formalized)

This locks down the sketch in `01-architecture.md` §5.

### 4.1 `users/{user_id}`

| Field | Type | Notes |
|---|---|---|
| `user_id` | string | Firebase UID. |
| `username` | string | normalized lowercase, unique. Indexed. |
| `email` | string | for support; not used for lookup (we use Firebase UID). |
| `email_hash` | string | sha256(email) for analytics joins without exposing PII. |
| `created_at` | timestamp | |
| `deleted_at` | timestamp? | soft-delete; hard-deleted by reconciler at +7 days. |
| `tier` | enum | `free` / `paid`. |
| `device_cap` | int | derived from `tier` but stored to allow per-user overrides. |
| `bandwidth_quota_period_bytes` | int | resets monthly. |
| `bandwidth_used_period_bytes` | int | incremented from CF Worker analytics push. |
| `risk_score` | int | 0..100, computed by background job. |
| `suspended` | bool | abuse flag; blocks all attestation. |
| `username_changed_at` | timestamp? | for 90-day cooldown. |

**Indexes:** `username` (unique), `email_hash`, `(suspended, last_active)`.

### 4.2 `devices/{device_id}`

| Field | Type | Notes |
|---|---|---|
| `device_id` | string | `dev_` + 16 char random. |
| `user_id` | string | parent user (denormalized for query). |
| `device_pub_b64` | string | Ed25519 pubkey, base64. **Indexed**. |
| `device_pub_hash` | string | sha256, used in lookups to avoid storing pubkey in some logs. |
| `enrollment_token_hash` | string | sha256. |
| `enrollment_token_renewed_at` | timestamp | |
| `device_name` | string | user-editable. |
| `os` | string | `<platform>-<major>-<build>`. |
| `mc_version` | string | from latest attestation. |
| `client_secret_key_id` | string | last platform key id seen from this device (forensics on rotation). |
| `hostname_claim` | string | `<username>.PLATFORM_DOMAIN`. |
| `cf_tunnel_uuid` | string | Cloudflare tunnel id. |
| `cf_dns_record_id` | string | for cleanup on revoke. |
| `cf_access_app_id` | string | for cleanup on revoke. |
| `enrolled_at` | timestamp | |
| `revoked_at` | timestamp? | |
| `revoke_reason` | string? | |
| `last_seen` | timestamp | from attestation. |
| `last_attestation_result` | string | last error code or `ok`. |
| `provisioning_state` | enum | `pending` / `active` / `failed`. |

**Indexes:** `device_pub_b64` (unique), `(user_id, revoked_at)`, `(hostname_claim)`.

### 4.3 `versions/{mc_version}` (was `builds/`)

Per `05-build-pipeline.md` §1, the per-binary-hash flow is descoped; we track versions, not builds. When code-signing returns to scope, this collection extends with the additional fields rather than being replaced.

| Field | Type | Notes |
|---|---|---|
| `mc_version` | string | semver, doc id. |
| `min_protocol` | int | |
| `released_at` | timestamp | when first registered. |
| `revoked` | bool | |
| `revoke_reason` | string? | |
| *(reserved)* `mc_sha256_by_platform` | map<string,string>? | populated when code-signing returns to scope. |
| *(reserved)* `signing_key_id` | string? | KMS key id once build manifests are signed again. |

### 4.3a `client_secret_keys/{key_id}` (NEW for open-core)

Active and recently-rotated platform client-secret pubkeys. Written by the operator via `POST /v1/admin/client_keys` on every `mc-tunnel` release.

| Field | Type | Notes |
|---|---|---|
| `key_id` | string | e.g. `mc-tunnel-2026a`. Doc id. |
| `pubkey_b64` | string | Ed25519 public key, base64. |
| `released_at` | timestamp | when this binary release shipped. |
| `revoked_at` | timestamp? | nullable. Set on suspected leak; forces all attestations carrying this key id to fail with `revoked_client_key`. |
| `revoke_reason` | string? | `extracted` / `expired_rotation` / `cve` etc. |
| `mc_tunnel_version` | string | `mc-tunnel` semver this key shipped with. |

**Active set** = up to 3 most-recent non-revoked rows (read at attestation step 4.5). Older entries remain in the collection for forensics but no longer authorize attestations.

**Indexes:** `(revoked_at, released_at desc)` for the active-set query.

### 4.4 `attestation_log/{auto_id}`

TTL = 30 days (Firestore-managed).

| Field | Type | Notes |
|---|---|---|
| `device_id` | string | |
| `user_id` | string | |
| `timestamp` | timestamp | |
| `result` | string | error code or `ok`. |
| `mc_binary_sha` | string? | reserved; populated when code-signing returns to scope. |
| `mc_version` | string | |
| `client_secret_key_id` | string | which platform key this attestation was signed with — surfaces leak/rotation patterns. |
| `os` | string | |
| `ip_hash` | string | sha256(ip + daily_salt) — coarse-grained, rotates daily. |
| `nonce_id` | string | for replay forensics. |
| `latency_ms` | int | server-side processing time. |

**Indexes:** `(device_id, timestamp desc)`, `(result, timestamp desc)`.

### 4.5 `enrollment_intents/{auto_id}`

TTL = 15 minutes.

| Field | Type | Notes |
|---|---|---|
| `device_pub_hash` | string | |
| `csrf_nonce_hash` | string | |
| `redirect` | string | must be `http://127.0.0.1:5199/api/mc-callback`. |
| `username_hint` | string? | |
| `created_at` | timestamp | |
| `expires_at` | timestamp | |
| `consumed_at` | timestamp? | one-shot use. |

### 4.6 `nonces` (Memorystore Redis)

Not Firestore — needs strong consistency and sub-millisecond latency. Key pattern:

```
nonce:<device_id>:<nonce_id>  →  hash(nonce)  (TTL 30s)
```

Burned on consumption with `DEL` after `GETSET` to prevent races. Failure of the `DEL` is non-fatal (TTL covers it).

### 4.7 `rate_limits` (Memorystore Redis)

Sliding-window counters:

```
rl:attest:<device_id>:<minute_bucket>  →  count
rl:attest:<user_id>:<hour_bucket>      →  count
rl:nonce:<device_id>:<minute_bucket>   →  count
rl:enroll:<email>:<hour_bucket>        →  count
```

All TTL'd to 2× their window.

---

## 5. Cloudflare orchestration

The control plane is the only entity that talks to the Cloudflare API. The user's `mc-tunnel` never holds Cloudflare credentials.

### 5.1 Required CF resources per user

| Resource | Created at | Deleted at |
|---|---|---|
| Tunnel (named) | enrollment | account deletion / device revocation |
| Tunnel ingress config | enrollment | tunnel delete |
| DNS CNAME `<username>.PLATFORM_DOMAIN` | enrollment | account deletion |
| Access application gating that hostname | enrollment | account deletion |
| Access policy (email == user.email) | enrollment | username change / account deletion |

### 5.2 CF API rate limits

CF imposes ~1200 requests/5 min per token. The CP serializes CF API calls behind a rate-limited worker queue (Cloud Tasks) to stay well under. Enrollment latency budget: 3 seconds (5 CF calls × ~400ms avg).

### 5.3 Failure recovery

The CP keeps `provisioning_state` on each device. A reconciler runs every 5 minutes, finds `provisioning_state in (pending, failed)` rows, and retries with idempotency keys. After 24h of failure → user notified by email, row marked `provisioning_abandoned`.

### 5.4 Webhook handling

CF can push tunnel-event webhooks (connect/disconnect). The CP exposes `POST /v1/webhooks/cloudflare` (HMAC-signed by CF token shared secret). Used to maintain `online` state on `devices` rows so `/v1/devices` can answer accurately without polling. Webhooks are advisory; absence does not affect security (attestation is the authority).

---

## 6. Idempotency and retries

### 6.1 Client retry rules

- Attestation: retry on 503 / network errors with full jitter, base 500ms, max 5 attempts, then surface error.
- Enrollment: retry only on explicit `503 retry_after_ms`; 4xx is final and surfaced to user.
- Nonce: retry on 503 with backoff; on 429 honor `retry_after_ms`.

### 6.2 Server idempotency

Every `POST` accepts `Idempotency-Key: <client-generated-uuid>`. Lookup table:

```
idem:<endpoint>:<Idempotency-Key>  →  { status, body, expires=24h }
```

Repeat with same key returns cached result. Mismatched body with same key → `409 idempotency_conflict`.

Endpoints that **must** receive idempotency keys:
- `POST /v1/enroll`
- `POST /v1/devices/{id}/revoke`
- `POST /v1/admin/builds`
- `DELETE /v1/account`

Endpoints that **may** ignore idempotency keys (intrinsically idempotent):
- `POST /v1/attest` (nonces are single-use; replay is detected at protocol layer)
- `POST /v1/devices/{id}/rename`

---

## 7. Error envelope

Every non-2xx response:

```json
{
  "code": "rate_limited",
  "message": "Too many attestations from this device. Wait and retry.",
  "request_id": "req_abc123",
  "retry_after_ms": 8400,
  "details": { /* optional, code-specific */ }
}
```

`code` is one of the 22 strings from `02-attestation-protocol.md` §10 plus a small set of HTTP-layer codes:

```
malformed_json
unsupported_media_type
payload_too_large
unauthorized
forbidden
not_found
conflict
idempotency_conflict
internal_error
service_unavailable
email_unverified
enrollment_intent_invalid
username_taken
quota_exceeded_account
maintenance_mode
```

Combined codebook lives in `docs/remote-access/error_codes.md` (to be created when 04 is written) so client localization tables have one source of truth.

---

## 8. Observability

### 8.1 Per-request

Every response carries:
```
X-Request-Id: req_<ulid>
X-Server-Region: us-central1
X-Build: <git_sha>
```

Logs are JSON-structured: `{ time, level, request_id, route, method, status, latency_ms, user_id?, device_id?, code? }`. **Never** logs request bodies, tokens, or signatures.

### 8.2 Metrics (Cloud Monitoring)

- `attestation_total{result=...}` — counter
- `attestation_latency_ms` — histogram
- `enrollment_total{outcome=...}` — counter
- `cf_api_latency_ms{endpoint=...}` — histogram
- `firestore_op_latency_ms{op=...}` — histogram
- `rate_limit_hits_total{rule=...}` — counter
- `device_online_total` — gauge (from webhooks + last_seen heuristic)

### 8.3 Tracing

Cloud Trace, sampling 1% in steady state, 100% for 5xx requests. Spans: incoming request → Firestore reads → CF API calls → Memorystore ops → Firestore writes.

### 8.4 SLOs

- `/v1/attest` p99 ≤ 300 ms (excluding network).
- `/v1/attest` availability ≥ 99.9% over 30 days. (User impact of breach: tunnel disconnects until token expires, ≤15 min later.)
- `/v1/enroll` p99 ≤ 4 s (CF API is the long tail).
- `/v1/connect` p99 ≤ 1 s.

---

## 9. Versioning and deprecation

- New fields are additive within `/v1/...`; clients ignore unknown response fields.
- Removing or renaming a field requires `/v2/...`. Both versions run for ≥6 months.
- Deprecated fields carry `Deprecation: <date>` and `Sunset: <date>` headers per RFC 8594.
- Protocol-level breaking changes bump the envelope `proto` (see `02-attestation-protocol.md` §1) and force build-manifest re-issuance.

---

## 10. Security review checklist

Before cutting v1 traffic, confirm:

- [ ] All write endpoints require explicit auth scheme (no implicit cookies cross-purposing as auth).
- [ ] CSRF nonces in `/v1/connect` are per-flow, server-bound, single-use.
- [ ] Firebase ID token signature verified, audience pinned, expiry enforced.
- [ ] Device signature verified on **every** attestation (no caching of "this device is trusted").
- [ ] Enrollment tokens stored as hashes; only returned plaintext **once** in `/v1/enroll` response.
- [ ] No CF API tokens or KMS keys appear in any response or log line.
- [ ] Per-endpoint rate limits live and tunable without redeploy.
- [ ] Admin endpoints behind IAP + IP allowlist.
- [ ] All admin actions append to a tamper-evident audit log (Cloud Logging with `protoPayload`).
- [ ] DELETE flows have a grace period and a documented restore path.
- [ ] Privacy review: confirm we never log emails, IPs (use ip_hash), or token contents.
- [ ] Pen-test the localhost-redirect flow for token-leak scenarios via referer headers (we should set `Referrer-Policy: no-referrer` on the enrollment HTML).

---

## 11. Open questions for v1

Carrying forward from `02-attestation-protocol.md` §13 plus new ones surfaced by the API surface:

1. **Username reservation policy.** Reserved words file (`admin`, `api`, `app`, `support`, `mc`, `claude`, `anthropic`, common 3-letter combos)? Profanity filter library? Squatting prevention (one username per account, paid tier may reserve up to 3)?
2. **Email change flow** — out of v1 (handled via account deletion + new signup) or first-class endpoint?
3. **Device transfer** between accounts — out of v1.
4. **Bandwidth metering granularity** — minute-level pushed from CF Worker, or hourly aggregates? Cost vs precision tradeoff.
5. **Maintenance mode UX** — hard-block attestation, or serve `pause` directive with hint? Leaning pause+hint.
6. **i18n** — error messages localized server-side or client-side? Strongly leaning client-side (codes only on the wire).

---

## 12. Implementation note

This doc is language-agnostic. The implementation language for the control plane (Python+FastAPI vs Node+Fastify vs Go+chi) is decided in `06-rollout-plan.md`. The OpenAPI YAML can drive code generation in any of those targets.

Recommended choice for v1: **Python + FastAPI** — same language as MC server, can share validation logic if useful, fastest iteration. Migrate to Go if/when attestation latency or cost becomes a binding constraint.

---

Next up: `04-abuse-prevention.md` — the Cloudflare Worker logic, traffic caps enforcement, risk scoring, and the consolidated error codebook.
