# Mission Control Remote Access — Abuse Prevention

**Status:** Draft
**Owner:** Ron Levy
**Last updated:** 2026-04-27
**Depends on:** `01-architecture.md`, `02-attestation-protocol.md`, `03-control-plane-api.md`
**Companion file:** `error_codes.md` (consolidated codebook)

This document specifies the controls that keep `*.PLATFORM_DOMAIN` from becoming a free public proxy / dyndns alternative. Goal: legitimate MC users notice nothing; bad-faith use becomes uneconomical.

---

## 1. Threat model

What we're defending against, ranked by likelihood:

| # | Adversary | Goal | Why our service is attractive |
|---|---|---|---|
| 1 | Hobbyist | Free stable hostname + tunnel for a Minecraft server, home media server, or `ngrok`-replacement | We give a stable URL with HTTPS, free, no port forwarding |
| 2 | Scraper / bot operator | Outbound proxy with residential-IP exit | Each tunnel exits from a real home IP |
| 3 | Phishing kit author | Disposable HTTPS hostname | `<random>.PLATFORM_DOMAIN` looks legit and gets a CF cert |
| 4 | Crypto miner / botnet C2 | C2 endpoint reachable through corporate firewalls | CF edge bypasses egress filters |
| 5 | DMCA-bait media host | Distribute pirated content via stable URL | High bandwidth, good cert, branded URL |
| 6 | Sophisticated attacker | Compromise legitimate user, extend access | Targets specific MC users, not the platform |

(6) is out of scope for *abuse prevention* — it's a security topic handled by the attestation protocol (`02-`) and the operator's incident response. This doc focuses on (1)–(5).

