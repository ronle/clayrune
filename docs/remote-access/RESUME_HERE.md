# RESUME HERE — picking up after reboot

**Last updated:** 2026-04-30 (mid-day, Firebase Auth wired, awaiting end-to-end test)
**You are here:** Everything from yesterday is live + custom domain + auto-deploy CI/CD. Firebase Auth flow is now built and the Cloud Run service has the three FB env vars set. Firebase project setup is complete on the user's side (Google signin enabled). **Pending: the actual end-to-end disconnect-and-reconnect test from a fresh MC shell without `MC_CP_DEV_AUTH=1`.** The dev-shim path (current default) still works fine; nothing breaks if you ignore Firebase Auth and keep using the dev path.

Cloud Run state:
- Custom domain: `https://api.clayrune.io/v1` (canonical), `*.run.app` URL also works.
- Latest revision: `control-plane-00011-b2h` (env-var update); image: `:5aa7d12` then `:59cb1fb` from CI auto-deploys.
- Env vars set: `FB_API_KEY=AIzaSyCcBU0GKtnKgNw3EiNYoMri6OVdnW8188s`, `FB_AUTH_DOMAIN=clayrune-49e57.firebaseapp.com`, `FB_PROJECT_ID=clayrune-49e57`.

Firebase project: `clayrune-49e57` (NOT `clayrune` — Firebase auto-suffixed because the bare name was taken). Lives at https://console.firebase.google.com/project/clayrune-49e57. Google sign-in enabled. App nickname "Clayrune".

---

## ☀️ RESUME CHECKLIST — pick up where we stopped

### Option A — Keep using the dev-shim path (no risk; current default)

Just relaunch MC normally with the existing env vars:

```powershell
cd C:\Users\levir\Documents\_claude\mission-control
$env:MC_REMOTE_CP_OVERRIDE   = "https://api.clayrune.io/v1"
$env:MC_CP_DEV_AUTH          = "1"
$env:MC_REMOTE_DEV_USERNAME  = "ron"
$env:MC_REMOTE_DEV_EMAIL     = "leviran1@gmail.com"
python server.py
```

Settings → Remote Access reappears green Online; existing session keeps working. Nothing changed for this path. Use this if you want to verify yesterday's work is still intact before touching the Firebase flow.

### Option B — Run the Firebase Auth end-to-end test

This is the pending milestone. It exercises the new browser-mediated signin path that replaces the dev shim. **DESTRUCTIVE: it disconnects the current `ron` enrollment and re-creates it via real Firebase signin.** If you want to keep the existing enrollment, skip this.

1. **Disconnect** — in MC, Settings → Remote Access → Disconnect. This frees username `ron`, deletes CF tunnel/DNS/Access app, wipes the local keystore.

2. **Restart MC without `MC_CP_DEV_AUTH`** — close MC, reopen with env vars but **omit** the dev-shim ones:
   ```powershell
   cd C:\Users\levir\Documents\_claude\mission-control
   $env:MC_REMOTE_CP_OVERRIDE = "https://api.clayrune.io/v1"
   Remove-Item Env:MC_CP_DEV_AUTH         -ErrorAction SilentlyContinue
   Remove-Item Env:MC_REMOTE_DEV_USERNAME -ErrorAction SilentlyContinue
   Remove-Item Env:MC_REMOTE_DEV_EMAIL    -ErrorAction SilentlyContinue
   python server.py
   ```

3. **Click Enable Remote Access** in MC. Default browser opens to `https://api.clayrune.io/v1/connect?pub=...&nonce=...&callback=http://127.0.0.1:5199/api/mc-callback`. You should see "Connect this Mission Control" with a "Sign in with Google" button.

4. **Sign in with Google** (use `leviran1@gmail.com`). After signin, the page shows a username field. Type "ron" → click Connect.

5. **Browser auto-redirects** to `http://127.0.0.1:5199/api/mc-callback?...` and shows "You're connected!"

6. **Back in MC dashboard**, Settings → Remote Access shows green Online pill. Mobile sign-in to `https://ron.clayrune.io` should work as before.

### What if Option B fails

