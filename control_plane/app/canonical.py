"""Canonical-JSON (RFC 8785 / JCS) helpers.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

Used to recompute envelope_canonical_sha256 and verify the client-side
hash matches. See `02-attestation-protocol.md` §2 + §7.

This is a SKELETON wrapping rfc8785.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_bytes(obj: Any) -> bytes:
    """Return RFC 8785 canonical-JSON serialization."""
    try:
        import rfc8785
    except ImportError:
        # Fallback for early dev: NOT canonical. Will produce wrong hashes
        # in production. Replace once rfc8785 is in requirements.
        return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return rfc8785.dumps(obj)


def canonical_sha256_hex(obj: Any) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()
