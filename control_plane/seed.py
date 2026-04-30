"""Seed the local Firestore emulator (or a real CLAYRUNE-dev project).

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

For local dev: pre-populates the collections required for /v1/attest to
verify a real envelope from MC. Without this seed, every attestation fails
at step 7 (`unknown_version`) or step 4.5 (`unknown_client_key`) because
nothing is registered.

Usage (against the emulator):

    FIRESTORE_EMULATOR_HOST=127.0.0.1:8081 \
    FIRESTORE_PROJECT=clayrune-dev \
    python -m control_plane.seed

What it seeds:
  - One mc_version (1.4.2) — matches the version the Python attestation
    module currently reports.
  - One client_secret_key (mc-tunnel-dev-2026) — matches the dev key
    embedded in mc_remote.attestation.
  - Empty users/devices collections — populated by /v1/enroll at runtime.

Idempotent: safe to re-run; uses set() with merge=True.
"""
from __future__ import annotations

import datetime as _dt
import os
import sys

# When running this script directly, ensure the package is importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Local imports — these read FIRESTORE_PROJECT / FIRESTORE_EMULATOR_HOST
from control_plane.app import firestore as fs


# ─── Match what the v1 client sends ──────────────────────────────────────────


def _mc_remote_dev_pubkey_b64() -> str:
    """Reach into mc_remote.attestation to grab the dev client pubkey.

    This couples the control plane's seed script to the proprietary client
    in this repo — fine for v1 dev. In production the operator runs
    `POST /v1/admin/client_keys` once per release with the real pubkey.
    """
    try:
        from mc_remote import attestation as mc_att
        return mc_att.dev_client_pubkey_b64()
    except Exception as e:
        raise RuntimeError(
            f"Could not import mc_remote.attestation to extract dev client pubkey: {e}"
        )


def _mc_remote_dev_key_id() -> str:
    from mc_remote import attestation as mc_att
    return mc_att.dev_client_secret_key_id()


def _mc_version_in_use() -> str:
    """Mirrors what mc_remote.attestation._mc_version() returns."""
    from mc_remote import attestation as mc_att
    return mc_att._mc_version()


def main() -> None:
    print(f"Seeding Firestore project={os.environ.get('FIRESTORE_PROJECT', '?')} "
          f"emulator={os.environ.get('FIRESTORE_EMULATOR_HOST', '<real GCP>')}")

    # 1. Versions
    mc_version = _mc_version_in_use()
    print(f"  versions/{mc_version} ...")
    fs.db().collection(fs.COL_VERSIONS).document(mc_version).set({
        "mc_version": mc_version,
        "min_protocol": 1,
        "released_at": _dt.datetime.now(_dt.timezone.utc),
        "revoked": False,
    }, merge=True)

    # 2. Client secret keys
    key_id = _mc_remote_dev_key_id()
    pubkey = _mc_remote_dev_pubkey_b64()
    print(f"  client_secret_keys/{key_id} ...")
    fs.db().collection(fs.COL_CLIENT_KEYS).document(key_id).set({
        "key_id": key_id,
        "pubkey_b64": pubkey,
        "released_at": _dt.datetime.now(_dt.timezone.utc),
        "revoked_at": None,
        "mc_tunnel_version": "0.1.0-dev-py",
    }, merge=True)

    print("Seed complete.")


if __name__ == "__main__":
    main()
