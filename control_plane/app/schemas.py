"""Pydantic schemas matching api_spec.yaml.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

Single source of truth is api_spec.yaml; these models mirror it. Drift
between the two is a bug. Future v2 idea: codegen from the YAML so they
can't drift.

This is a SKELETON listing the most important shapes.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ─── Error envelope ──────────────────────────────────────────────────────────
class ErrorEnvelope(BaseModel):
    code: str
    message: str
    request_id: str
    retry_after_ms: Optional[int] = None
    details: Optional[dict] = None


# ─── Attestation ─────────────────────────────────────────────────────────────
class AttestationEnvelope(BaseModel):
    proto: Literal[1]
    envelope_type: Literal["attestation_request"]
    device_pub_b64: str
    device_id: str
    enrollment_token: str
    mc_version: str
    mc_tunnel_version: str
    client_secret_key_id: str
    os: str
    hostname_claim: str
    timestamp: datetime
    nonce: str
    challenge_response: str
    previous_token_id: Optional[str] = None
    # Reserved (re-enabled when code-signing returns to scope per 05- §1):
    build_manifest_id: Optional[str] = None
    mc_binary_sha256: Optional[str] = Field(None, pattern=r"^[a-f0-9]{64}$")


class AttestationRequest(BaseModel):
    envelope: AttestationEnvelope
    envelope_canonical_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    signature_b64: str
    client_signature_b64: str


# ─── More schemas to come (Account, Device, EnrollRequest/Response, etc.) ───
