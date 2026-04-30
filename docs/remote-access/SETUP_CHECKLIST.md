# Remote Access — Cloud Setup Checklist

**Status:** v0 (M0 prep, 2026-04-28)
**You are here:** GCP project `CLAYRUNE` created. Everything else below is pending.

This checklist is the **operator's parallel work** — clicks and account setup that have to happen in cloud consoles. The code in `control_plane/` can be written and tested locally (Firestore emulator) without any of this; deploying to production requires it.

Tackle items in any order — most are independent. Items that must be ordered are flagged.

---

## 1. GCP project — APIs and services

In the Google Cloud Console with the `CLAYRUNE` project selected:

- [ ] **Confirm the project ID** (shown in the project picker). Save it. The display name is `CLAYRUNE` but the ID is what tools need. Likely `clayrune` or `clayrune-NNNNNN`.
- [ ] **Enable billing.** Even with $0/mo usage, GCP requires a billing account attached for most APIs. Free-tier credits cover everything we need for v1.
- [ ] **Enable APIs** (Cloud Console → APIs & Services → Library):
  - [ ] Cloud Run API
  - [ ] Firestore API
  - [ ] Cloud Build API
  - [ ] Secret Manager API
  - [ ] Cloud Logging API
  - [ ] Identity Toolkit API (Firebase Auth)
  - [ ] IAM API
  - [ ] Cloud KMS API (will use later for build-attestation key)
- [ ] **Pick a region.** Recommend `us-central1` (cheapest, most services). Stick with one region for everything.

## 2. Firestore (the database)

- [ ] **Create database in Native Mode.** Cloud Console → Firestore → Create database → **Native** (NOT Datastore).
- [ ] Region: same as your Cloud Run choice (e.g. `us-central1` → multi-region `nam5` is fine for free tier).
- [ ] Skip security rules for now (Cloud Run will use service-account auth, not direct client access).

## 3. Firebase Auth (browser sign-in)

