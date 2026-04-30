"""Firestore client + collection accessors.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

Lazy singleton client. Reads `FIRESTORE_PROJECT` from env (e.g.
`clayrune-staging` or `clayrune-prod`). When `FIRESTORE_EMULATOR_HOST` is
set, the google-cloud-firestore library auto-routes to the local emulator
— no code change needed for local dev.

Collections per `docs/remote-access/03-control-plane-api.md` §4. Collection
name constants live here so renames are grep-able.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Optional

log = logging.getLogger(__name__)


# Collection name constants — referenced from route handlers, not hardcoded.
COL_USERS = "users"
COL_DEVICES = "devices"
COL_VERSIONS = "versions"
COL_CLIENT_KEYS = "client_secret_keys"
COL_ATTEST_LOG = "attestation_log"
COL_ENROLL_INTENTS = "enrollment_intents"
COL_NONCES = "nonces"


@lru_cache(maxsize=1)
def db():
    """Return the Firestore client. Singleton; safe to call from any thread.

    Reads:
      FIRESTORE_PROJECT   — GCP project id (default: clayrune)
      FIRESTORE_DATABASE  — database id (default: '(default)' — the special
                            auto-default DB. If your db has the literal id
                            'default' instead, set FIRESTORE_DATABASE=default.)
      FIRESTORE_EMULATOR_HOST — emulator override (auto-honored by SDK)
    """
    from google.cloud import firestore  # type: ignore

    project = os.environ.get("FIRESTORE_PROJECT", "clayrune")
    database = os.environ.get("FIRESTORE_DATABASE", "(default)")
    emulator = os.environ.get("FIRESTORE_EMULATOR_HOST")
    if emulator:
        log.info("firestore: using emulator at %s (project=%s database=%s)",
                 emulator, project, database)
    else:
        log.info("firestore: using real GCP client (project=%s database=%s)",
                 project, database)
    return firestore.Client(project=project, database=database)


# ─── Convenience accessors ───────────────────────────────────────────────────


def device_by_pub(device_pub_b64: str) -> Optional[dict]:
    """Look up a device row by Ed25519 pubkey. Returns None if not found."""
    docs = db().collection(COL_DEVICES) \
        .where("device_pub_b64", "==", device_pub_b64) \
        .limit(1) \
        .stream()
    for d in docs:
        data = d.to_dict() or {}
        data["_id"] = d.id
        return data
    return None


def device_by_id(device_id: str) -> Optional[dict]:
    """Look up a device row by its document id."""
    snap = db().collection(COL_DEVICES).document(device_id).get()
    if not snap.exists:
        return None
    data = snap.to_dict() or {}
    data["_id"] = snap.id
    return data


def version_get(mc_version: str) -> Optional[dict]:
    snap = db().collection(COL_VERSIONS).document(mc_version).get()
    if not snap.exists:
        return None
    return snap.to_dict() or {}


def client_keys_active(limit: int = 10) -> list[dict]:
    """Return the active set of client_secret_keys: most recent N non-revoked entries.

    Per `02-` §3.6: active set = up to 3 most-recent non-revoked rows.
    `limit=10` here is a safety margin against transient mis-orderings.
    """
    docs = db().collection(COL_CLIENT_KEYS) \
        .where("revoked_at", "==", None) \
        .order_by("released_at", direction="DESCENDING") \
        .limit(limit) \
        .stream()
    out = []
    for d in docs:
        row = d.to_dict() or {}
        row["_id"] = d.id
        out.append(row)
    return out


def client_key_get(key_id: str) -> Optional[dict]:
    snap = db().collection(COL_CLIENT_KEYS).document(key_id).get()
    if not snap.exists:
        return None
    data = snap.to_dict() or {}
    data["_id"] = snap.id
    return data


def nonce_consume(device_id: str, nonce_id: str, nonce: str) -> str:
    """Atomically check + burn a nonce.

    Returns one of: 'ok', 'nonce_unknown', 'nonce_used', 'nonce_expired',
    'nonce_mismatch'. The atomic check uses a Firestore transaction.

    On success, the nonce row is deleted (single-use semantics).
    """
    import datetime as _dt
    from google.cloud import firestore  # type: ignore

    client = db()
    ref = client.collection(COL_NONCES).document(nonce_id)

    @firestore.transactional
    def _txn(txn):
        snap = ref.get(transaction=txn)
        if not snap.exists:
            return "nonce_unknown"
        row = snap.to_dict() or {}
        if row.get("device_id") != device_id:
            return "nonce_mismatch"
        if row.get("used"):
            return "nonce_used"
        expires_at = row.get("expires_at")
        # Firestore Timestamps come back as datetime; tolerate both
        if expires_at is not None:
            now = _dt.datetime.now(_dt.timezone.utc)
            if hasattr(expires_at, "timestamp"):
                if expires_at < now:
                    return "nonce_expired"
        if row.get("nonce") != nonce:
            return "nonce_mismatch"
        # Burn: delete the row
        txn.delete(ref)
        return "ok"

    return _txn(client.transaction())


def nonce_create(device_id: str, nonce_id: str, nonce: str, ttl_seconds: int = 30) -> None:
    """Persist a freshly-issued nonce."""
    import datetime as _dt

    expires_at = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=ttl_seconds)
    db().collection(COL_NONCES).document(nonce_id).set({
        "device_id": device_id,
        "nonce": nonce,
        "expires_at": expires_at,
        "used": False,
    })


def attestation_log_append(*, device_id: str, user_id: str, result: str,
                           mc_version: str, client_secret_key_id: str,
                           os_str: str, ip_hash: Optional[str],
                           nonce_id: Optional[str], latency_ms: Optional[int]) -> None:
    """Append to attestation_log. TTL = 30 days, managed via Firestore TTL policy."""
    import datetime as _dt
    db().collection(COL_ATTEST_LOG).add({
        "device_id": device_id,
        "user_id": user_id,
        "timestamp": _dt.datetime.now(_dt.timezone.utc),
        "result": result,
        "mc_version": mc_version,
        "client_secret_key_id": client_secret_key_id,
        "os": os_str,
        "ip_hash": ip_hash,
        "nonce_id": nonce_id,
        "latency_ms": latency_ms,
    })
