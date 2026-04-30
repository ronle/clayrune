# Mission Control Remote Access — Error Codebook

**Status:** Draft
**Owner:** Ron Levy
**Last updated:** 2026-04-27

This is the **single source of truth** for every error code that appears anywhere in the remote-access stack: protocol envelopes (`02-attestation-protocol.md`), HTTP responses (`03-control-plane-api.md`), Cloudflare Worker responses (`04-abuse-prevention.md`), and the local MC client.

Client localization tables key off the `code` column. Operator runbooks key off the `category` column. The `surfaces` column says where each code can originate.

---

## 1. Conventions

- **Code format:** `lowercase_snake_case`. No version suffixes — codes are stable across protocol versions; new codes added rather than renaming.
- **Where it's seen:** in the `code` field of every error response (protocol-level or HTTP-level).
- **Default English message:** what the server returns in the `message` field. Clients **should** localize — the code is the contract, the message is a default.
- **User-facing copy:** the *recommended* MC UI copy in English. Optimized for non-technical users. Localized client-side.

---

## 2. Categories

| Category | Meaning |
|---|---|
| `protocol` | Attestation envelope or build-manifest validation failed |
| `auth` | Identity / signature / credential failure |
| `authz` | Identity is valid; action is not allowed |
| `quota` | Cap or limit exceeded |
| `state` | Resource state precludes the action (revoked, suspended, etc.) |
| `request` | Malformed / oversized / wrong content-type input |
| `transient` | Temporary; retry will likely succeed |
| `not_found` | Resource doesn't exist |
| `conflict` | Concurrent modification or duplicate resource |
| `policy` | Worker-level policy violation (path allowlist, method allowlist) |

---

## 3. Codebook

### 3.1 Protocol-layer codes (originate from `mc-tunnel` ↔ control plane)

| Code | Category | HTTP | Surfaces | Default message | User-facing copy |
|---|---|---|---|---|---|
| `ok` | — | 200 | attest, nonce | Success | n/a (success path) |
| `bad_envelope` | request | 400 | attest | Malformed attestation envelope | "Couldn't connect — please reconnect remote access." |
| `bad_canonicalization` | protocol | 400 | attest | Envelope canonical hash mismatch | "Couldn't connect — please reconnect remote access." |
| `unknown_device` | auth | 401 | attest, nonce | No matching enrolled device | "This device isn't enrolled. Sign in to re-enroll." |
| `revoked_device` | state | 403 | attest, nonce | Device has been revoked | "This device was disconnected from remote access. Sign in to re-enroll." |
| `bad_signature` | auth | 401 | attest | Signature verification failed | "Couldn't connect — please reconnect remote access." |
| `bad_enrollment_token` | auth | 401 | attest | Enrollment token doesn't match device | "This device isn't enrolled. Sign in to re-enroll." |
| `version_floor_exceeded` | state | 403 | attest, nonce | Mission Control needs updating | "An update is required to use remote access. Update Mission Control." |
| `unknown_build` | state | 410 | attest | Build manifest not registered | "An update is required to use remote access. Update Mission Control." |
| `revoked_build` | state | 410 | attest | Build has been revoked (CVE response) | "An update is required for security. Update Mission Control." |
| `binary_hash_mismatch` | protocol | 400 | attest | mc-binary-sha differs from registered build | "Mission Control couldn't verify itself. Reinstall the latest version." |
| `nonce_used` | request | 400 | attest | Nonce already burned | "Couldn't connect — retrying…" (auto-retry) |
| `nonce_expired` | request | 400 | attest | Nonce older than 30 s | "Couldn't connect — retrying…" (auto-retry) |
| `nonce_unknown` | request | 400 | attest | Nonce not issued for this device | "Couldn't connect — retrying…" (auto-retry) |
| `timestamp_skew` | request | 400 | attest | Client clock off by more than 60 s | "Your computer's clock is wrong. Fix the clock and reconnect." |
| `rate_limited` | quota | 429 | attest, nonce, enroll, signin | Too many requests | "Slowing down a moment, then we'll reconnect." |
| `device_cap_exceeded` | quota | 403 | enroll, attest | Account at device limit | "You've reached the limit of devices on your plan. Disconnect another device or upgrade." |
| `hostname_mismatch` | state | 403 | attest | Device hostname_claim differs from server record | "Your username changed. Reconnecting…" (auto-resolve via /v1/devices refresh) |
| `unknown_previous_token` | request | 400 | attest | previous_token_id not recognized | "Couldn't connect — retrying…" (auto-retry; non-fatal) |
| `account_suspended` | state | 403 | attest, nonce, account | Account is suspended | "Your account is suspended. Email support." |
| `quota_exceeded` | quota | 402 | worker, attest | Bandwidth quota exhausted | "You've used up this month's free bandwidth. Verify your account or upgrade." |
| `maintenance_mode` | transient | 503 | any | Platform is in maintenance | "Remote access is temporarily unavailable. Trying again soon." |
| `internal_error` | transient | 500 | any | Unexpected server error | "Something went wrong. Trying again…" |

