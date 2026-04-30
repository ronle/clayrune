# Path B Runbook — real attestation flow, MC drives the tunnel

**Status:** active dev (2026-04-29)
**Goal:** click "Enable Remote Access" in MC's Settings → MC's keystore-issued
keypair enrolls against the real control plane → supervisor manages cloudflared
+ token rotation → `https://<your-username>.clayrune.io` is reachable from any
browser, end-to-end real.

This is the architectural milestone that proves the entire protocol chain works
against real infrastructure. Path A (`first_enroll_demo.py`) was a sanity check
with a placeholder pubkey — Path B uses MC's actual keystore key.

## Prerequisites (already done as of 2026-04-29)

- ✅ GCP project `clayrune` with Firestore Native + Standard, us-central1
- ✅ Firestore seeded (`versions/1.4.2`, `client_secret_keys/mc-tunnel-dev-2026`)
- ✅ Cloudflare zone `clayrune.io` live
- ✅ Cloudflare API token (`$token` in your PowerShell session)
- ✅ Cloudflare Zero Trust enabled on the account
- ✅ Bundled `cloudflared.exe` at `mc_tunnel/bin/`
- ✅ Demo enrollment cleaned up + MC's keystore wiped

## Three-terminal launch

You need three concurrent processes during dev. Once it's all working, deploying
the CP to Cloud Run collapses this to two (Tauri + cloudflared).

### Terminal 1 — control plane (long-running)

```powershell
$env:CLOUDFLARE_API_TOKEN  = $token
$env:FIRESTORE_PROJECT     = "clayrune"
$env:FIRESTORE_DATABASE    = "default"
$env:MC_CP_DEV_AUTH        = "1"

uvicorn control_plane.app.main:app --port 8080 --reload
```

Expected: `Application startup complete.` + `Uvicorn running on http://127.0.0.1:8080`

Sanity-check from another shell:

```powershell
curl http://localhost:8080/v1/health
# → {"status":"ok","build":"dev","time":"..."}
```

### Terminal 2 — Mission Control (long-running)

**First close any existing MC running** (Tauri or `python server.py`) — env vars
won't take effect mid-process.

```powershell
$env:FIRESTORE_PROJECT       = "clayrune"
$env:FIRESTORE_DATABASE      = "default"
$env:CLOUDFLARE_API_TOKEN    = $token   # mc_remote.cloudflared doesn't need this
                                        # but config.from_env() guards on it
$env:MC_REMOTE_CP_OVERRIDE   = "http://127.0.0.1:8080/v1"
$env:MC_CP_DEV_AUTH          = "1"
$env:MC_REMOTE_DEV_USERNAME  = "ron"     # whatever you want as your handle
$env:MC_REMOTE_DEV_EMAIL     = "leviran1@gmail.com"   # CF Access will gate on this

# IMPORTANT: do NOT set MC_REMOTE_LOCAL_MOCK or MC_DEV_REMOTE_STUB — Path B
# uses the real CP via MC_REMOTE_CP_OVERRIDE.

# Then launch MC however you usually do:
python server.py
# (or via Tauri host with the same env exported in the parent shell)
```

### Terminal 3 — leave alone

`mc_remote/cloudflared.py` will spawn `cloudflared.exe` as a subprocess of MC
once the supervisor gets a tunnel token. You don't run cloudflared manually for
Path B.

## Testing the flow

1. With both Terminal 1 (CP) and Terminal 2 (MC) running, open MC's UI
2. Navigate to **Settings → Remote Access**
3. You should see the "**Mission Control Cloud**" provider, **not enrolled**
4. Click **"Enable Remote Access"**

What happens under the hood:
1. Frontend POSTs `/api/remote/enable` to MC's local Flask
2. `provider.begin_enrollment()` sees `MC_CP_DEV_AUTH=1` + the dev creds → goes direct-API path
3. `enrollment.enroll_via_cp()` generates a fresh Ed25519 keypair (real, not the placeholder)
4. POSTs to `http://127.0.0.1:8080/v1/enroll` with `X-Dev-User-Email`
5. CP provisions a real CF tunnel + DNS + Access app
6. CP returns `enrollment_token` + `device_id` + `hostname`
7. MC persists the identity (pubkey + privkey + token + hostname + username) to Windows Credential Manager
8. `tunnel_supervisor.maybe_start()` is called → spawns the attestation thread + watchdog
9. First attestation tick (~immediate): `/v1/nonce` + `/v1/attest` against real CP
10. CP's attestation chain runs (14+1 steps) against real Firestore
11. Attestation succeeds → returns the cached `cf_tunnel_token` from device row
12. Supervisor calls `cloudflared.swap_token(...)` → spawns `cloudflared.exe` as subprocess
13. cloudflared registers ~3 tunnel connections to CF's edge
14. Settings panel flips to **green Online pill** with live bandwidth

5. Visit `https://ron.clayrune.io` from any device:
   - CF Access prompts for email OTP → enter `leviran1@gmail.com`
   - Get the OTP code → enter it
   - Forwarded to your local MC dashboard
6. **Disconnect button** does a real revoke: stops the supervisor, kills cloudflared, clears keystore. The CF resources stay (since the real `/v1/devices/{id}/revoke` endpoint isn't wired yet — that's a v2 ticket).

## Cleanup

After testing, tear it all down:

```powershell
# In Terminal 2 (MC): click "Disconnect" in Settings → Remote Access
# (clears local keystore + stops supervisor; CF resources persist)

# Then in any PowerShell with the env set:
$env:CLOUDFLARE_API_TOKEN = $token
$env:FIRESTORE_PROJECT    = "clayrune"
$env:FIRESTORE_DATABASE   = "default"
$env:MC_CP_DEV_AUTH       = "1"

python -m control_plane.first_enroll_demo --cleanup --username ron
# Deletes the CF tunnel + DNS + Access app + Firestore rows.
```

## What this validates

After Path B works, every layer of the design from the docs is proven against real infra:

| Layer | Status |
|---|---|
| Browser → CF edge | ✅ |
| CF Access OTP gating | ✅ |
| CF tunnel forwarding | ✅ |
| cloudflared local lifecycle | ✅ via supervisor |
| MC keystore + Ed25519 device key | ✅ via mc_remote.device_keys |
| Real /v1/enroll provisions real CF resources | ✅ |
| Real /v1/nonce + /v1/attest dual-sig verification | ✅ |
| Token rotation every 10 min | ✅ via supervisor |
| Settings panel reflects live state | ✅ |

## Known gaps (deferred)

- **Path B requires MC restart for env var changes** — Tauri inherits env from
  the launching shell. To change `MC_REMOTE_DEV_USERNAME` you have to close +
  relaunch MC.
- **No real /v1/devices/{id}/revoke endpoint yet** — Disconnect clears the local
  keystore but leaves CF resources behind. Run `first_enroll_demo --cleanup`
  manually to wipe CF.
- **CP runs locally on port 8080** — for "always-on remote access," CP needs to
  be on Cloud Run. That's the next-after-Path-B milestone.
- **Browser-mediated enrollment (Firebase signin) is not wired** — Path B uses
  the dev-auth shim. Real users will need the Firebase signin path before public
  alpha.
