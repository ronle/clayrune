# Tunnel — design, capacity, and the roadmap out of Cloudflare

**2026-07-13.** Companion to `01-architecture.md` (the shape) and
`03-control-plane-api.md` (the API). This doc supersedes both wherever they mention
**Cloudflare Access** — Access is being removed.

Facts below are from Cloudflare's own docs, cross-checked against
`clayrune-cloud/docs/STEWARD_HANDOFF.md` §8.

---

## 1. The design, as it should stand

```
  user's machine                      Cloudflare edge                control plane
 ┌──────────────┐    free, unmetered ┌───────────────────┐        ┌───────────────┐
 │ Clayrune     │◄──── tunnel ──────►│  Worker           │◄─JWKS──│ /v1/jwks      │
 │ 127.0.0.1    │                    │  *.clayrune.io    │        │ ES256, rotatable│
 │ + cloudflared│                    │                   │        │               │
 └──────────────┘                    │  authN: verify JWT│        │ /v1/session/  │
                                     │  authZ: claims.u  │        │   start       │
                                     │      === subdomain│        │   refresh ◄── entitlement
                                     │  reserved: api,www│        │   logout      │
                                     └───────────────────┘        └───────────────┘
```

**Three properties that must hold:**

1. **No Cloudflare Access, ever again.** See §2 — it is a margin trap *and* it was
   silently doing our authorization.
2. **The Worker does authZ, not just authN.** `claims.u === subdomain` is the only
   thing stopping `alice` from reaching `bob.clayrune.io`. A JWT that merely proves
   *"you are a Clayrune user"* is not enough. This is the single most important line
   in the whole system.
3. **The control plane never sees user data.** cloudflared terminates TLS at the edge
   and proxies to `127.0.0.1`. That invariant is what makes the product legal
   (`BYOL_TOS_RISK.md`) — do not let any future design put us on the data path.

**Cost:** CF Tunnel is **free and unmetered** — $0/user, no bandwidth charge. The
Worker is **$5/mo flat**, one wildcard route, regardless of user count.

---

## 2. Why Access is gone (do not reintroduce it)

| | |
|---|---|
| **Price** | Free to 50 users, then **$7/user/mo for all users**. At $6.99/mo revenue that is **negative margin on every single customer.** |
| **Cap** | 500 Access apps. Lower than the tunnel cap — it was the *real* ceiling. |
| **The trap** | `03-control-plane-api.md` §3.5 provisioned **one Access app per user.** 50 free-beta users trips the cliff at **$357/mo with zero revenue.** |

⚠️ **The non-obvious part:** Access was doing **authorization**, not just
authentication. Its per-user email policy is what enforced tenant isolation. Deleting
Access without moving that check into the Worker doesn't cost money — **it opens every
user's dashboard to every other user.** The Worker's `claims.u === subdomain` check
*is* the replacement. Treat it as a security control, not a routing detail.

**Status:** control-plane half done (`837d6d7` — JWKS, session JWT, revocable refresh
store, `is_entitled` at refresh). Worker in `clayrune-cloud/edge-worker/`.
Open: backlog `1e5feb38` (reserved-subdomain bypass + SSE/WS lifetime cap),
`ee94a17e` (mobile pairing still on Access service tokens — **blocks phones, and
therefore blocks the demo clip**).

---

## 3. The ceiling — and it bites exactly when we start winning

**Hard limits, per Cloudflare account:**

- **1,000 tunnels**
- **1,000 DNS routes**

One Connect user = one tunnel + one route. **So: ~1,000 users per CF account.** At
$6.99 that is a wall at roughly **$5.6k/mo** — i.e. it lands precisely at the moment
the business first looks like a business. There is no way to trick this with a
wildcard CNAME: each user's `cloudflared` is a distinct connector and needs its own
route.

### 3.1 Do this now — the quota alarm at 800 (an afternoon)

Turns a cliff into a **scheduled decision**. Cron job → CF API:

```
GET /accounts/{id}/cfd_tunnel?is_deleted=false   → count
GET /zones/{zone}/dns_records                    → count
```

Alert at **800** of either. Email via `tools/night-review/send_mail.py`
(`[Clayrune ops] BLOCKED: tunnel quota at N/1000`). Filed to backlog.

### 3.2 The bridge nobody has named: **shard across CF accounts**

Before we consider a Rust rewrite, note that the limit is **per account**, not per
zone. A second CF account with the same zone delegation gives another 1,000 tunnels.
The control plane already picks the tunnel's home — it can pick an *account* too:
store `cf_account_id` on the user record, round-robin new enrollments.

**Ugly. Correct. Buys 10×.** It is a day of control-plane work, not a quarter of
systems work, and it means **hitting 1,000 users never forces a Rust rewrite under
schedule pressure.** That is the entire point: never be in a position where growth is
the emergency.

---

## 4. `mc-tunnel` — design it, do NOT build it

**Trigger: ≥500 Connect users** (or a CF pricing/policy change that breaks §1).

**Why waiting is correct, plainly:** CF is already **$0/user**. An in-house Rust relay
**saves no COGS at all** — its only benefit is removing the ceiling. Building it early
buys us an **uptime SLA we'd have to honor** and an **abuse surface we'd have to
police**, before there is one dollar of revenue or one user who cares. That is a
strictly worse position.

**Waiting costs zero architectural debt** because `mc_remote_iface/` is already the
seam: a `RemoteAccessProvider` Protocol, a registry, single-provider invariant, and a
`dev_stub`. Swapping the CF provider for an `mc_tunnel` provider is an import change
in `server.py`, not a refactor. **Preserve that seam** — any code that reaches around
it and talks to Cloudflare directly is the debt we're claiming not to have.

**Economics when it does land:** ~$15–30/mo **flat**, all users, ceiling gone.

**Design constraints to honor when the trigger fires** (so the future build has a
spec, not a blank page):

- Implements `RemoteAccessProvider` verbatim. No new seam.
- Same authZ model: the relay enforces `claims.u === subdomain`, verified against the
  control plane's JWKS. **Never invent a second auth model.**
- Same data-path invariant: relay proxies bytes, never inspects, never stores.
- Abuse: per-user rate limit + bandwidth quota reported through `ProviderCaps` (the
  DTO already exists — `bandwidth_quota_period_bytes`, `rate_limit_rps`, …).
- Ship a real SLA or don't ship. This is the actual cost of leaving CF.

---

## 5. What to do, in order

1. **Finish Access removal** — `1e5feb38`, `ee94a17e`. `ee94a17e` blocks phones, and
   phones block the demo clip, and the demo clip blocks the entire launch.
2. **Quota alarm at 800** (§3.1) — an afternoon.
3. **CF account sharding** (§3.2) — when the alarm first fires, not before.
4. **`mc-tunnel`** — at ≥500 Connect users. Not one user sooner.