### 3.2 HTTP-layer codes (originate from control plane API)

| Code | Category | HTTP | Surfaces | Default message | User-facing copy |
|---|---|---|---|---|---|
| `malformed_json` | request | 400 | any POST/PUT | Could not parse request body as JSON | "Couldn't connect — please reconnect remote access." |
| `unsupported_media_type` | request | 415 | any POST/PUT | Content-Type must be application/json | (developer-only) |
| `payload_too_large` | request | 413 | any POST/PUT | Request body exceeds 64 KiB | (developer-only) |
| `unauthorized` | auth | 401 | account, admin | Auth missing or invalid | "Please sign in again." |
| `forbidden` | authz | 403 | account, admin | Action not allowed | "You don't have permission to do that." |
| `not_found` | not_found | 404 | various | Resource doesn't exist | "Couldn't find that." |
| `conflict` | conflict | 409 | enroll, username | Duplicate or concurrent modification | (varies — see specific codes below) |
| `idempotency_conflict` | conflict | 409 | any with idem-key | Same key, different body | (developer-only) |
| `service_unavailable` | transient | 503 | any | Backend unavailable; retry | "Remote access is briefly unavailable. Trying again…" |
| `email_unverified` | auth | 403 | enroll, account | Firebase email_verified == false | "Verify your email before continuing. Check your inbox." |
| `enrollment_intent_invalid` | request | 400 | enroll | CSRF nonce missing/expired | "Sign-in expired. Click 'Connect' again." |
| `username_taken` | conflict | 409 | enroll, username | Username already in use | "That username is taken. Try another." |
| `username_invalid` | request | 400 | enroll, username | Username doesn't match policy | "Usernames are 3–24 characters: lowercase letters, numbers, and dashes." |
| `username_reserved` | request | 400 | enroll, username | Username on reserved list | "That username isn't available. Try another." |
| `username_change_too_recent` | quota | 403 | username | Within 90 days of last change | "You can change your username again on <date>." |
| `quota_exceeded_account` | quota | 402 | worker | Bandwidth/period cap exhausted | "You've used up this month's free bandwidth. Verify your account or upgrade." |
| `step_up_required` | authz | 403 | various | Step-up auth needed before action | "Please verify your account to continue. We'll walk you through it." |
| `provisioning_in_progress` | transient | 503 | enroll, attest | CF resource provisioning still pending | "Setting up remote access… try again in a few seconds." |
| `provisioning_abandoned` | state | 410 | enroll | CF provisioning failed > 24h | "Setup couldn't complete. Email support." |

### 3.3 Worker-layer codes (originate from Cloudflare Worker)