| Symptom | Likely cause | Fix |
|---|---|---|
| `/v1/connect` page says "Server misconfigured: Firebase apiKey not set" | Cloud Run env vars dropped | Re-run: `gcloud run services update control-plane --region=us-central1 --project=clayrune --update-env-vars="FB_API_KEY=AIzaSyCcBU0GKtnKgNw3EiNYoMri6OVdnW8188s,FB_AUTH_DOMAIN=clayrune-49e57.firebaseapp.com,FB_PROJECT_ID=clayrune-49e57"` |
| "Sign-in cancelled or failed" popup | Google signin not enabled in Firebase, or browser blocked the popup | Firebase console → Authentication → Sign-in method → Google → Enable + support email |
| `/v1/signin/complete` returns "Sign-in token invalid: ..." | `firebase_admin` initialized with wrong projectId | `gcloud run services describe control-plane --region=us-central1 --project=clayrune --format="value(spec.template.spec.containers[0].env)"` — confirm `FB_PROJECT_ID=clayrune-49e57` is set |
| Browser doesn't open at all | `_launch_browser_for_user()` returned False | Copy the URL from MC's stdout/Tauri console, paste into a real browser manually |
| "username_taken" error | The `ron` Disconnect step in (1) didn't fully release the claim | Run `python -m control_plane.force_cleanup --username ron` (CLOUDFLARE_API_TOKEN + FIRESTORE_PROJECT must be set in shell) |
| "enrollment_intent_invalid" | Browser tab took >15min between /signin/start and /signin/complete | Click Enable Remote Access again to mint a fresh nonce |

### Where the code is

- `control_plane/app/routes_public.py` — `/v1/connect` HTML + `/v1/signin/start` + `/v1/signin/complete`
- `control_plane/app/routes_account.py` — `_verify_firebase_token()` (uses `firebase_admin.auth.verify_id_token()`), `_do_enroll_after_auth()` (shared with `/v1/enroll`)
- `mc_remote/config.py` — `connect_url()` builds `<cp>/v1/connect?pub=...&nonce=...&callback=...`
- `mc_remote/enrollment.py` — `complete()` parses callback query → persists keystore
- `server.py` — `/api/mc-callback` route renders the success/failure HTML

### What's NOT yet done

- **Operator dashboard** — read-only `/admin` view showing all enrolled devices + sessions across users. ~30 min.
- **Re-merge `mode-c-audio` branch** — Mode C interactive agent + voice STT/TTS lives on a separate branch; needs rebase + conflict resolution in `static/index.html`.
- **CF Access Audit Logs scope** — would let us enrich session list with UA/IP/country without the manual "Name this device" page. Token currently lacks this scope.

---

## TL;DR — fastest path to a working tunnel after a fresh reboot

The control plane is now on Cloud Run, so you only need **one terminal**: Mission Control itself.

### Terminal 1 — Mission Control

Close any existing MC first. Then:

```powershell
cd C:\Users\levir\Documents\_claude\mission-control

$env:MC_REMOTE_CP_OVERRIDE   = "https://api.clayrune.io/v1"
$env:MC_CP_DEV_AUTH          = "1"
$env:MC_REMOTE_DEV_USERNAME  = "ron"
$env:MC_REMOTE_DEV_EMAIL     = "leviran1@gmail.com"

# CRITICAL — make sure these are NOT set:
Remove-Item Env:MC_REMOTE_LOCAL_MOCK -ErrorAction SilentlyContinue
Remove-Item Env:MC_DEV_REMOTE_STUB   -ErrorAction SilentlyContinue

python server.py
```

(Or via Tauri host with the same env in the parent shell.)

**No more `gcloud secrets ...` for `CLOUDFLARE_API_TOKEN`** — that's now bound directly to the Cloud Run service via `--set-secrets`. MC doesn't need to know it.

**No more `FIRESTORE_PROJECT` / `FIRESTORE_DATABASE` env on MC** either — MC only talks to the deployed CP via HTTP; only the CP touches Firestore.

### Terminal 2 — leave free

The supervisor inside MC will spawn `cloudflared.exe` as a subprocess. You'll see its log lines interleaved with MC's stdout. No manual run needed.

### Then in MC

Settings → Remote Access → click **Enable Remote Access**.

If you were already enrolled before the reboot (keystore persists), MC's `mc_remote/__init__.py:_maybe_register` will auto-start the supervisor on boot — no click needed. Just visit the Settings panel and confirm green "Online" pill.

If not enrolled, the click triggers the direct-API path (no browser), provisions real CF resources, persists to keystore, starts the supervisor, spawns cloudflared. Tunnel up in ~5 seconds.

---

## Where each piece of state lives

