"""ES256 (ECDSA P-256) JWT minting + JWKS publication.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

This is the key half of the contract with the edge Worker
(`clayrune-cloud/edge-worker/src/index.js`). The Worker holds **only the public
key**, fetched from `GET /v1/jwks` and cached for 10 minutes, and verifies the
session cookie in CPU at the edge — no origin call on the hot path.

ES256 ONLY. The Worker pins `header.alg === 'ES256'` and refuses anything else;
we must never mint anything else. There is no `none`, no HS256 fallback, and no
"algorithm negotiation" — the algorithm is a constant on both sides.

## Key material

Production: `CLAYRUNE_JWT_SIGNING_KEYS` — a JSON array, newest first:

    [{"kid": "cp-2026a", "pem": "-----BEGIN PRIVATE KEY-----\\n...", "active": true},
     {"kid": "cp-2025b", "pem": "...", "active": false}]

`active: true` keys are candidates for *signing* (the first one wins). ALL keys
are published in the JWKS, active or not — that is what makes rotation safe:
publish the new key, wait one JWKS cache TTL (10 min), start signing with it,
keep the old key published until every JWT it signed has expired (one session
TTL), then drop it.

Single-key convenience: `CLAYRUNE_JWT_SIGNING_KEY_PEM` + `CLAYRUNE_JWT_KID`.

Dev/test: with neither set, an ephemeral key is generated in-process. Sessions
do not survive a restart — which is the honest behaviour for a key that lives
only in RAM. Production is guarded: `_load_keys()` refuses to generate one when
`K_SERVICE` (Cloud Run) is present.

Generate a key:

    openssl ecparam -genkey -name prime256v1 -noout \\
      | openssl pkcs8 -topk8 -nocrypt -out cp-2026a.pem
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)
from cryptography.exceptions import InvalidSignature

log = logging.getLogger(__name__)


ALG = "ES256"
_CURVE_BYTES = 32  # P-256 → r and s are 32 bytes each


class JWTError(ValueError):
    """Malformed, unverifiable, or expired token."""


# ─── base64url ───────────────────────────────────────────────────────────────


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


# ─── Keyring ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SigningKey:
    kid: str
    private_key: ec.EllipticCurvePrivateKey
    active: bool


_keys: Optional[list[SigningKey]] = None


def _allow_ephemeral_key() -> bool:
    """Opt-in to an in-memory signing key. Local dev / tests ONLY — never a deploy.

    Read at call time, not import time, so tests and the dev runner can flip it.
    """
    return os.environ.get("CLAYRUNE_ALLOW_EPHEMERAL_KEY", "0") == "1"


def _parse_pem(pem: str) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise RuntimeError("JWT signing key is not an EC private key")
    if not isinstance(key.curve, ec.SECP256R1):
        raise RuntimeError(
            f"JWT signing key must be on P-256 (secp256r1); got {key.curve.name}. "
            "The Worker imports the JWK with namedCurve 'P-256' and will reject anything else."
        )
    return key


def _load_keys() -> list[SigningKey]:
    raw = os.environ.get("CLAYRUNE_JWT_SIGNING_KEYS", "").strip()
    if raw:
        try:
            entries = json.loads(raw)
        except ValueError as e:
            raise RuntimeError(f"CLAYRUNE_JWT_SIGNING_KEYS is not valid JSON: {e}") from e
        if not isinstance(entries, list) or not entries:
            raise RuntimeError("CLAYRUNE_JWT_SIGNING_KEYS must be a non-empty JSON array")
        out = []
        for e in entries:
            kid = (e.get("kid") or "").strip()
            pem = e.get("pem") or ""
            if not kid or not pem:
                raise RuntimeError("each CLAYRUNE_JWT_SIGNING_KEYS entry needs 'kid' and 'pem'")
            out.append(SigningKey(kid=kid, private_key=_parse_pem(pem),
                                  active=bool(e.get("active", True))))
        if not any(k.active for k in out):
            raise RuntimeError("CLAYRUNE_JWT_SIGNING_KEYS has no key with active:true — "
                               "nothing could be signed")
        return out

    pem = os.environ.get("CLAYRUNE_JWT_SIGNING_KEY_PEM", "").strip()
    if pem:
        kid = os.environ.get("CLAYRUNE_JWT_KID", "cp-1").strip() or "cp-1"
        return [SigningKey(kid=kid, private_key=_parse_pem(pem), active=True)]

    # FAIL CLOSED. This guard used to fire only when K_SERVICE was set — i.e. on
    # Cloud Run and nowhere else. Anywhere else that runs more than one process
    # (gunicorn --workers 2, a plain VM, Fly — where the hosted product is
    # heading) EVERY WORKER would generate its own private key and publish them
    # all under the same kid. /v1/jwks returns whichever worker happened to
    # answer, the edge Worker caches that for 10 minutes, and every JWT signed by
    # a different worker fails verification.
    #
    # The symptom is ~50% of requests 403-ing at the edge, at random, looking for
    # all the world like a network flake. That is a day of someone's life.
    #
    # So: an ephemeral key is now opt-IN, not opt-out. Local dev sets the flag
    # (conftest and the dev runner do); no deployment ever should.
    if not _allow_ephemeral_key():
        raise RuntimeError(
            "No JWT signing key configured (CLAYRUNE_JWT_SIGNING_KEYS / "
            "CLAYRUNE_JWT_SIGNING_KEY_PEM). Refusing to generate an ephemeral key: "
            "with more than one process each would sign with a DIFFERENT key under "
            "the SAME kid, and the edge would reject a random ~half of all tokens. "
            "Set a real key, or set CLAYRUNE_ALLOW_EPHEMERAL_KEY=1 for local dev "
            "(single process only — sessions will not survive a restart)."
        )

    log.warning("jwt: no signing key configured — generating an EPHEMERAL dev key "
                "(CLAYRUNE_ALLOW_EPHEMERAL_KEY=1). Sessions will not survive a "
                "restart, and this is UNSAFE with more than one worker process.")
    return [SigningKey(kid="dev-ephemeral",
                       private_key=ec.generate_private_key(ec.SECP256R1()),
                       active=True)]


def keys() -> list[SigningKey]:
    global _keys
    if _keys is None:
        _keys = _load_keys()
    return _keys


def reset_keys_for_tests() -> None:
    global _keys
    _keys = None


def signing_key() -> SigningKey:
    """The key new tokens are signed with: the first `active` entry."""
    for k in keys():
        if k.active:
            return k
    raise RuntimeError("no active JWT signing key")


# ─── JWKS ────────────────────────────────────────────────────────────────────


def _public_jwk(k: SigningKey) -> dict:
    nums = k.private_key.public_key().public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "kid": k.kid,
        "alg": ALG,
        "use": "sig",
        "x": _b64u_encode(nums.x.to_bytes(_CURVE_BYTES, "big")),
        "y": _b64u_encode(nums.y.to_bytes(_CURVE_BYTES, "big")),
    }


def jwks() -> dict:
    """The public JWKS document. Every key, active or not — see module docstring."""
    return {"keys": [_public_jwk(k) for k in keys()]}


# ─── Sign ────────────────────────────────────────────────────────────────────


def sign(claims: dict[str, Any], *, ttl_seconds: int) -> str:
    """Mint a compact ES256 JWT. `iat`/`exp` are set here; callers own the rest."""
    k = signing_key()
    now = int(time.time())
    payload = dict(claims)
    payload.setdefault("iat", now)
    payload["exp"] = now + int(ttl_seconds)

    header = {"alg": ALG, "typ": "JWT", "kid": k.kid}
    signing_input = (
        _b64u_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode())
        + "."
        + _b64u_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    )

    der = k.private_key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    # JOSE wants the raw fixed-width r||s concatenation, NOT the DER sequence
    # `cryptography` hands back. WebCrypto's ECDSA verify (what the Worker uses)
    # will silently reject a DER blob.
    r, s = decode_dss_signature(der)
    raw_sig = r.to_bytes(_CURVE_BYTES, "big") + s.to_bytes(_CURVE_BYTES, "big")

    return f"{signing_input}.{_b64u_encode(raw_sig)}"


# ─── Verify ──────────────────────────────────────────────────────────────────
#
# The Worker is the real verifier; this exists so the control plane can read its
# own cookie (and so tests can assert the exact checks the Worker performs).
# Kept deliberately in lockstep with `edge-worker/src/index.js`.


def verify(token: str, *, issuer: Optional[str] = None, audience: Optional[str] = None,
           leeway_s: int = 0) -> dict:
    """Verify signature + exp + iss + aud. Returns claims. Raises JWTError."""
    parts = token.split(".")
    if len(parts) != 3:
        raise JWTError("token is not three dot-separated segments")
    h64, p64, s64 = parts

    try:
        header = json.loads(_b64u_decode(h64))
        claims = json.loads(_b64u_decode(p64))
        raw_sig = _b64u_decode(s64)
    except Exception as e:
        raise JWTError(f"token segments are not valid base64url JSON: {e}") from e

    # Pin the algorithm. Never dispatch on header.alg — that is the classic
    # JWT confusion bug (alg:none, or HS256 verified against the public key).
    if header.get("alg") != ALG:
        raise JWTError(f"unsupported alg {header.get('alg')!r}; only {ALG} is accepted")

    kid = header.get("kid")
    key = next((k for k in keys() if k.kid == kid), None)
    if key is None:
        raise JWTError(f"unknown kid {kid!r}")

    if len(raw_sig) != 2 * _CURVE_BYTES:
        raise JWTError("signature is not a 64-byte raw ECDSA r||s")
    r = int.from_bytes(raw_sig[:_CURVE_BYTES], "big")
    s = int.from_bytes(raw_sig[_CURVE_BYTES:], "big")
    try:
        key.private_key.public_key().verify(
            encode_dss_signature(r, s),
            f"{h64}.{p64}".encode("ascii"),
            ec.ECDSA(hashes.SHA256()),
        )
    except InvalidSignature as e:
        raise JWTError("signature does not verify") from e

    now = int(time.time())
    exp = claims.get("exp")
    if not isinstance(exp, int) or exp <= now - leeway_s:
        raise JWTError("token is expired or has no exp")
    if issuer is not None and claims.get("iss") != issuer:
        raise JWTError("issuer mismatch")
    if audience is not None and claims.get("aud") != audience:
        raise JWTError("audience mismatch")

    return claims