| Code | Category | HTTP | Surfaces | Default message | User-facing copy |
|---|---|---|---|---|---|
| `unknown_hostname` | not_found | 404 | worker | Hostname not registered | (visitor-facing) "Page not found." |
| `tunnel_offline` | state | 502 | worker | Tunnel exists but no live mc-tunnel | (custom HTML page; see `04-abuse-prevention.md` §3) "Mission Control is offline. Last seen: <time>." |
| `path_not_allowed` | policy | 404 | worker | Path not in allowlist | "Page not found." |
| `method_not_allowed` | policy | 405 | worker | HTTP method not in allowlist | (developer-only) |
| `method_forbidden` | policy | 403 | worker | CONNECT or other forbidden verb | (developer-only) |
| `upgrade_not_allowed` | policy | 403 | worker | WebSocket on non-allowlisted path | (developer-only) |
| `headers_too_large` | request | 431 | worker | Total header bytes > 16 KiB | (developer-only) |
| `body_too_large` | request | 413 | worker | Body > 64 MiB | "File too large to upload." |
| `response_too_large` | quota | 502 | worker | Origin returned > 10 MiB response | "Response was too large." (rare on legitimate use) |
| `concurrency_exceeded` | quota | 429 | worker | More than max concurrent connections | "Too many active connections. Trying again…" |
| `bandwidth_exceeded` | quota | 402 | worker | Period bandwidth quota hit | "You've used up this month's free bandwidth." |

### 3.4 Local MC / `mc-tunnel` codes (never reach the network — local UI only)

| Code | Category | Surfaces | Default message | User-facing copy |
|---|---|---|---|---|
| `tunnel_handshake_failed` | local | mc-tunnel ↔ MC | Localhost handshake rejected | "Remote access couldn't start. Restart Mission Control." |
| `tunnel_parent_verify_failed` | local | mc-tunnel | Parent process verification failed | "Mission Control couldn't verify itself. Reinstall the latest version." |
| `tunnel_manifest_missing` | local | mc-tunnel | build_manifest.json not found | "Installation looks incomplete. Reinstall Mission Control." |
| `tunnel_manifest_invalid` | local | mc-tunnel | Manifest signature invalid | "Mission Control couldn't verify itself. Reinstall the latest version." |
| `tunnel_keystore_unavailable` | local | MC | OS keystore inaccessible | "Couldn't access secure storage. Run as your normal user (not Administrator) and try again." |
| `tunnel_cloudflared_down` | local | supervisor | cloudflared subprocess not alive (crashed, missing binary, bad token) | "Tunnel daemon stopped responding. Reconnecting…" |
| `tunnel_no_internet` | local | mc-tunnel | Network unreachable | "No internet connection." |
| `tunnel_cp_unreachable` | local | mc-tunnel | api.PLATFORM_DOMAIN can't be reached | "Remote access service is unreachable. Check your connection." |
| `tunnel_cf_pin_failed` | local | mc-tunnel | Control plane TLS pin mismatch | "Couldn't securely connect to remote access. Check for a Mission Control update." |

---

## 4. Decision tree for new codes

When a new error condition arises:

1. **Is it user-actionable?** If yes, define a code and user-facing copy. If no, fold into `internal_error`.
2. **Does it fit an existing code?** Reuse it. Don't create near-synonyms.
3. **Does it cross a layer?** If a worker-layer error effectively means the same as a protocol-layer one, prefer keeping them distinct so logs surface the layer.
4. **Add it to this document** before merging the code that emits it. PR description should reference the row added.

---

## 5. Logging and metrics conventions

- **Server logs** include `code` as a structured field. **Never** log `message` — it's user-facing and may be localized.
- **Cloud Monitoring metric label:** `code` is bounded (this codebook is the alphabet). Safe to use as a high-cardinality dimension. Approx 60 codes total — fine.
- **Alerting:** rate of `internal_error` > baseline by 3σ → page on-call. Rate of `bad_signature` from a single device → suspicious; auto-create operator ticket.

---

## 6. Localization plan

- v1: English-only user-facing copy. Codes-only on the wire.
- v2: ship a `messages_<lang>.json` file with MC; keys = codes, values = localized copy. Translation effort scoped at ~60 strings × N languages.
- The control plane and worker are **never** in the localization loop — they emit codes; clients render.

---

## 7. Cross-references

- Wire format and verification logic: `02-attestation-protocol.md` §10
- HTTP response shapes: `03-control-plane-api.md` §7
- Worker response shapes: `04-abuse-prevention.md` §3
- Step-up auth flow that resolves several codes: `04-abuse-prevention.md` §2 Layer 4