| State | Location | Survives reboot? |
|---|---|---|
| MC's Ed25519 device keypair + enrollment_token + username + hostname | Windows Credential Manager (`mission-control-remote` service) | ✅ |
| Firestore (`users/`, `devices/`, `versions/`, `client_secret_keys/`, etc.) | GCP project `clayrune`, database `default` | ✅ |
| Cloudflare zone + tunnel + DNS + Access app | CF account, zone `clayrune.io` | ✅ |
| **CF API token** | **GCP Secret Manager: `cloudflare-api-token` in project `clayrune`**, bound to Cloud Run service via `--set-secrets` | ✅ |
| **Control plane (FastAPI app)** | **Cloud Run service `control-plane` in `us-central1`** | ✅ — always-on, no restart needed |
| MC server process (port 5199) | `python server.py` or Tauri | ❌ — restart |
| cloudflared subprocess | spawned by MC's tunnel_supervisor | ❌ — auto-spawned when supervisor restarts |

---

## What changed in this session (2026-04-30)

| Area | Change |
|---|---|
| CP `/v1/sessions` | Added `nonce` field to flatten output for joining with MC-side labels (deployed v5/v6) |
| CP `/v1/sessions/{id}/revoke` | Added `?strict=1` mode that returns 503 instead of falling back to revoke-all. Tries 4 known CF API shapes (POST/DELETE × full-name/nonce-only) before giving up (deployed v8 — v7 had a `Query` import bug) |
| MC server | New label storage at `data/session_labels.json` keyed by CF Access nonce |
| MC server | New `before_request` hook redirects unlabeled CF-tunneled requests (excluding `/api/*`, `/static/*`, `/_mc/*`) to `/_mc/name-device` |
| MC server | New page `/_mc/name-device` — standalone HTML form with UA-derived suggestion chips (My iPhone / My Mac / etc.); POSTs to `/api/_mc/session-label` |
| MC server | New `POST /api/remote/sessions/<id>/label` for retroactive renaming (extracts nonce from session_id; localhost-only, no CF auth needed) |
| MC server | New daemon thread `_session_label_enforcer_loop` (60s interval) calls strict per-session revoke for unnamed sessions older than 10 min. Aborts pass on first `per_session_unsupported` to protect named sessions. Config: `auto_revoke_unnamed_sessions` (default true), `auto_revoke_unnamed_after_seconds` (600), `auto_revoke_check_interval_seconds` (60) |
| MC server | New endpoints `POST /api/remote/sessions/enforce` (manual trigger) + `GET /api/remote/sessions/enforcer-state` |
| Frontend | `refreshRemoteSessions()` leads with `s.label` when present; falls back to clickable "Name this session…" link. Shows parsed UA, relative time, ago/expires, app count |
| Frontend | New `renameRemoteSession(sid, current)` handler — `prompt()` to enter or rename a label |
| Frontend | "Clean up unnamed" + "Sign out everywhere" buttons; "Auto-cleanup ran Xm ago · per-session revoke unsupported by CF" status line |

**CF reality:** All 4 per-session revoke API shapes return HTTP 405 — CF Access does not expose per-session revoke for our current token/account configuration. The strict-mode auto-loop is wired and safe (will not nuke named sessions) but cannot actually revoke individual unnamed sessions. Workflow: name new sessions via the device-naming page (works), use "Sign out everywhere" for stuck unnamed strays.

---

## What works as of 2026-04-30 end-of-session

- ✅ **Control plane deployed to Cloud Run** — always-on, free-tier-friendly (`min-instances=0`); reboot-resilient
- ✅ Real `/v1/enroll` against real CP → real CF tunnel + DNS + Access app + Firestore rows
- ✅ Real `/v1/nonce` + `/v1/attest` (14+1 step verification chain)
- ✅ Real `/v1/devices/{id}/revoke` — Disconnect button cleanly tears down CF resources + Firestore row + username claim
- ✅ **Self-healing `/v1/enroll`** — automatically wipes any pre-existing CF/Firestore state for the target hostname before creating new resources. Re-enrollment of same username is now idempotent.
- ✅ MC keystore generates real Ed25519 keypair; supervisor signs envelopes with both device key + dev client secret
- ✅ `cloudflared.exe` bundled at `mc_tunnel/bin/`, spawned by supervisor with issued tunnel token
- ✅ Settings panel reflects live state with **four distinct pill colors**:
  - **green Online** — tunnel up
  - **gray Offline** — paused (supervisor stopped)
  - **blue Connecting…** — supervisor running, attestation in progress (no false "paused" warning)
  - **red Error** — attestation failure or cloudflared crash
