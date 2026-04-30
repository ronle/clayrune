"""
attestation — Attestation envelope construction, dual-signing, and submission.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

Implements the client side of `02-attestation-protocol.md` §7. Each call to
attest_once() does:

    1. GET /v1/nonce  → nonce + nonce_id
    2. Build inner envelope (proto, device fields, mc_version, hostname, ...)
    3. Canonical-JSON (RFC 8785) → sha256 hex
    4. Sign envelope hash with device key (proves "this enrolled user/device")
    5. Sign envelope hash with client secret (proves "this is platform mc-tunnel")
    6. POST /v1/attest { envelope, signature_b64, client_signature_b64 }
    7. Return the tunnel-token issuance response (or error envelope)

The dev client secret embedded here is a PLACEHOLDER for v1 development.
When we ship the real Rust mc-tunnel, the actual CLIENT_SECRET_PRIV gets
baked into that binary via build.rs (see `05-build-pipeline.md` §3.1) and
this Python module either delegates to mc-tunnel via stdin/stdout or gets
removed entirely. For local-mock dev iteration, a hard-coded keypair here
is fine — the control plane validates against whatever pubkeys it has
registered, including dev ones.
"""
from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import logging
import platform
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests
import rfc8785
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mc_remote_iface import ProviderCaps

from . import config, device_keys

log = logging.getLogger(__name__)


# ─── Dev client secret (placeholder for v1) ─────────────────────────────────
# REPLACED by per-release embedded keys when the real Rust mc-tunnel ships.
# Until then, this keypair is registered with the local mock control plane
# at boot so attestations can be verified end-to-end without infra.
_DEV_CLIENT_SECRET_KEY_ID = "mc-tunnel-dev-2026"
_DEV_CLIENT_SECRET_PRIV_B64 = "LZTfhO9q8AUAA4vYBg2kvnbKzbdCePfg6bDC3RTFvFA="

_dev_client_priv: Optional[Ed25519PrivateKey] = None


def _client_priv() -> Ed25519PrivateKey:
    global _dev_client_priv
    if _dev_client_priv is None:
        _dev_client_priv = Ed25519PrivateKey.from_private_bytes(
            base64.b64decode(_DEV_CLIENT_SECRET_PRIV_B64)
        )
    return _dev_client_priv


