# Control Plane — Firestore Schema

**License:** PROPRIETARY AND CONFIDENTIAL.
**Authoritative source:** `docs/remote-access/03-control-plane-api.md` §4.
**This file:** quick reference + index creation commands.

## Collections

```
users/{user_id}                    — user accounts (Firebase UID = user_id)
devices/{device_id}                — enrolled devices, indexed by device_pub_b64
versions/{mc_version}              — mc_version allowlist (was `builds/`)
client_secret_keys/{key_id}        — platform client-secret pubkey allowlist
attestation_log/{auto_id}          — 30-day TTL forensics
enrollment_intents/{auto_id}       — 15-min TTL CSRF-protected flow rows
nonces/{auto_id}                   — sub-minute TTL attestation nonces
                                     (only if Memorystore is dropped per
                                     06- §13.2)
```

See `03-control-plane-api.md` §4.1–§4.3a for the full field listings.

## Indexes (gcloud commands)

```bash
PROJECT=clayrune-staging   # or clayrune-prod

# users
gcloud firestore indexes composite create \
  --project=$PROJECT --collection-group=users \
  --field-config=field-path=username,order=ascending \
  --field-config=field-path=__name__,order=ascending
# (username uniqueness enforced via transaction in /v1/enroll, not the index)

# devices
gcloud firestore indexes composite create \
  --project=$PROJECT --collection-group=devices \
  --field-config=field-path=user_id,order=ascending \
  --field-config=field-path=revoked_at,order=ascending

gcloud firestore indexes composite create \
  --project=$PROJECT --collection-group=devices \
  --field-config=field-path=hostname_claim,order=ascending

# attestation_log
gcloud firestore indexes composite create \
  --project=$PROJECT --collection-group=attestation_log \
  --field-config=field-path=device_id,order=ascending \
  --field-config=field-path=timestamp,order=descending

gcloud firestore indexes composite create \
  --project=$PROJECT --collection-group=attestation_log \
  --field-config=field-path=result,order=ascending \
  --field-config=field-path=timestamp,order=descending

# client_secret_keys (active-set query)
gcloud firestore indexes composite create \
  --project=$PROJECT --collection-group=client_secret_keys \
  --field-config=field-path=revoked_at,order=ascending \
  --field-config=field-path=released_at,order=descending
```

## TTL policies (Firestore Native)

```bash
# attestation_log — 30 days from `timestamp`
gcloud firestore fields ttls update timestamp \
  --collection-group=attestation_log --enable-ttl --project=$PROJECT

# enrollment_intents — 15 minutes from `expires_at`
gcloud firestore fields ttls update expires_at \
  --collection-group=enrollment_intents --enable-ttl --project=$PROJECT

# nonces — 30 seconds from `expires_at` (TTL granularity is minutes; will
# typically expire 1-2 minutes late which is acceptable since we also
# enforce timestamp ±60s in attestation step 10)
gcloud firestore fields ttls update expires_at \
  --collection-group=nonces --enable-ttl --project=$PROJECT
```

## Bootstrap data

After deploying the control plane to staging or prod, the operator must
seed:

1. **At least one entry in `versions`** (the version they're shipping):
   `POST /v1/admin/versions { mc_version: "1.4.2", min_protocol: 1, released_at: ... }`

2. **At least one entry in `client_secret_keys`** (the key the released
   `mc-tunnel` was built with):
   `POST /v1/admin/client_keys { key_id: "mc-tunnel-2026a", pubkey_b64: "...", mc_tunnel_version: "1.0.0", released_at: ... }`

Without (1), all attestations fail with `unknown_version`.
Without (2), all attestations fail with `unknown_client_key`.