- ✅ Pause / Resume / Disconnect buttons work end-to-end with full revoke
- ✅ **Copy link** button works in Tauri/WebView2 (uses `copyToClipboardSafe()` with textarea fallback)
- ✅ **Resume** transitions cleanly via optimistic UI — no yellow-paused flicker during the 1–5s reconnect window
- ✅ Self-recovering panel — any "Connecting…" state auto-polls until stable (no more stuck panels after enrollment)
- ✅ `https://ron.clayrune.io` reachable from any device after CF Access OTP signin
- ✅ Token rotation every 10 min via supervisor
- ✅ Cloudflared crash detection via watchdog (5s tick)
- ✅ CF API token in GCP Secret Manager — no more session-bound `$token` paste
- ✅ **End-to-end orphan-free cycle** — enroll → disconnect → re-enroll without any manual cleanup needed

---

## What changed in this session (2026-04-29)

Beyond the Path B backbone, polish work:

| Area | Change |
|---|---|
| Clipboard | Added `copyToClipboardSafe(text, toast)` helper in `static/index.html` with hidden-textarea + `execCommand('copy')` fallback for WebView2 |
| Provider interface | Added `resume()` method to `RemoteAccessProvider` Protocol (idempotent restart for an already-enrolled device) — implemented in `ClayruneProvider` and `dev_stub` |
| Server route | New `POST /api/remote/resume` in `server.py` — returns 409 `not_enrolled` if no keystore identity |
| UI | New "Resume" button when enrolled+offline; disabled "Connecting…" indicator while reconnecting |
| Status semantics | New `connecting: bool` field on `ProviderStatus` — distinguishes "intentionally paused" from "actively reconnecting"; surfaced in `/api/remote/status` JSON |
| UI | New blue "Reconnecting…" notice + pill state during the connecting window (replaces the misleading yellow "Tunnel paused" notice) |
| UI | `resumeRemoteAccess()` now optimistically flips local state to `connecting=true` immediately on click + polls 4× over 10s to catch real online state |
| Secrets | CF API token moved to GCP Secret Manager (`cloudflare-api-token` in `clayrune`) — no more PowerShell-session-bound `$token` |
| Cleanup tooling | Demonstrated orphan-CF-resource cleanup recipe via curl (Access apps + tunnels + DNS + Firestore device rows) |

---

## What's NOT yet wired

| Gap | Workaround |
|---|---|
| ~~Custom domain `api.clayrune.io`~~ | **DONE 2026-04-30 AM.** Cert provisioned overnight, all conditions True. CF Origin Rules path was abandoned (paid CF plan required for Host-header override); Google-managed cert + DNS-only CNAME `api.clayrune.io → ghs.googlehosted.com` is the working setup. CF doesn't proxy this hostname (no WAF), which is acceptable for the API surface. |
| Browser-mediated enrollment (Firebase signin) | Dev shim via `MC_CP_DEV_AUTH=1` + `X-Dev-User-Email` works fine for solo dev |
| `/v1/connect` HTML signin page | Stub. Direct API enrollment via `enroll_via_cp()` is the working path today |
| Public-facing landing page on `clayrune.io` root | Deferred |
| Real Firebase Auth | Deferred to public-alpha milestone |
| `/v1/devices` listing endpoint (read all enrolled devices) | Deferred — only `/v1/devices/{id}/revoke` is implemented |
| Operator dashboard | Deferred |

---

## Common failure modes + fixes

### "Module not found: google.cloud.firestore" in CP

