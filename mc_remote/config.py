"""
mc_remote.config

Single source of truth for platform-domain configuration in the proprietary
remote-access module.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.
Distributed only under the Mission Control Cloud Terms of Service.

This module is intentionally kept tiny. Anything broader belongs in a sibling
module (`enrollment`, `tunnel_supervisor`, `attestation`, etc.).

Environment-variable overrides exist so staging deployments can point at
`api-staging.clayrune.io` without rebuilding. They are NOT a user-facing
configuration surface — typical end users never set these.
"""
from __future__ import annotations

import os

# ─── Platform domain (single source of truth) ────────────────────────────────
# All four TLDs are owned (per memory: project_remote_access_domains.md).
# `.io` is the primary user-facing host; `.com` / `.dev` / `.ai` redirect to it.
# Override via env var for staging/dev.
PLATFORM_DOMAIN: str = os.environ.get("MC_REMOTE_PLATFORM_DOMAIN", "clayrune.io")

# ─── Derived URLs ────────────────────────────────────────────────────────────
# Built from PLATFORM_DOMAIN so changing one env var moves everything.
CONTROL_PLANE_HOST: str = os.environ.get(
    "MC_REMOTE_CONTROL_PLANE_HOST",
    f"api.{PLATFORM_DOMAIN}",
)
CONTROL_PLANE_BASE_URL: str = f"https://{CONTROL_PLANE_HOST}/v1"


def control_plane_base_url() -> str:
    """Resolve the control plane URL at call time (env vars may change post-import).

    Resolution order:
      1. MC_REMOTE_CP_OVERRIDE  — full URL incl. /v1 (e.g. http://localhost:8080/v1).
                                  Used when running a real CP locally for dev.
      2. MC_REMOTE_LOCAL_MOCK=1 — point at MC's own Flask server's mock endpoints.
      3. MC_REMOTE_CONTROL_PLANE_HOST host override + https://.../v1.
      4. Production default: https://api.<PLATFORM_DOMAIN>/v1.
    """
    override = os.environ.get("MC_REMOTE_CP_OVERRIDE")
    if override:
        return override.rstrip("/")
    if os.environ.get("MC_REMOTE_LOCAL_MOCK") == "1":
        return f"{MC_LOCAL_BASE_URL}/v1"
    host = os.environ.get("MC_REMOTE_CONTROL_PLANE_HOST", f"api.{PLATFORM_DOMAIN}")
    return f"https://{host}/v1"

MARKETING_HOST: str = PLATFORM_DOMAIN  # `https://clayrune.io/`

# Hostnames issued to users follow this pattern; `{username}` substituted at
# enrollment time.
USER_HOSTNAME_TEMPLATE: str = f"{{username}}.{PLATFORM_DOMAIN}"

# Connect (enrollment) URL the user's browser is sent to.
# When MC_REMOTE_LOCAL_MOCK=1, points at the in-process /api/_mock/connect
# so the full enrollment flow can be exercised without a real control plane.
def connect_url(device_pub_b64: str, csrf_nonce: str, *, username_hint: str | None = None) -> str:
    from urllib.parse import urlencode
    params = {
        "device_pub": device_pub_b64,
        "nonce": csrf_nonce,
        "redirect": MC_CALLBACK_URL,
    }
    if username_hint:
        params["username_hint"] = username_hint
    if os.environ.get("MC_REMOTE_LOCAL_MOCK") == "1":
        return f"{MC_LOCAL_BASE_URL}/api/_mock/connect?{urlencode(params)}"
    return f"https://{PLATFORM_DOMAIN}/connect?{urlencode(params)}"

# ─── Local MC integration ────────────────────────────────────────────────────
# MC's existing Flask server port. Hardcoded — `mc-tunnel` only ever forwards
# to 127.0.0.1:5199 (`04-abuse-prevention.md` Layer 1).
MC_LOCAL_HOST: str = "127.0.0.1"
MC_LOCAL_PORT: int = int(os.environ.get("MC_REMOTE_MC_PORT", "5199"))
MC_LOCAL_BASE_URL: str = f"http://{MC_LOCAL_HOST}:{MC_LOCAL_PORT}"

# Browser callback path served by MC during enrollment.
MC_CALLBACK_PATH: str = "/api/mc-callback"
MC_CALLBACK_URL: str = f"{MC_LOCAL_BASE_URL}{MC_CALLBACK_PATH}"

# ─── Keystore namespace ──────────────────────────────────────────────────────
# Where the Python `keyring` library stashes our secrets. See
# `02-attestation-protocol.md` §3.4.
KEYSTORE_SERVICE: str = "mission-control-remote"
KEYSTORE_KEYS = {
    # logical-name -> keyring entry name within KEYSTORE_SERVICE
    "device_priv":      "device_priv_b64",   # 32-byte Ed25519 seed, base64
    "device_pub":       "device_pub_b64",    # 32-byte Ed25519 pubkey, base64
    "device_id":        "device_id",         # server-issued at enrollment
    "username":         "username",          # user's chosen handle
    "enrollment_token": "enrollment_token",  # opaque server-issued credential
    "hostname":         "hostname",          # <username>.PLATFORM_DOMAIN
}

# ─── mc-tunnel subprocess ────────────────────────────────────────────────────
MC_TUNNEL_BINARY_NAME: str = "mc-tunnel.exe"  # Windows-only in v1
ATTESTATION_ROTATE_SECONDS: int = 600         # 10 min (per protocol §7.5)
NONCE_TTL_SECONDS: int = 30                   # informational; server enforces

# ─── Feature flag ────────────────────────────────────────────────────────────
# When False, `mc_remote` exposes no provider — MC behaves as pure open-core.
# Default True; can be flipped off via env for builds shipped without the
# proprietary glue.
REMOTE_ACCESS_ENABLED: bool = (
    os.environ.get("MC_REMOTE_ENABLED", "1").lower() not in {"0", "false", "no"}
)


def summary() -> dict:
    """For diagnostic logging at MC startup. Never includes secrets."""
    return {
        "platform_domain": PLATFORM_DOMAIN,
        "control_plane_base_url": CONTROL_PLANE_BASE_URL,
        "mc_local_base_url": MC_LOCAL_BASE_URL,
        "remote_access_enabled": REMOTE_ACCESS_ENABLED,
    }
