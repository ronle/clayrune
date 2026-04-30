# control_plane — Mission Control Cloud control plane (`api.clayrune.io`)

**License:** PROPRIETARY AND CONFIDENTIAL. Not part of the open-source MC core.
**Stack:** Python 3.11 + FastAPI + Firestore + Cloud Run.
**Status:** v0.1 skeleton. See `docs/remote-access/06-rollout-plan.md` M1.

## What this is

The hosted service that:
- Authenticates users (Firebase Auth) at signup.
- Provisions Cloudflare tunnels + DNS + Access apps for each enrolled device.
- Issues short-lived tunnel tokens to `mc-tunnel` clients on every attestation.
- Holds the platform client-secret pubkey allowlist (per-`mc-tunnel` release).
- Tracks per-user bandwidth & rate-limit caps; pushes them to the CF Worker.

It does **not** see, proxy, or store user dashboard contents (per `04-` §7).

## Files

| Path | Purpose |
|---|---|
| `api_spec.yaml` | OpenAPI 3.1 contract (authoritative) |
| `app/` | FastAPI app (skeleton) |
| `app/main.py` | ASGI entrypoint, routes registered here |
| `app/auth.py` | Firebase ID token verification, device-signature auth dependency |
| `app/routes_public.py` | `/v1/health`, `/v1/connect`, `/v1/signin/*` |
| `app/routes_account.py` | `/v1/enroll`, `/v1/account`, `/v1/devices/*` |
| `app/routes_attest.py` | `/v1/nonce`, `/v1/attest` (load-bearing) |
| `app/routes_admin.py` | `/v1/admin/*` (operator-only, behind IAP) |
| `app/firestore.py` | Firestore client + collection accessors |
| `app/cloudflare.py` | CF API wrapper (tunnel/DNS/Access provisioning) |
| `app/schemas.py` | Pydantic models matching `api_spec.yaml` |
| `app/canonical.py` | Canonical-JSON (RFC 8785) helpers for envelope verification |
| `app/verify.py` | The 14-step attestation verification logic from `02-` §7.4 |
| `requirements.txt` | Python deps |
| `Dockerfile` | Cloud Run image |

## Running locally (against staging)

```bash
cd control_plane
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export FIRESTORE_PROJECT=clayrune
export GOOGLE_APPLICATION_CREDENTIALS=$HOME/.gcp/clayrune-sa.json
uvicorn app.main:app --reload --port 8080
curl http://localhost:8080/v1/health
```

## Deploying

```bash
gcloud builds submit --tag gcr.io/clayrune/control-plane --project=clayrune
gcloud run deploy control-plane --image gcr.io/clayrune/control-plane \
  --region=us-central1 --allow-unauthenticated --min-instances=0 \
  --project=clayrune
```

For production, set `--min-instances=1` to absorb cold-start latency on
attestation. Single-project setup for v1 (no separate staging) — use
deploy environments / Cloud Run revisions for staging vs production
slices instead.

## Cross-references

- API contract: `api_spec.yaml` + `docs/remote-access/03-control-plane-api.md`
- Wire formats: `docs/remote-access/02-attestation-protocol.md`
- Schemas: `docs/remote-access/03-control-plane-api.md` §4