- [ ] Go to <https://console.firebase.google.com>.
- [ ] **Add Firebase to the existing GCP project** (don't create a new one). Look for a "Add project" → it should let you import `CLAYRUNE`.
- [ ] In the Firebase project: **Authentication → Get started**.
- [ ] **Enable sign-in methods**:
  - [ ] **Google** (recommended primary)
  - [ ] **Email/password** (fallback for users without Google accounts)
  - [ ] *(Later)* Email link / OTP for the recovery flow
- [ ] Note the **Web API key** (Firebase project settings → General). The control plane needs this for verifying ID tokens.

## 4. Cloudflare account + zone

- [ ] **Create a Cloudflare account** if you don't have one (<https://dash.cloudflare.com/sign-up>). Free plan is fine for v1.
- [ ] **Add `clayrune.io` as a zone**. Cloudflare will give you nameserver records.
- [ ] **At your registrar (where you bought clayrune.io)**: change the nameservers to the ones Cloudflare provides. Allow up to 24h for propagation; typically <30 minutes.
- [ ] **Repeat for `clayrune.com`, `clayrune.dev`, `clayrune.ai`** — bring all four under Cloudflare so we can set up redirects later.
- [ ] **Enable DNSSEC** on `clayrune.io` (Cloudflare → DNS → Settings → DNSSEC). One-time, free.

## 5. Cloudflare API token

The control plane needs a CF API token to provision tunnels per user.

- [ ] Cloudflare dashboard → **My Profile → API Tokens → Create Token**.
- [ ] Use the **Custom token** template with these permissions:

  | Permission | Resource |
  |---|---|
  | Zone — DNS — Edit | Include — Specific zone — `clayrune.io` |
  | Account — Cloudflare Tunnel — Edit | Include — All accounts |
  | Account — Access: Apps and Policies — Edit | Include — All accounts |
  | Account — Account Settings — Read | Include — All accounts |

- [ ] Set expiration to 90 days (rotate every 60–90 days; calendar reminder).
- [ ] Save the token securely — Cloudflare shows it **once**. It will go into GCP Secret Manager later.
- [ ] Test the token: `curl -H "Authorization: Bearer <TOKEN>" https://api.cloudflare.com/client/v4/user/tokens/verify` should return `{"success": true, ...}`.

## 6. GCP Secret Manager — store the CF token

After you have the token from §5:

- [ ] Cloud Console → Secret Manager → Create secret `cloudflare-api-token`.
- [ ] Value: the CF API token from §5.
- [ ] Grant the (yet-to-be-created) Cloud Run service account `secretAccessor` on this secret.

## 7. Service account for Cloud Run

- [ ] Cloud Console → IAM & Admin → Service Accounts → Create.
- [ ] Name: `control-plane-sa`.
- [ ] Roles to grant on the project:
  - [ ] `Cloud Datastore User` (Firestore read/write)
  - [ ] `Cloud Run Invoker`
  - [ ] `Logs Writer`
  - [ ] `Secret Manager Secret Accessor` (or scope to the specific secret from §6)

## 8. Workload Identity Federation (for GitHub Actions, defer)

Only needed when CI deploys are wired up. Not blocking v1 dev.

- [ ] Cloud Console → IAM & Admin → Workload Identity Federation → Create pool + provider for GitHub.
- [ ] Bind the `control-plane-sa` to the WIF principal.
- [ ] Output the provider resource name + service account email — those go into GitHub Actions vars.

## 9. Local development tools

For working with the code I'm writing now:

- [ ] **Install gcloud CLI** if you don't have it: <https://cloud.google.com/sdk/docs/install>. After install, `gcloud auth login` and `gcloud config set project <YOUR_PROJECT_ID>`.
- [ ] **Install Java** (OpenJDK 11+ is fine) — required for `gcloud emulators firestore start` (used for local dev without burning quota).
- [ ] **Install Docker Desktop** (or `docker` + `docker-compose`) — used for the local-dev runner I'll build.

---

## Order of operations (recommended)

If you want to do them in a useful order:

1. **§1 + §2** (GCP APIs + Firestore Native mode) — ~5 minutes, unblocks everything.
2. **§4 + §5** (Cloudflare zone + API token) — ~30 minutes including DNS propagation.
3. **§3** (Firebase Auth) — ~10 minutes, can run in parallel.
4. **§6 + §7** (Secret Manager + service account) — ~10 minutes, requires §1 done.
5. **§9** (local tools) — ~30 minutes total install time, can do anytime.
6. **§8** (WIF) — defer until CI deploys.

Total active time: roughly **1.5–2 hours of clicking** spread across 1–2 sessions. Most of the wall-clock time is DNS propagation and waiting for Cloudflare to validate the zone.

---

## Costs (v1 estimate)

All of the above stays within free tiers for the v1 alpha audience:

| Service | Free tier | When it starts costing |
|---|---|---|
| Cloud Run | 2M requests/mo, 360k GB-seconds/mo | Past ~10k active devices doing 1 attestation / 10min |
| Firestore | 1 GiB storage, 50k reads/day, 20k writes/day | Past ~50 active devices |
| Firebase Auth | 50k MAU | Past 50k users |
| Cloudflare (free plan) | Unlimited tunnels, unlimited DNS | Never for what we need |
| Secret Manager | 6 active secret versions | Never for what we need |
| Cloud Logging | 50 GiB/mo, 30-day retention | Never for what we need |
| **Total at v1 alpha (50 users)** | **~$0/mo** |  |

The first paid tier kicks in around the 100-active-user mark, mostly Firestore writes. Plenty of runway.

---

## When you're done (or partially done)

Tell me which sections are complete and I'll:
- Update the relevant config (`control_plane/app/firestore.py`, deployment scripts)
- Replace any local-dev mocks with real GCP clients
- Walk through the first deploy

Until then, the code I've written runs **entirely locally** against the Firestore emulator + httpx-mocked Cloudflare API. You can review it without spending a cent.

---

## Code state as of 2026-04-29

These pieces are **already implemented and tested** locally; they activate against real cloud services as soon as the matching checklist sections are done.

| Code | Activates when… | Status |
|---|---|---|
| `control_plane/app/main.py` | Always (Cloud Run / local) | ✅ Working |
| `control_plane/app/firestore.py` | Reads `FIRESTORE_PROJECT` + auto-detects emulator | ✅ Working |
| `control_plane/app/auth.py` (parse_device_auth) | Always | ✅ Working |
| `control_plane/app/auth.py` (firebase_user) | When §3 is done + firebase-admin SDK is wired | ❌ Stub (NotImplementedError) |
| `control_plane/app/canonical.py` | Always | ✅ Working (uses `rfc8785`) |
| `control_plane/app/verify.py` (14+1 chain) | Against any Firestore | ✅ Working — covered by smoke test |
| `control_plane/app/routes_attest.py` (`/v1/nonce`, `/v1/attest`) | Always | ✅ Working — covered by smoke test |
| `control_plane/app/cloudflare.py` (CF client) | Reads `CLOUDFLARE_API_TOKEN` from §6 | ✅ Working — currently httpx-mocked in tests |
| `control_plane/app/routes_account.py` (`/v1/enroll`) | §3 (Firebase) — OR `MC_CP_DEV_AUTH=1` for dev path | ✅ Working — 7 tests pass |
| `control_plane/seed.py` | Run once after Firestore + the proprietary `mc_remote` are imported | ✅ Working |
| `control_plane/docker-compose.dev.yml` | When you have Docker Desktop installed (§9) | ✅ Working — Firestore emulator |
| `control_plane/tests/test_enroll.py` | Always (no infra needed) | ✅ 7/7 passing |
| `/v1/connect` (browser HTML signin) | After §3 (Firebase) + frontend HTML written | ❌ Stub |
| `/v1/devices`, `/v1/account`, etc. | After core enrollment is live | ❌ Stub |
| Cloud Run deployment script | After §1 + §6 + §7 done | ❌ Not started |
| Workload Identity Federation (CI deploys) | §8 (deferred) | ❌ Not started |

### What runs locally right now (no setup needed)

```bash
# Run the test suite — no GCP, no CF, no Firebase, no Docker required
python -m control_plane.tests.test_enroll
# Expected: 7 passed, 0 failed
```

### What will run locally after §1 + §2 + §9

```bash
# 1. Start the Firestore emulator
docker compose -f control_plane/docker-compose.dev.yml up firestore-emulator -d

# 2. Seed it
FIRESTORE_PROJECT=clayrune-dev FIRESTORE_EMULATOR_HOST=127.0.0.1:8081 \
  python -m control_plane.seed

# 3. Run the control plane locally with dev auth on
FIRESTORE_PROJECT=clayrune-dev FIRESTORE_EMULATOR_HOST=127.0.0.1:8081 \
  MC_CP_DEV_AUTH=1 \
  CLOUDFLARE_API_TOKEN=<your-token-from-§5> \
  uvicorn control_plane.app.main:app --reload --port 8080

# 4. Drive a real enrollment from outside MC:
curl -X POST http://localhost:8080/v1/enroll \
  -H "X-Dev-User-Email: ron@clayrune.io" \
  -H "Content-Type: application/json" \
  -d '{
    "device_pub_b64": "<base64 32 bytes>",
    "csrf_nonce": "test",
    "username": "ron",
    "device_name": "Ron Desktop",
    "os": "win32-11",
    "mc_version": "1.4.2"
  }'
```

This last step provisions a **real** Cloudflare tunnel + DNS + Access app for `ron.clayrune.io`. Pointing your phone at that URL hits Cloudflare Access (you sign in with the email you registered), then forwards to your PC's MC. End-to-end remote access — without a Cloud Run deploy.