def dev_client_pubkey_b64() -> str:
    """Public key for the dev client secret. Used by the mock CP to verify."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    pub = _client_priv().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(pub).decode("ascii")


def dev_client_secret_key_id() -> str:
    return _DEV_CLIENT_SECRET_KEY_ID


# ─── Attestation result types ────────────────────────────────────────────────


@dataclass
class AttestationOk:
    tunnel_token: str
    tunnel_token_id: str
    tunnel_token_expires_at: _dt.datetime
    next_attestation_after: _dt.datetime
    caps: ProviderCaps
    directives: list[dict]
    received_at: _dt.datetime


@dataclass
class AttestationFailure:
    code: str
    message: str
    http_status: int
    received_at: _dt.datetime


AttestationResult = AttestationOk | AttestationFailure


# ─── Last-seen result (for status display) ───────────────────────────────────

_last_lock = threading.Lock()
_last_result: Optional[AttestationResult] = None


def get_last_result() -> Optional[AttestationResult]:
    with _last_lock:
        return _last_result


def _set_last_result(r: AttestationResult) -> None:
    global _last_result
    with _last_lock:
        _last_result = r


# ─── Public API ──────────────────────────────────────────────────────────────


def attest_once(
    *,
    session: requests.Session,
    cp_base_url: Optional[str] = None,
    previous_token_id: Optional[str] = None,
    timeout: float = 10.0,
) -> AttestationResult:
    """Run one full attestation: GET /nonce + POST /attest.

    Side effects: updates module-level last-result so .get_last_result()
    surfaces it to status display.
    """
    cp_base = cp_base_url or config.CONTROL_PLANE_BASE_URL
    identity = device_keys.load_identity()
    if identity is None:
        return _fail("not_enrolled", "No device identity in keystore.", 0)

    # 1. Get a fresh nonce
    try:
        r = session.get(
            f"{cp_base}/nonce",
            params={"device_id": identity.device_id},
            headers={"Authorization": f'MC-Device device_id="{identity.device_id}"'},
            timeout=timeout,
        )
    except requests.RequestException as e:
        return _fail("internal_error", f"Nonce request failed: {e}", 0)
    if r.status_code != 200:
        return _fail_from_http(r)
    nonce_data = r.json()

    # 2. Build envelope
    envelope = _build_envelope(
        identity=identity,
        nonce=nonce_data["nonce"],
        previous_token_id=previous_token_id,
    )

    # 3. Canonical JSON + hash
    canonical = rfc8785.dumps(envelope)
    envelope_hash_hex = hashlib.sha256(canonical).hexdigest()
    envelope_hash_bytes = bytes.fromhex(envelope_hash_hex)

    # 4. Device signature
    try:
        device_sig = device_keys.sign(envelope_hash_bytes)
    except Exception as e:
        return _fail("internal_error", f"Device key sign failed: {e}", 0)

    # 5. Client-secret signature
    client_sig = _client_priv().sign(envelope_hash_bytes)

    # 6. POST /attest
    body = {
        "envelope": envelope,
        "envelope_canonical_sha256": envelope_hash_hex,
        "signature_b64": base64.b64encode(device_sig).decode("ascii"),
        "client_signature_b64": base64.b64encode(client_sig).decode("ascii"),
    }
    try:
        r = session.post(
            f"{cp_base}/attest",
            json=body,
            headers={"Authorization": f'MC-Device device_id="{identity.device_id}"'},
            timeout=timeout,
        )
    except requests.RequestException as e:
        return _fail("internal_error", f"Attest request failed: {e}", 0)
    if r.status_code != 200:
        return _fail_from_http(r)

    # 7. Parse success response
    rsp = r.json()
    try:
        result = AttestationOk(
            tunnel_token=rsp["tunnel_token"],
            tunnel_token_id=rsp["tunnel_token_id"],
            tunnel_token_expires_at=_iso(rsp["tunnel_token_expires_at"]),
            next_attestation_after=_iso(rsp["next_attestation_after"]),
            caps=ProviderCaps(
                bandwidth_quota_period_bytes=rsp["caps"]["bandwidth_bytes_remaining_period"]
                                             + rsp["caps"].get("bandwidth_used_period_bytes", 0),
                bandwidth_used_period_bytes=rsp["caps"].get("bandwidth_used_period_bytes", 0),
                rate_limit_rps=rsp["caps"]["rate_limit_rps"],
                max_response_bytes=rsp["caps"]["max_response_bytes"],
                max_concurrent_connections=rsp["caps"]["max_concurrent_connections"],
            ),
            directives=rsp.get("directives", []),
            received_at=_dt.datetime.now(_dt.timezone.utc),
        )
    except (KeyError, ValueError) as e:
        return _fail("internal_error", f"Malformed attestation response: {e}", 0)

    _set_last_result(result)
    return result


# ─── Internals ───────────────────────────────────────────────────────────────


def _build_envelope(
    *,
    identity: device_keys.DeviceIdentity,
    nonce: str,
    previous_token_id: Optional[str],
) -> dict:
    """Build the inner envelope dict (without signatures) per protocol §7.1."""
    return {
        "proto": 1,
        "envelope_type": "attestation_request",
        "device_pub_b64": identity.device_pub_b64,
        "device_id": identity.device_id,
        "enrollment_token": identity.enrollment_token,
        "mc_version": _mc_version(),
        "mc_tunnel_version": _mc_tunnel_version(),
        "client_secret_key_id": _DEV_CLIENT_SECRET_KEY_ID,
        "os": _os_string(),
        "hostname_claim": identity.hostname,
        "timestamp": _utcnow_iso(),
        "nonce": nonce,
        "challenge_response": "",  # Reserved (challenge originates from local handshake when Rust mc-tunnel exists)
        "previous_token_id": previous_token_id,
    }


def _mc_version() -> str:
    # MC's version is published in pyproject.toml / about-page metadata.
    # For v1 dev, hardcode; wire to real source later.
    return "1.4.2"


def _mc_tunnel_version() -> str:
    return "0.1.0-dev-py"


def _os_string() -> str:
    sys_p = platform.system().lower()
    rel = platform.release()
    if sys_p == "windows":
        return f"win32-{rel}"
    if sys_p == "darwin":
        return f"darwin-{rel}"
    return f"{sys_p}-{rel}"


def _utcnow_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _iso(s: str) -> _dt.datetime:
    # Tolerate trailing Z
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return _dt.datetime.fromisoformat(s)


def _fail(code: str, message: str, http_status: int) -> AttestationFailure:
    r = AttestationFailure(code=code, message=message, http_status=http_status,
                           received_at=_dt.datetime.now(_dt.timezone.utc))
    _set_last_result(r)
    return r


def _fail_from_http(r: requests.Response) -> AttestationFailure:
    try:
        body = r.json()
        code = body.get("code", "internal_error")
        message = body.get("message", body.get("error", "Unknown error"))
    except Exception:
        code = "internal_error"
        message = f"HTTP {r.status_code}"
    return _fail(code, message, r.status_code)
