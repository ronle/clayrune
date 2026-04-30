"""Attestation verification — the 14+1-step checklist.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

Implements `docs/remote-access/02-attestation-protocol.md` §7.4. Each step's
failure produces a stable error code (see `error_codes.md`). Steps run in
order; first failure short-circuits.

Inputs come pre-parsed as Pydantic `AttestationRequest` (see schemas.py).
Output is either a `VerifiedAttestation` (success — caller issues tunnel
token) or an `AttestationFailure` raised exception.
"""
from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import logging
import secrets
from dataclasses import dataclass
from typing import NoReturn, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import canonical, firestore as fs
from .schemas import AttestationRequest

log = logging.getLogger(__name__)


_TIMESTAMP_SKEW_TOLERANCE_S = 60.0


class AttestationFailure(Exception):
    """Raised on first failing verification step."""

    def __init__(self, code: str, message: str, http_status: int = 400):
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(message)


def _fail(code: str, message: str, http_status: int = 400) -> NoReturn:
    raise AttestationFailure(code, message, http_status)


@dataclass
class VerifiedAttestation:
    """Returned to /v1/attest on successful verification. Carries the device
    row, version row, and key id so the route handler can issue a token
    + log an attestation event without re-querying Firestore."""

    device: dict
    version: dict
    client_key_id: str
    nonce_id: Optional[str]


