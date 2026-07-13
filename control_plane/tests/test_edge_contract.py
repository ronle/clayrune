"""The contract with the edge Worker — pinned from THIS side.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

The Worker (`clayrune-cloud/edge-worker/src/index.js`) and this control plane are
two halves of one auth system that live in two repos and are deployed separately.
Nothing type-checks across that seam. These tests are the seam.

Two invariants, both of which have already been violated once:

1. **RESERVED ⊆ _USERNAME_RESERVED.** The Worker bypasses authentication entirely
   for a set of "not a user dashboard" subdomains (`api`, `www`, …). If a user
   could *register* one of those names, their machine would be proxied to the
   public internet with no auth at all — by the very line that exists to keep the
   Worker from locking itself out. The spec (03-control-plane-api.md §3.15.4)
   shipped a RESERVED list containing `dash`, and asserted it was "already
   consistent with _USERNAME_RESERVED". It was not: `dash` is not reserved here.

2. **ES256 signatures are raw r||s, never DER.** WebCrypto's `crypto.subtle.verify`
   — the Worker's verifier — SILENTLY returns false for the DER SEQUENCE that
   `cryptography` produces by default. `jwt_es256.sign()` converts DER → raw. If
   that conversion is ever "simplified" away, every token in the world stops
   verifying at the edge, with no error and no log: just a zone-wide redirect loop
   to the sign-in page. Nothing in the Python suite would have noticed.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from app import jwt_es256
from app.routes_account import _USERNAME_RESERVED


# ─── 1. The reserved-subdomain invariant ─────────────────────────────────────

# Transcribed from edge-worker/src/index.js. Used when the sibling repo is not
# checked out (CI), so the invariant is still asserted rather than skipped.
_PINNED_EDGE_RESERVED = {"api", "www", "app", "admin", "dashboard", "cdn", "edge"}


def _edge_worker_source() -> Path | None:
    """Locate the Worker source in the sibling clayrune-cloud checkout, if present."""
    override = os.environ.get("CLAYRUNE_CLOUD_REPO")
    candidates = []
    if override:
        candidates.append(Path(override) / "edge-worker" / "src" / "index.js")
    # control_plane/tests/ -> control_plane/ -> mission-control/ -> _claude/
    here = Path(__file__).resolve()
    candidates.append(here.parents[3] / "clayrune-cloud" / "edge-worker" / "src" / "index.js")
    for c in candidates:
        if c.is_file():
            return c
    return None


def _parse_reserved(src: str) -> set[str]:
    """Pull the RESERVED set literal out of the Worker source."""
    m = re.search(r"const RESERVED = new Set\(\[(.*?)\]\)", src, re.S)
    assert m, "could not find `const RESERVED = new Set([...])` in the Worker source"
    return set(re.findall(r"'([a-z0-9-]+)'", m.group(1)))


def test_edge_reserved_subdomains_are_all_unregistrable():
    """A subdomain the Worker lets through unauthenticated must be one NO user can own.

    This is the `dash` bug. If it fails: either add the name to _USERNAME_RESERVED,
    or take it out of the Worker's RESERVED. Do not "fix" it by deleting this test.
    """
    for name in sorted(_PINNED_EDGE_RESERVED):
        assert name in _USERNAME_RESERVED, (
            f"the edge Worker bypasses ALL authentication for {name!r}.<zone>, but "
            f"{name!r} is not in _USERNAME_RESERVED — a user could register it and "
            f"their dev machine would be served to the public internet unauthenticated."
        )


def test_worker_source_matches_the_pinned_reserved_list():
    """Keep the transcription above honest against the real Worker source."""
    src_path = _edge_worker_source()
    if src_path is None:
        pytest.skip("clayrune-cloud is not checked out beside this repo")
    actual = _parse_reserved(src_path.read_text(encoding="utf-8"))
    assert actual == _PINNED_EDGE_RESERVED, (
        f"the Worker's RESERVED list ({sorted(actual)}) has drifted from the copy pinned "
        f"here ({sorted(_PINNED_EDGE_RESERVED)}). Update _PINNED_EDGE_RESERVED — and make "
        f"sure every new name is in _USERNAME_RESERVED, or it is a public-exposure bug."
    )
    # And the real thing, against the real source: subset, no transcription in between.
    unregistrable = actual - set(_USERNAME_RESERVED)
    assert not unregistrable, (
        f"the Worker bypasses auth for {sorted(unregistrable)}, which users can REGISTER."
    )


def test_dash_specifically_is_not_bypassed():
    """The exact bug the spec shipped. `dash` is registrable, so it must not bypass."""
    assert "dash" not in _USERNAME_RESERVED, "if you reserve `dash`, update this test"
    assert "dash" not in _PINNED_EDGE_RESERVED, (
        "`dash` is bypassed at the edge but registrable as a username — "
        "whoever registers it gets their machine published unauthenticated."
    )


# ─── 2. The signature-encoding invariant (the DER trap) ──────────────────────


def _sig_bytes(token: str) -> bytes:
    seg = token.split(".")[2]
    return jwt_es256._b64u_decode(seg)


def test_signature_is_raw_r_s_not_der(monkeypatch):
    """WebCrypto silently rejects DER. The signature must be exactly 64 raw bytes.

    A DER ECDSA signature is an ASN.1 SEQUENCE: it starts with 0x30 and is ~70-72
    bytes. If this test ever sees one, the edge is rejecting 100% of tokens.
    """
    monkeypatch.setenv("CLAYRUNE_ALLOW_EPHEMERAL_KEY", "1")
    jwt_es256.reset_keys_for_tests()
    try:
        token = jwt_es256.sign({"sub": "u1", "u": "bob"}, ttl_seconds=900)
        sig = _sig_bytes(token)

        assert len(sig) == 64, (
            f"the edge Worker's crypto.subtle.verify() requires a 64-byte raw r||s "
            f"signature and returns FALSE — silently — for anything else. Got "
            f"{len(sig)} bytes. If this is ~70 bytes starting with 0x30, someone "
            f"removed the DER→raw conversion in jwt_es256.sign() and every session "
            f"in production is now failing at the edge."
        )
        assert sig[0] != 0x30 or len(sig) == 64, "looks like a DER SEQUENCE"
    finally:
        jwt_es256.reset_keys_for_tests()


def test_header_pins_es256_and_carries_a_kid(monkeypatch):
    """The Worker pins `header.alg === 'ES256'` and looks the key up by `kid`."""
    import base64
    import json

    monkeypatch.setenv("CLAYRUNE_ALLOW_EPHEMERAL_KEY", "1")
    jwt_es256.reset_keys_for_tests()
    try:
        token = jwt_es256.sign({"sub": "u1", "u": "bob"}, ttl_seconds=900)
        h = json.loads(jwt_es256._b64u_decode(token.split(".")[0]))
        assert h["alg"] == "ES256"
        assert h.get("kid"), "no kid → the Worker cannot select a key → every token 302s"

        published = {k["kid"] for k in jwt_es256.jwks()["keys"]}
        assert h["kid"] in published, "we are signing with a kid we do not publish in JWKS"
    finally:
        jwt_es256.reset_keys_for_tests()


def test_jwks_is_publishable_public_material_only(monkeypatch):
    """Whatever else changes, the JWKS must never grow a private component."""
    monkeypatch.setenv("CLAYRUNE_ALLOW_EPHEMERAL_KEY", "1")
    jwt_es256.reset_keys_for_tests()
    try:
        doc = jwt_es256.jwks()
        assert doc["keys"], "an empty JWKS 503s the entire zone"
        for k in doc["keys"]:
            assert k["kty"] == "EC" and k["crv"] == "P-256"
            assert k["alg"] == "ES256"
            assert set(k) == {"kty", "crv", "kid", "alg", "use", "x", "y"}, (
                f"unexpected JWKS field(s): {set(k) - {'kty', 'crv', 'kid', 'alg', 'use', 'x', 'y'}} "
                f"— 'd' would be the PRIVATE key."
            )
            assert "d" not in k
    finally:
        jwt_es256.reset_keys_for_tests()