What we're **explicitly not** trying to stop:
- A legitimate user running *only* their own MC dashboard for personal use, even if heavy (their own bandwidth budget governs).
- Privacy-conscious access (a user behind a VPN connecting to *their own* dashboard is fine).
- Power-user automation (curl scripts hitting their own MC's API are fine, within rate caps).

---

## 2. The six layers (recap and detail)

The conversation arrived at six layers; this section turns each into concrete controls with thresholds.

### Layer 1 — Bind the tunnel to MC

**Mechanism:** documented in detail in `02-attestation-protocol.md`.

Summary of what it gives us *as an abuse control*:

- `mc-tunnel` only forwards to `127.0.0.1:5199`. Hardcoded; not a flag.
- Parent process must be a signed MC binary whose hash matches a registered build manifest. So you cannot point `mc-tunnel` at a Minecraft server, an HTTP proxy, or any other service.
- The MC binary itself is Authenticode/notarized. Modifying it invalidates the OS signature **and** changes its SHA256 (build-manifest verification fails).

**What still leaks through this layer:** an attacker who *runs MC unmodified* and operates it in unintended ways (e.g. uses MC's terminal pop-out feature to relay bytes through their own PC). Layers 2–6 catch this.

### Layer 2 — Edge path allowlist + content shape checks

Implemented in a single Cloudflare Worker that fronts every `<username>.PLATFORM_DOMAIN`.

**Allowlist** (a literal regex against `request.url.pathname`):

```
^/$                                  # dashboard root
^/static/.+                          # static assets
^/data/uploads/.+                    # user uploads (read-only by browser)
^/api/(?!internal/).+                # all public API routes; deny /api/internal/*
^/api/sse/.+                         # SSE streams
^/api/terminal/.+                    # terminal pop-out endpoints
^/favicon\.ico$
^/manifest\.webmanifest$
^/sw\.js$                            # service worker
```

Anything else → 404 with `path_not_allowed`. Sourced from MC's actual routing table; reviewed every release.

**Method allowlist:** GET, POST, PUT, DELETE, OPTIONS, HEAD. Anything else → 405.

**Header sanity:**
- Reject any request with `Upgrade: websocket` to a non-allowlisted path.
- Reject `CONNECT` (the proxy-tunnel verb) outright.
- Strip and re-set `Host`, `X-Forwarded-For`, `CF-Connecting-IP` (defensive).
- Cap header total size at 16 KiB.

**Response shape checks** (sampled, not on every request):

- 1% of responses are inspected for `Content-Type` mismatch (e.g. tunnel returns `application/octet-stream` for a `.html`-suffixed path).
- 1% are checked against MC's known response fingerprints — `/api/projects` should return JSON with a `projects` array; `/api/agent_log` should return JSON with `entries`. Sustained mismatch → flag.

The sample rate is intentionally low: legitimate MC traffic should never trip this; abuse traffic, when investigated, can be rapidly resampled at higher rates from the operator dashboard.

### Layer 3 — Traffic shape caps

Enforced at the Worker, with thresholds returned to `mc-tunnel` in the attestation response (`02-attestation-protocol.md` §7.5) so the user-facing UI can show usage graphs.

**Default free-tier caps:**

| Cap | Value | Why this number |
|---|---|---|
| Bandwidth (rolling 30-day) | 5 GiB / month | MC's measured peak usage for a heavy user is ~150 MiB/mo; 5 GiB is 33× headroom. Anything above is not MC. |
| Request rate (sustained) | 60 req/s | MC dashboards spike to 5–20 req/s briefly; 60 is generous. |
| Request rate (burst) | 200 req/s for ≤5 s | Initial dashboard load can fan out. |
| Concurrent connections | 20 | MC opens 1–4 SSE + a few HTTP. Scrapers run hundreds. |
| Response body size | 10 MiB | MC never returns more than ~2 MiB; 10 is safety margin. Blocks media hosting. |
| Request body size | 64 MiB | MC accepts uploads (project files); 64 is the practical max we currently support. |
| WebSocket session duration | 6 hours | Auto-reconnect is fine; abuse flows often don't reconnect cleanly. |
| Outbound CONNECT, HTTP/2 server push | denied | Proxy verbs / unusual flows. |

**Paid-tier defaults** (placeholder; for future planning):

| Cap | Value |
|---|---|
| Bandwidth (rolling 30-day) | 100 GiB |
| Request rate (sustained) | 300 req/s |
| Concurrent connections | 100 |
| Response body | 50 MiB |
| WebSocket session | 24 hours |

Caps are **per-account**, not per-tunnel. A user with two devices shares the bandwidth budget. The Worker keys counters by `user_id` (looked up from hostname → `users/{user_id}`).

**Enforcement responses:**
- Rate limit hit: 429 with `Retry-After`.
- Bandwidth quota exhausted: 402 `quota_exceeded` (Payment Required is the right semantic for "you can pay your way out").
- Response size cap exceeded: 502 `response_too_large` from the Worker, *and* the tunnel for that user is briefly throttled to 1 req/s for 5 minutes (because exceeding the response cap is a strong abuse signal).

### Layer 4 — Identity friction proportional to use

**Free tier signup:** email OTP only. Free, low-friction, blocks zero-day disposable emails (Firebase Auth checks email validity).

**Friction step-up triggers** (any one of):

- 2nd device enrollment.
- Bandwidth use crosses 1 GiB/month (warning) / 5 GiB/month (hard cap).
- Account age < 24h *and* bandwidth use > 100 MiB/day.
- Account exhibits any "suspicious shape" risk score ≥ 50 (see Layer 5).

**Step-up options shown to user**, in increasing trust order:
1. Google sign-in (link existing OAuth identity to the account).
2. SMS verification.
3. **Card pre-authorization** ($1 hold, immediately released). This is the real abuse killer. Costs ~$0.05 per attempt; disposable card / VBV-failing cards reject.

The step-up is a *modal* on `account.PLATFORM_DOMAIN`; until it's resolved, the account is throttled to 1 GiB/month and 1 device. This is intentionally generous so a real user only hits it if they're scaling beyond casual personal use.

**Why card pre-auth as the gate:** real users have a real card. Abusers have card stacks of 5–50, but pre-auth uses Stripe Radar for fraud scoring and rejects most. Even if not, $0.05 per attempt × 1000 attempts = $50 spent to spin up 1000 abuse accounts, vs. ~$0 with email-only.

### Layer 5 — Risk scoring and auto-throttle

A background job runs every 5 minutes over recently-active users and computes a risk score (0–100). Inputs:

| Signal | Weight | Why |
|---|---|---|
| Sustained bandwidth >> MC baseline | +30 | Strongest signal of non-MC use |
| Sustained req/s >> MC baseline | +20 | Scraping shape |
| Many distinct visitor IPs in short windows | +15 | Public proxy use |
| Tor exit node as primary visitor | +10 | Not necessarily abuse — privacy-conscious users — but correlated |
| Geographic mismatch (visitor country ≠ device country) sustained | +5 | Travel is real; sustained disjoint is suspicious |
| Account age < 7 days and high traffic | +15 | New-account abuse pattern |
| Path mismatch fingerprints (Layer 2 sampling) | +20 per hit | Direct evidence |
| Card pre-auth completed | −20 | Real-money buy-in |
| Linked accounts (same card / device fingerprint) flagged | +25 | Account farm pattern |
| User has been on the platform > 90 days with low risk history | −10 | Long-tail trust |

**Thresholds:**

| Score | Effect |
|---|---|
| 0–19 | normal |
| 20–49 | shadow flag — operator visible, no user impact |
| 50–69 | auto-throttle to ~10% of normal caps; user shown soft notice "Unusual activity detected; please complete verification." Resolved by passing step-up auth (Layer 4). |
| 70–89 | hard-throttle (1 req/s, 100 MiB/day); operator alerted |
| 90+ | suspend; operator must manually unsuspend |

Auto-throttle is **reversible without operator action** by the user completing step-up. This is critical: if the only path back is operator review, false positives become support burden.

### Layer 6 — Make alternatives more attractive for non-MC use

A defensive design choice, not a control. The premise: abuse happens when our free tier beats the dedicated alternatives. We deliberately keep our free tier **worse for non-MC purposes** than:

| Alternative | What it gives | Where we are deliberately worse |
|---|---|---|
| Tailscale Funnel | 1 TB/mo bandwidth, free, stable URL | We cap at 5 GiB/mo |
| ngrok free | Random URL, unlimited bandwidth | We constrain to MC paths |
| Cloudflare Tunnel (own domain) | Unlimited, full control, ~$10/yr for domain | Requires owning a domain — friction we don't impose |
| localhost.run / serveo | Free, unstable | We're stable but path-locked |

If a hypothetical user wanted to abuse our service for, say, hosting media, Tailscale Funnel is strictly better (200× the bandwidth, no path enforcement). Our service is only better than these alternatives **if you're using it for MC**.

---

## 3. Cloudflare Worker — implementation sketch

Single Worker, deployed to all routes matching `*.PLATFORM_DOMAIN`. Pseudocode (TypeScript, the actual Worker language):

```ts
import { Router } from 'itty-router';

const PATH_ALLOWLIST = [
  /^\/$/,
  /^\/static\/.+/,
  /^\/data\/uploads\/.+/,
  /^\/api\/(?!internal\/).+/,
  /^\/api\/sse\/.+/,
  /^\/api\/terminal\/.+/,
  /^\/favicon\.ico$/,
  /^\/manifest\.webmanifest$/,
  /^\/sw\.js$/,
];

export default {
  async fetch(req: Request, env: Env, ctx: ExecutionContext) {
    const url = new URL(req.url);
    const hostname = url.hostname;            // e.g. "ron.PLATFORM_DOMAIN"
    const username = hostname.split('.')[0];

    // 1. Hostname → user lookup (cached in Durable Object or KV; 60s TTL)
    const user = await env.USER_KV.get(`user:${username}`, { type: 'json' });
    if (!user) return j(404, 'unknown_hostname');

    if (user.suspended) return j(403, 'account_suspended');
    if (user.tunnel_offline) return offlinePage(user);

    // 2. Path allowlist
    if (!PATH_ALLOWLIST.some(re => re.test(url.pathname)))
      return j(404, 'path_not_allowed');

    // 3. Method allowlist
    if (!['GET','POST','PUT','DELETE','OPTIONS','HEAD'].includes(req.method))
      return j(405, 'method_not_allowed');

    // 4. CONNECT / Upgrade abuse
    if (req.method === 'CONNECT') return j(403, 'method_forbidden');
    const upgrade = req.headers.get('Upgrade');
    if (upgrade && !isAllowedWsPath(url.pathname)) return j(403, 'upgrade_not_allowed');

    // 5. Header size cap
    if (totalHeaderBytes(req) > 16 * 1024) return j(431, 'headers_too_large');

    // 6. Body size cap
    const cl = parseInt(req.headers.get('Content-Length') || '0', 10);
    if (cl > 64 * 1024 * 1024) return j(413, 'body_too_large');

    // 7. Per-user rate limit (Durable Object holds sliding-window counters)
    const rl = await env.RATE_LIMITER.idFromName(`u:${user.user_id}`);
    const rlStub = env.RATE_LIMITER.get(rl);
    const rlResp = await rlStub.fetch('https://_/check', {
      method: 'POST',
      body: JSON.stringify({
        kind: 'request',
        weight: 1,
        caps: user.caps,
      }),
    });
    if (rlResp.status === 429) return rlResp;

    // 8. Bandwidth quota gate
    if (user.bandwidth_used >= user.bandwidth_quota) return j(402, 'quota_exceeded');

    // 9. Forward to tunnel; cap response body size as it streams
    const upstream = await fetch(`https://${user.cf_tunnel_uuid}.cfargotunnel.com${url.pathname}${url.search}`, {
      method: req.method,
      headers: sanitizeHeaders(req.headers),
      body: req.body,
    });

    const limited = limitResponseBody(upstream.body, user.caps.max_response_bytes);

    // 10. Sample for fingerprint check (1%)
    if (Math.random() < 0.01) ctx.waitUntil(sampleFingerprint(upstream, user, url));

    // 11. Push bandwidth analytics back to control plane (batched)
    ctx.waitUntil(env.ANALYTICS.write({
      user_id: user.user_id,
      bytes_in: cl,
      bytes_out: estimateOut(upstream),
      path: url.pathname,
      status: upstream.status,
    }));

    return new Response(limited.body, {
      status: upstream.status,
      headers: limited.headers,
    });
  },
};
```

**State that the Worker reads:**
- `USER_KV` (Workers KV) — hostname → user record. Written by control plane on enrollment / username change / suspension. 60-second TTL is fine because critical revocations (suspend) also propagate via KV with explicit cache-bust on the affected key.
- `RATE_LIMITER` (Durable Object) — per-user sliding-window counter.
- `ANALYTICS` (Workers Analytics Engine) — batched bandwidth telemetry; pushed to control plane every 60s.

**State the Worker doesn't have:** any keys, any tokens, any decrypted user data. The Worker is a policy point, not a custodian.

---

## 4. Detection — what looks like abuse

These are *post-hoc* signals fed into Layer 5 risk scoring. None block traffic on their own.

| Signal | Detection method |
|---|---|
| Visitor IP variety | Count distinct `CF-Connecting-IP` per user per hour; flag if >50 |
| Bot UAs as primary visitors | UA classification via CF Bot Management; flag if >70% bot |
| Tor exit nodes as primary visitors | CF threat intel; weight at 10% — not block, just flag |
| Sustained throughput at saturation | If hourly bandwidth ÷ cap > 0.95 for 24h, flag (real user might hit this once; sustained is suspicious) |
| Path mix anomaly | Real MC users' top-10 paths should include `/api/projects`, `/api/config`, `/`. If the top-10 are all unique paths, flag |
| Card-stack signal (Stripe Radar) | If Stripe rejects 3+ pre-auth attempts on the same account, flag |
| Same browser fingerprint creating many accounts | Browser-side fingerprint hashed at signup; >5 accounts/fingerprint/week → flag |
| New TLD in `Origin` header sustained | Origin should usually be `<username>.PLATFORM_DOMAIN`; sustained other origins suggest CSRF-style tunneling |
| Payload size distribution | Real MC has a tight distribution (most 1–50 KiB JSON, occasional larger files). Bimodal distribution with a heavy tail >1 MiB is suspicious |

These run in a daily batch job over `attestation_log` and CF Worker analytics dumps. Operator dashboard surfaces flagged accounts ranked by score.

---

## 5. Operator playbook

When the dashboard flags an account, the operator has a fixed set of actions, in order of severity:

| Action | API call | Reversible? | When |
|---|---|---|---|
| Add note (no user impact) | `POST /v1/admin/users/{id}/note` | yes | "watching" |
| Email user (Layer 4 step-up prompt) | manual via support@ | n/a | First-time anomaly |
| Auto-throttle to 10% caps | `PATCH /v1/admin/users/{id}/caps` | yes | Risk 50–69 (auto) |
| Hard-throttle to floor caps | `PATCH /v1/admin/users/{id}/caps` | yes | Risk 70–89 |
| Suspend account | `POST /v1/admin/users/{id}/suspend` | yes | Risk ≥90, or direct evidence |
| Revoke specific device | `POST /v1/admin/devices/{id}/revoke` | yes | Compromised device only, account otherwise OK |
| Revoke build hash | `POST /v1/admin/builds/{id}/revoke` | yes | CVE; affects all users on that build |
| Permaban (with refund if paid) | DB column `permabanned: true` | no | Repeat offender, evidence of malice |

**Auditable:** every admin action lands in Cloud Logging with `protoPayload` (immutable, tamper-evident). Operator can be subpoenaed; logs are the answer.

**SLA:** auto-throttle is real-time. Operator review of flagged accounts: target 24h response.

---

## 6. False positive handling

If a legitimate user is auto-throttled (Layer 5):

1. They see a banner in MC's Remote Access panel: "Unusual activity detected. Verify your account to restore full access."
2. Click → opens browser to `account.PLATFORM_DOMAIN/verify`.
3. Step-up auth flow (Layer 4): pick Google sign-in / SMS / card pre-auth.
4. On success, throttle is released within 60 seconds. Risk score drops by the −20 weight.
5. If user disputes ("I'm legitimate, this is wrong"), they can email support; operator reviews within 24h, can manually clear.

Goal: the false-positive cost to a legitimate user is "verify your email or run a card" — annoying once, never again.

---

## 7. Privacy posture

This is a defense-in-depth system, but it deliberately collects minimal data.

**What we collect and store:**
- Bandwidth counters per user (aggregated minute → hourly).
- Attestation results (success/failure code, with `ip_hash`, 30-day TTL).
- CF Worker analytics — bytes in/out, status code, *path*, *no body*.
- Risk-score inputs (computed signals, not raw data) — 90-day retention.

**What we deliberately don't collect:**
- Request bodies.
- Response bodies.
- User-Agent of dashboard visitors (CF aggregates; we see only "% bot" not the string).
- Plaintext IPs (only `sha256(ip + daily_salt)`).
- Plaintext emails (only `email_hash` for join).
- Anything that lets us reconstruct what the user looked at in their dashboard.

**Privacy disclosure to user (in TOS):** "We measure how much traffic flows through your tunnel and which paths are accessed (e.g. `/api/projects`). We do not see, store, or have access to the contents of your projects or any data inside Mission Control. The dashboard's TLS terminates at Cloudflare; in v1, Cloudflare can see decrypted traffic on its servers (this is true of every CF customer). v2 will add end-to-end encryption to remove even this."

The TOS is honest about the v1 trust model — v2 is not yet shipped, so we don't claim it.

---

## 8. Terms of Service hooks

The TOS (drafted later) will codify the abuse boundary. Required clauses:

1. **Permitted use** — Mission Control operation only. Not a generic tunnel/proxy/hosting service.
2. **Prohibited content** — illegal content, malware, phishing, spam, infringing material, sexual content involving minors (mandatory wherever you operate), CSAM (immediate report).
3. **Prohibited use patterns** — proxying third-party traffic, exposing services other than MC, automated visitors at scale, redistribution, commercial resale of remote access.
4. **Capacity limits** — operator may throttle or suspend at any time for cap violations.
5. **DMCA + counter-notice procedure** — designated agent address, takedown timing, repeat-infringer policy.
6. **Liability** — strict limitations, "AS IS" warranty disclaimer.
7. **Data handling** — what we store (per §7), retention, deletion rights.
8. **Termination** — operator's right to terminate, user's right to export account data (devices list, account info; we don't have dashboard data to export).
9. **Governing law** — TBD with legal entity.

Privacy policy mirrors §7. Cookie policy (CSRF cookies on enrollment flow; Firebase session cookies — both essential, no analytics cookies).

---

## 9. Specific abuse scenarios — walk-throughs

### 9.1 Hobbyist tries to expose their Minecraft server

- Downloads MC, signs up, enrolls.
- Realizes MC's `mc-tunnel` only forwards to `127.0.0.1:5199` and only that.
- Tries to modify `mc-tunnel` to forward to `:25565` — modification breaks the embedded build-attestation pubkey verification (the binary is signed; modified binaries fail Authenticode and the build-manifest hash check).
- Tries to run `cloudflared` directly with the tunnel token they snooped out of memory — token is short-lived (15 min) and bound to a hostname in CF that the Worker enforces via path allowlist; even if they re-flow it, traffic must look like MC.
- Gives up; uses Tailscale Funnel instead (better fit anyway).

**Cost to abuser:** an afternoon of work, no traction.
**Cost to platform:** zero — Layer 1 + Layer 2 caught it.

### 9.2 Scraper farm tries to spin up 1000 accounts

- Buys 1000 disposable emails, scripts signup.
- Each signup: email OTP works (Firebase doesn't catch all disposables), reaches `/v1/enroll`.
- Each account at signup is in step-up-pending state because of the "new account high traffic" rule — but they don't know it yet.
- They start hitting traffic; Layer 3 caps each at 1 GiB/month. They need 100 GiB total → need 100 accounts active → need to pass step-up on each.
- Step-up: card pre-auth. They use a stack of 100 cards; Stripe Radar rejects 80%, allows 20.
- 20 accounts × 5 GiB = 100 GiB. Cost to abuser: ~$2 in pre-auth fraud + significant card-stack burn. Cost to scraping output: 100 GiB at 60 req/s sustained per account = abuse-worthy throughput.
- Detection: Layer 5 catches "20 accounts created same week, similar IP fingerprint, all at saturation, unusual path mix." Operator suspends within 24h.

**Net result:** abuser gets ~24h × 100 GiB before suspension. Hostile. But the cost-per-day-of-throughput is much higher than dedicated alternatives (residential proxy services charge $5-10/GB; we cost them ~$2 of cards + significant time). Not economical.

**Cost to platform:** 24 hours of bandwidth (~$0 since CF eats it), one operator-review hour.

### 9.3 Phishing kit author tries to host a banking lookalike

- Signs up, picks `c1tibank` as username (fails — reserved-words / brand-similarity blocklist).
- Tries `c1ti-secure` (passes; reserved-words list isn't a brand-impersonation detector).
- Enrolls; runs MC; uses MC's `data/uploads` directory to host their phishing HTML.
- Dashboard at `c1ti-secure.PLATFORM_DOMAIN/data/uploads/login.html` works briefly.
- Layer 2 path allowlist allows `/data/uploads/.+` (legitimate users have screenshots there).
- Layer 2 fingerprint sampling (1%) starts catching: response is `text/html` from a path that's normally `image/*`. Counter increments.
- Layer 5 risk score climbs. CF Worker analytics show 100% of visitors are coming from email-link source (no `Referer` from `<username>.PLATFORM_DOMAIN/` itself — they never use the dashboard). Score bumps further.
- Within hours, account auto-throttles. Phishing kit becomes too slow to convert.
- Operator review confirms; account suspended. CF tunnel + DNS deleted. Old hostname returns 404 immediately.
- **Mitigation hardening:** reduce `/data/uploads/.+` allowlist to specifically `image/*` content types (server-side); explicitly deny `.html` / `.htm` / `.js` from uploads. Action item filed.

**Lesson:** the path allowlist is necessary but not sufficient. Content-type enforcement on `/data/uploads/` is added to the v1 launch checklist as a result of writing this scenario.

### 9.4 Crypto miner uses MC for C2

- Sets up MC normally; uses MC's terminal pop-out feature, intending to drive miner instances on user's PC and report back via tunneled API.
- This isn't actually an *abuse of the platform*: it's the user's own PC running their own software, using MC as designed (terminal pop-out is a real feature). They own the PC; they own the bandwidth.
- The platform doesn't try to detect this. If the user is mining on their own hardware and sees their dashboard remotely, that's their business. The TOS (§2 prohibited content) covers illegal use; the platform doesn't enforce mining specifically.

**This is the kind of case worth being explicit about:** abuse prevention does *not* mean turning the platform into a content moderator for what users run on their own PCs. It means stopping people from using the platform itself as the conduit for unrelated traffic.

---

## 10. v1 implementation order

What to build first (cross-references `06-rollout-plan.md`):

1. **Worker with path allowlist + method allowlist + body/header size caps** (week 1 of CP work). Without this, nothing else matters.
2. **Bandwidth metering pipeline** (Worker → analytics → control plane → user record). Daily-aggregate granularity is fine for v1.
3. **Per-account rate limiter** (Durable Object). Enforces cap thresholds.
4. **Free-tier defaults baked in**; admin endpoint for per-user overrides.
5. **Step-up auth flow** (Layer 4) — email OTP signup is already handled by enrollment; SMS + card pre-auth are net-new. Card pre-auth via Stripe.
6. **Risk score job** — start with the heaviest signals (bandwidth, account age, IP variety). Other signals added as we observe abuse patterns.
7. **Operator dashboard** — last because we don't need it until users exist. Until then, ad-hoc Firestore queries + Cloud Logging suffice.

**Defer to v2:**
- Layer 2 fingerprint sampling beyond simple Content-Type checks.
- Sophisticated risk-score signals (linked-accounts, browser fingerprinting).
- Automated phishing-domain detection.
- Bot management beyond CF's built-in.
- Active behavioral fingerprinting on the dashboard side.

---

## 11. Open questions for v1

1. **Username brand-impersonation detection** — basic blocklist (banks, big platforms, "claude", "anthropic", common typo squats) for v1; Levenshtein-distance to brand list for v2?
2. **`/data/uploads` content-type enforcement** — the scenario in §9.3 surfaced this. Decision: enforce server-side in MC (only allow image/* to be served from `/data/uploads/`), or enforce in Worker? Leaning **MC server-side** so it works locally without remote access too.
3. **Step-up auth UI placement** — `account.PLATFORM_DOMAIN` (separate site) or in MC's Remote Access panel? Probably both — make MC link into the browser flow.
4. **Card pre-auth amount** — $1 standard; some abuse-prevention vendors recommend $0.50 to reduce real-user friction; some recommend $5 to weed out smaller-stack abusers. Default $1 unless data says otherwise.
5. **Geographic mismatch threshold** — too aggressive flags travelers; too lenient misses farms. Start at "sustained (>7 days) disjoint country pair" as a minor signal (+5).
6. **Tor signal weighting** — treating Tor as suspicious in any way is at odds with privacy-conscious legitimate users. Default to 0 weight; revisit if abuse patterns warrant.

---

## 12. Cross-references

- Architecture context: `01-architecture.md`
- Why `mc-tunnel` is bound to MC: `02-attestation-protocol.md` §4–5
- API endpoints invoked by the abuse system: `03-control-plane-api.md` admin section
- Error codes returned to users: `error_codes.md` (companion file)
- Build hash revocation flow: `05-build-pipeline.md` (next)
- Rollout sequence: `06-rollout-plan.md` (after that)

---

Next file: `error_codes.md` — the consolidated codebook covering protocol errors (`02-` §10), API errors (`03-` §7), and abuse responses introduced here. Single source of truth for client localization tables.