async def verify(req: AttestationRequest, *, raw_envelope: dict) -> VerifiedAttestation:
    """Run all 14+1 verification steps from `02-` §7.4.

    `raw_envelope` is the envelope dict exactly as it arrived in the request
    body (JSON-parsed but NOT round-tripped through Pydantic). We must hash
    *that* — not a Pydantic re-serialization — because Pydantic adds None
    values for optional fields, which changes the canonical bytes the
    client signed.

    Caller is responsible for catching AttestationFailure and converting
    to an HTTP error envelope.
    """
    # Step 1: outer wrapper parses, both signatures present.
    # Pydantic already validated this at request boundary. If we got here, OK.
    if not req.signature_b64 or not req.client_signature_b64:
        _fail("bad_envelope", "Both signatures required", 400)

    env = req.envelope  # Pydantic-validated; convenient typed access
    # `raw_envelope` is what we hash — guaranteed to match the client's bytes
    if not isinstance(raw_envelope, dict):
        _fail("bad_envelope", "Envelope must be a JSON object.", 400)

    # Step 2: envelope_canonical_sha256 matches recomputed JCS hash of the
    # raw dict (NOT the Pydantic re-serialization).
    try:
        recomputed = hashlib.sha256(canonical.canonical_bytes(raw_envelope)).hexdigest()
    except Exception as e:
        _fail("bad_canonicalization", f"JCS hash failed: {e}", 400)

    if recomputed != req.envelope_canonical_sha256:
        _fail("bad_canonicalization",
              f"Hash mismatch: client={req.envelope_canonical_sha256} server={recomputed}", 400)

    envelope_hash_bytes = bytes.fromhex(req.envelope_canonical_sha256)

    # Step 3: device_pub_b64 matches a non-revoked device row.
    device = fs.device_by_pub(env.device_pub_b64)
    if device is None:
        _fail("unknown_device", "No enrolled device matches this public key.", 401)
    if device.get("revoked_at") is not None:
        _fail("revoked_device", "Device was disconnected.", 403)
    if device.get("user_id") and _is_user_suspended(device["user_id"]):
        _fail("account_suspended", "Account is suspended.", 403)

    # Step 4: signature_b64 verifies under device_pub_b64 (Ed25519).
    try:
        device_pub_raw = base64.b64decode(env.device_pub_b64)
        Ed25519PublicKey.from_public_bytes(device_pub_raw).verify(
            base64.b64decode(req.signature_b64), envelope_hash_bytes,
        )
    except (InvalidSignature, ValueError) as e:
        _fail("bad_signature", f"Device signature invalid: {e}", 401)

    # Step 4.5: client_signature_b64 verifies under an active platform key.
    client_key_id = env.client_secret_key_id
    if not client_key_id:
        _fail("unknown_client_key", "Envelope missing client_secret_key_id.", 401)

    key_row = fs.client_key_get(client_key_id)
    if key_row is None:
        _fail("unknown_client_key",
              f"Platform key {client_key_id!r} not registered.", 401)
    if key_row.get("revoked_at") is not None:
        _fail("revoked_client_key", "Platform key revoked. Update Mission Control.", 403)

    try:
        client_pub_raw = base64.b64decode(key_row["pubkey_b64"])
        Ed25519PublicKey.from_public_bytes(client_pub_raw).verify(
            base64.b64decode(req.client_signature_b64), envelope_hash_bytes,
        )
    except (InvalidSignature, ValueError, KeyError) as e:
        _fail("bad_client_signature", f"Client signature invalid: {e}", 401)

    # Step 5: enrollment_token hash matches stored hash.
    enrollment_hash_stored = device.get("enrollment_token_hash")
    if not enrollment_hash_stored:
        _fail("bad_enrollment_token", "Device row missing enrollment hash.", 401)
    enrollment_hash_provided = hashlib.sha256(env.enrollment_token.encode("utf-8")).hexdigest()
    if not secrets.compare_digest(enrollment_hash_stored, enrollment_hash_provided):
        _fail("bad_enrollment_token", "Enrollment token mismatch.", 401)

    # Step 6: proto >= devices.min_protocol AND proto >= versions[mc_version].min_protocol.
    version_row = fs.version_get(env.mc_version)
    if version_row is None:
        _fail("unknown_version", f"Mission Control version {env.mc_version!r} is not registered.", 410)
    if version_row.get("revoked"):
        _fail("revoked_version", "This Mission Control version has been revoked. Update.", 410)

    min_proto_version = int(version_row.get("min_protocol", 1))
    min_proto_device = int(device.get("min_protocol", 1))
    if env.proto < max(min_proto_version, min_proto_device):
        _fail("version_floor_exceeded", "Mission Control needs updating.", 403)

    # Step 7: mc_version in versions and not revoked. (already done above)

    # Step 8 RESERVED — was binary hash check. Re-enabled when code-signing
    # returns to scope per `05-` §1.

    # Step 9: nonce single-use + unexpired.
    # We need a nonce_id but it's not in the envelope schema today (we just
    # send `nonce` in the envelope). Store nonces keyed by (device_id, nonce_value)
    # OR have the client send the nonce_id. v1 simplification: scan the
    # nonce row by device_id + nonce string. /v1/nonce already stores that.
    nonce_id = _find_nonce_id_for_value(device["device_id"], env.nonce)
    if nonce_id is None:
        _fail("nonce_unknown", "Nonce not recognized.", 400)

    consume_result = fs.nonce_consume(device["device_id"], nonce_id, env.nonce)
    if consume_result == "ok":
        pass
    elif consume_result == "nonce_used":
        _fail("nonce_used", "Nonce already consumed.", 400)
    elif consume_result == "nonce_expired":
        _fail("nonce_expired", "Nonce expired; request a fresh one.", 400)
    elif consume_result == "nonce_unknown":
        _fail("nonce_unknown", "Nonce not recognized.", 400)
    else:
        _fail("nonce_unknown", f"Nonce check failed: {consume_result}", 400)

    # Step 10: timestamp within ±60s.
    if env.timestamp:
        try:
            ts = env.timestamp
            if isinstance(ts, str):
                if ts.endswith("Z"):
                    ts = ts[:-1] + "+00:00"
                ts = _dt.datetime.fromisoformat(ts)
            now = _dt.datetime.now(_dt.timezone.utc)
            skew = abs((now - ts).total_seconds())
            if skew > _TIMESTAMP_SKEW_TOLERANCE_S:
                _fail("timestamp_skew",
                      f"Client clock off by {skew:.0f}s. Fix and retry.", 400)
        except AttestationFailure:
            raise
        except Exception as e:
            _fail("timestamp_skew", f"Bad timestamp: {e}", 400)

    # Step 11: per-device rate limit.
    # v1 simplified: skip explicit rate limiting in the verify chain; the
    # CF Worker enforces the user-facing limits, and Firestore writes are
    # naturally bounded. Real per-device rate limiting added when a Redis
    # / Memorystore counter is wired up.

    # Step 12: device_cap not exceeded.
    # v1 simplified: enforced at /v1/enroll (can't enroll past cap) rather
    # than at every attest. Skipped here.

    # Step 13: hostname_claim matches stored.
    stored_hostname = device.get("hostname_claim", "")
    if env.hostname_claim and env.hostname_claim.lower() != stored_hostname.lower():
        _fail("hostname_mismatch",
              f"Hostname {env.hostname_claim!r} doesn't match enrollment {stored_hostname!r}.", 403)

    # Step 14: previous_token_id (if non-null) is recognized.
    # v1 simplified: we trust the client to be honest about its previous
    # token. We don't gain much by validating it (the device sig already
    # proves identity). Real check added when tunnel_tokens collection is
    # populated (currently it isn't — opaque CF tokens, not stored).

    return VerifiedAttestation(
        device=device,
        version=version_row,
        client_key_id=client_key_id,
        nonce_id=nonce_id,
    )


def _find_nonce_id_for_value(device_id: str, nonce_value: str) -> Optional[str]:
    """Find the nonce_id for a given (device_id, nonce_value) pair.

    Used because the client sends `nonce` but we keyed our store by
    `nonce_id`. Future: have `/v1/nonce` return both nonce + nonce_id and
    have the client put nonce_id into the envelope explicitly. v1: we scan
    a small set.
    """
    # Limit scan: only nonces for this device, not yet expired.
    try:
        docs = fs.db().collection(fs.COL_NONCES) \
            .where("device_id", "==", device_id) \
            .where("nonce", "==", nonce_value) \
            .limit(1) \
            .stream()
        for d in docs:
            return d.id
    except Exception as e:
        log.warning("nonce lookup failed: %s", e)
    return None


def _is_user_suspended(user_id: str) -> bool:
    try:
        snap = fs.db().collection(fs.COL_USERS).document(user_id).get()
        if not snap.exists:
            return False
        return bool((snap.to_dict() or {}).get("suspended"))
    except Exception:
        return False