Wrong Python. Use `python -m uvicorn ...`, not bare `uvicorn` (which is Python 3.12's shim). Or use the explicit path:
```
C:\Users\levir\AppData\Local\Python\pythoncore-3.14-64\python.exe -m uvicorn control_plane.app.main:app --port 8080
```

### Orphan CF resources from a failed prior attempt

Symptoms during retry of `/v1/enroll`:
- HTTP 400 `81053: An A, AAAA, or CNAME record with that host already exists`
- HTTP 409 `11010: access.api.error.application_already_exists`

Fix — fetch token from Secret Manager + curl through all three resource types. Run from **PowerShell** with the username adjusted:

```powershell
$env:CLOUDFLARE_API_TOKEN = (gcloud secrets versions access latest --secret=cloudflare-api-token --project=clayrune)
$user = "ron"
$account = "211d1929ec33d2518e12bc9079998bfb"
$zone = "d3550fdb6fd83f01549f1f538b4ca670"

# 1. List orphan Access apps for <user>.clayrune.io
curl.exe -H "Authorization: Bearer $env:CLOUDFLARE_API_TOKEN" "https://api.cloudflare.com/client/v4/accounts/$account/access/apps" | ConvertFrom-Json | Select-Object -ExpandProperty result | Where-Object { $_.domain -like "*$user.clayrune.io*" } | Format-List id,name,domain
# Then DELETE each:
$appId = "<paste-id>"
curl.exe -X DELETE -H "Authorization: Bearer $env:CLOUDFLARE_API_TOKEN" "https://api.cloudflare.com/client/v4/accounts/$account/access/apps/$appId"

# 2. List orphan tunnels named mc-<user>-*
curl.exe -H "Authorization: Bearer $env:CLOUDFLARE_API_TOKEN" "https://api.cloudflare.com/client/v4/accounts/$account/cfd_tunnel" | ConvertFrom-Json | Select-Object -ExpandProperty result | Where-Object { $_.name -like "mc-$user-*" -and $_.deleted_at -eq $null } | Format-List id,name
# Then DELETE each:
$tunnelId = "<paste-id>"
curl.exe -X DELETE -H "Authorization: Bearer $env:CLOUDFLARE_API_TOKEN" "https://api.cloudflare.com/client/v4/accounts/$account/cfd_tunnel/$tunnelId/connections"
curl.exe -X DELETE -H "Authorization: Bearer $env:CLOUDFLARE_API_TOKEN" "https://api.cloudflare.com/client/v4/accounts/$account/cfd_tunnel/$tunnelId"

# 3. List orphan DNS records for <user>.clayrune.io
curl.exe -H "Authorization: Bearer $env:CLOUDFLARE_API_TOKEN" "https://api.cloudflare.com/client/v4/zones/$zone/dns_records?name=$user.clayrune.io" | ConvertFrom-Json | Select-Object -ExpandProperty result | Format-List id,name,type,content
# Then DELETE each:
$recordId = "<paste-id>"
curl.exe -X DELETE -H "Authorization: Bearer $env:CLOUDFLARE_API_TOKEN" "https://api.cloudflare.com/client/v4/zones/$zone/dns_records/$recordId"

# 4. Wipe stale Firestore device rows for that hostname:
$env:FIRESTORE_PROJECT="clayrune"; $env:FIRESTORE_DATABASE="default"
python -c "
import sys; sys.path.insert(0, '.')
from control_plane.app import firestore as fs
docs = list(fs.db().collection(fs.COL_DEVICES).where('hostname_claim','==','ron.clayrune.io').stream())
for d in docs:
    fs.db().collection(fs.COL_DEVICES).document(d.id).delete()
    print(f'deleted devices/{d.id}')
ref = fs.db().collection('usernames').document('ron')
if ref.get().exists: ref.delete(); print('deleted usernames/ron')
"

# 5. Optionally clear MC's local keystore (defensive):
python -c "import sys; sys.path.insert(0, '.'); from mc_remote import device_keys; device_keys.clear_identity(); print('keystore cleared')"
```

This whole recipe should become an automated `force_cleanup` tool — that's #3 on the next-session priority list.

### "Tunnel paused" yellow notice but you didn't pause

(Should be rare after this session's fix.) Supervisor's first attestation hasn't completed yet, or cloudflared crashed. Check live state:
```powershell
curl http://127.0.0.1:5199/api/remote/status | ConvertFrom-Json | Format-List
```

| Status | Meaning |
|---|---|
| `connecting=true` | Reconnecting (transient — UI now shows blue "Reconnecting…" notice not yellow) |
| `running=false online=false error_code=null` | Genuinely paused |
| `running=true online=false error_code='tunnel_cloudflared_down'` | cloudflared crashed |
| `error_code='unknown_device'` | Device row in Firestore missing — keystore is stale; Disconnect + re-Enable |
| `error_code='bad_signature'` etc. | Attestation rejected by CP — check Terminal 1 logs |

### Settings panel won't refresh after restart

Hard-refresh the dashboard (Ctrl+Shift+R) — the JS state is cached.

### "Two MCs running on the same port" symptoms

Don't run a second `python server.py` while Tauri's MC is running. Now fatal — you'll see a clear error message at startup. If you genuinely need both, set `MC_ALLOW_PORT_CONFLICT=1` (rare).

---

## CF + GCP resource IDs (current; for reference)

| Resource | ID |
|---|---|
| GCP project ID | `clayrune` |
| GCP project number | `189381911926` |
| Firestore DB id | `default` (literal, not the special `(default)`) |
| Cloudflare account ID | `211d1929ec33d2518e12bc9079998bfb` |
| Cloudflare zone ID (clayrune.io) | `d3550fdb6fd83f01549f1f538b4ca670` |
| GCP Secret Manager key for CF token | `cloudflare-api-token` (project `clayrune`) |
| Cloud Run service | `control-plane` (region `us-central1`) |
| Cloud Run service URL | `https://control-plane-189381911926.us-central1.run.app` |
| Cloud Run service account | `control-plane-sa@clayrune.iam.gserviceaccount.com` |
| Container image | `us-central1-docker.pkg.dev/clayrune/mc-cloud/control-plane:v8` (active) |
| Artifact Registry repo | `mc-cloud` (region `us-central1`) |
| Bundled cloudflared version | `2026.3.0` |

---

## Where everything is in the codebase

| File | What it does |
|---|---|
| `mc_remote_iface/` | OPEN-source provider interface (Protocol class with `resume()`, `register_provider`, `dev_stub`, `ProviderStatus` with `connecting:bool`) |
| `mc_remote/` | PROPRIETARY provider implementation (config, device_keys, enrollment, attestation, tunnel_supervisor, cloudflared, provider_impl) |
| `mc_tunnel/bin/cloudflared.exe` | Bundled CF tunnel binary (Authenticode-verified at build time) |
| `control_plane/app/` | FastAPI control plane (main, routes_public, routes_attest, routes_account, auth, verify, firestore, cloudflare, schemas) |
| `control_plane/seed.py` | Idempotent seeder for `versions/` + `client_secret_keys/` (run once per environment) |
| `control_plane/first_enroll_demo.py` | Path A demo + cleanup tool (works today via TestClient) |
| `control_plane/bring_tunnel_up.py` | Path A: just runs cloudflared with a stored tunnel token |
| `control_plane/tests/test_enroll.py` | 7 unit tests for `/v1/enroll`; runs via in-memory Firestore + CF httpx mock |
| `server.py` (Flask) | `/api/remote/{status,enable,disable,resume,disconnect}` + `/api/tunnel-handshake` + `/api/mc-callback` |
| `static/index.html` | Settings → Remote Access panel + `copyToClipboardSafe()` + `resumeRemoteAccess()` |
| `docs/remote-access/01-…07-` | Full design docs (architecture, attestation protocol, API, abuse, build, rollout, licensing) |
| `docs/remote-access/SETUP_CHECKLIST.md` | GCP/CF/Firebase setup checklist (most done; §6-§8 deferred) |
| `docs/remote-access/PATH_B_RUNBOOK.md` | The full Path B launch runbook |
| `docs/remote-access/RESUME_HERE.md` | This file |

---

## How to redeploy the control plane after a code change

```powershell
cd C:\Users\levir\Documents\_claude\mission-control\control_plane

# 1. Build + push (incrementing tag)
gcloud builds submit . `
  --tag us-central1-docker.pkg.dev/clayrune/mc-cloud/control-plane:v2 `
  --project=clayrune

# 2. Deploy the new image (preserves env vars + secrets from previous deploy)
gcloud run deploy control-plane `
  --image=us-central1-docker.pkg.dev/clayrune/mc-cloud/control-plane:v2 `
  --region=us-central1 `
  --project=clayrune
```

If you need to change env vars or secrets, redeploy with full flags (see `--set-env-vars` and `--set-secrets` in the original deploy in this doc's session log).

## What to do next session

**Updated 2026-04-29 (end of Cloud Run deploy session).** The Cloud Run deploy is done — control plane is reboot-resilient. Top priority is now custom domain or polish.

In rough priority order:

1. ✅ **Custom domain `api.clayrune.io`** — DONE 2026-04-30 (cert may still be provisioning at session end; AM checklist above handles the cutover). Used Cloud Run domain mapping with Google-managed cert, not CF Origin Rules.

2. ✅ ~~**Performance**~~ — DONE 2026-04-30. force_cleanup was already collision-only; added CP-warmup ping at MC startup to mask Cloud Run cold-start. Warm CP responses ~330ms.

3. ✅ **`force_cleanup` admin tool** — DONE 2026-04-30. `python -m control_plane.force_cleanup --username ron [--dry-run] [--keep-username]`. Tested clean.

4. **Operator dashboard + `/v1/devices` listing** — `/v1/devices` listing endpoint already exists; the dashboard UI doesn't. ~30 min to wire a read-only `/admin` page in MC that hits `/v1/devices` + `/v1/sessions` and displays a table.

5. **Browser-mediated enrollment via Firebase Auth** — for the eventual public alpha. Setup is in `SETUP_CHECKLIST.md` §3. Bigger lift (~1-2h) but unblocks anyone who isn't us from enrolling.

6. ✅ **CI/CD for Cloud Run** — DONE 2026-04-30 AM. `.github/workflows/deploy-control-plane.yml` builds + deploys via WIF on every push to main that touches `control_plane/**`. Image is built with `docker build` directly on the GitHub runner (NOT `gcloud builds submit`) because Cloud Build's source-upload bucket has legacy IAM that doesn't grant access to WIF principals — the error message misleadingly suggests granting `serviceusage.services.use` but that permission is fine; the bucket itself is the blocker. Direct docker push avoids the bucket entirely. First auto-deploy: revision `control-plane-00009-z4w` from commit `0922dd0`. Tradeoff: ~3-5 min builds vs ~50s in Cloud Build, acceptable.

7. **Re-merge `mode-c-audio` branch** — Mode C interactive agent + voice STT/TTS lives on a separate branch (`mode-c-audio`). Needs rebase onto current master and conflict resolution in `static/index.html` (tile redesign + Advanced-features flags collide).

8. **CF Access Audit Logs scope** (deferred) — would let us enrich session list with user_agent / IP / country without the manual "Name this device" page. Token currently lacks this scope; user has been resistant to more token edits.

### History of the orphan loop (now resolved)

**Session 1 (Path A demo):** Used `first_enroll_demo.py` to provision CF resources for `ron.clayrune.io`. Cleaned up cleanly via `--cleanup` afterward.

**Session 2 (Path B):** Multiple failed enrollment attempts during Python-3.12-vs-3.14 debugging left orphan CF resources. We cleaned manually via curl → `application_already_exists` resolved → green Online.

**Session 3 (post-reboot):** Reboot → MC keystore was already empty (wiped during Session 2 cleanup) → MC tried fresh enrollment → collided with Session 2's CF resources that were never wiped because Disconnect didn't touch CF. Manual cleanup again → resolved.

**Session 4 (this session, 2026-04-29):** Shipped the fix. Two changes:
- `_force_cleanup_for_hostname()` runs at the START of every `/v1/enroll` — wipes any pre-existing CF resources or stale Firestore device rows for the target hostname before creating new ones. Re-enrollment of same username is now idempotent.
- `POST /v1/devices/{id}/revoke` is real and wired into MC's Disconnect button. Disconnect now: (1) stops supervisor, (2) calls `/v1/devices/{id}/revoke` with the device's enrollment_token (server-side auth), (3) server deletes Access app + DNS + tunnel + Firestore row + releases username claim, (4) MC clears local keystore. End-to-end clean teardown.

**Verified end-to-end 2026-04-29**: enroll → disconnect → re-enroll cycle works without ANY manual cleanup. No orphans, no manual curl, no Firestore surgery. The `gcloud secrets ... | curl DELETE` recipe in this doc is now only needed if you want to wipe state during dev (e.g. cleaning up a test username) — it's no longer a recovery procedure for normal flows.

---

## Quick smoke test after reboot

To verify the whole stack came back up cleanly after reboot, in order:

```powershell
# 1. CP healthy?
curl http://127.0.0.1:8080/v1/health | ConvertFrom-Json

# 2. MC's view of remote state?
curl http://127.0.0.1:5199/api/remote/status | ConvertFrom-Json | Format-List

# 3. Tunnel actually up at the edge?
# (Open https://ron.clayrune.io from your phone — should hit CF Access OTP, then dashboard)
```

If (1) returns 200 + (2) shows `online: True` + (3) reaches your dashboard → you're back to the same state we left at end-of-session.
