"""
enrollment — Browser-mediated first-run enrollment flow.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

Implements the flow from `02-attestation-protocol.md` §6:

    1. MC generates a device keypair (private stays on device).
    2. MC opens user's browser to:
         https://PLATFORM_DOMAIN/connect
           ?device_pub=...
           &nonce=<csrf>
           &redirect=http://127.0.0.1:5199/api/mc-callback
    3. User signs in (Firebase Auth) and picks username on clayrune.io.
    4. Control plane creates user/device, provisions CF tunnel, redirects to:
         http://127.0.0.1:5199/api/mc-callback
           ?nonce=<original>
           &enrollment_token=...
           &username=...
           &device_id=...
           &hostname=...
    5. /api/mc-callback calls complete() — we validate nonce, validate
       fields, persist to keystore. Browser sees a success page.

Pending state (the just-generated keypair + matching CSRF nonce) lives in
process memory only. If the user abandons enrollment, the private key
simply gets GC'd — no keystore pollution. TTL is 15 minutes.

Concurrency: the in-memory dict is guarded by a Lock. Multiple concurrent
enrollments on the same MC instance are unusual but not forbidden.
"""
from __future__ import annotations

import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Optional

from . import config, device_keys

log = logging.getLogger(__name__)

# 15 minutes — matches `enrollment_intents` TTL on the control plane side.
_PENDING_TTL_SECONDS = 15 * 60


@dataclass
class _PendingEnrollment:
    """In-memory pending state for an enrollment in progress."""
    device_pub_b64: str
    device_priv_b64: str  # secret — held only in this process, never on disk
    created_at: float


_lock = threading.Lock()
_pending: dict[str, _PendingEnrollment] = {}  # nonce -> pending


# ─── Public API ──────────────────────────────────────────────────────────────


def begin() -> str:
    """
    Start an enrollment. Generates a fresh device keypair + CSRF nonce,
    stashes the pending state in memory, and returns the browser URL.

    The keypair is NOT persisted yet — only on successful complete().
    """
    _gc_expired()

    pub_b64, priv_b64 = device_keys.generate_keypair()
    nonce = secrets.token_urlsafe(32)

    with _lock:
        _pending[nonce] = _PendingEnrollment(
            device_pub_b64=pub_b64,
            device_priv_b64=priv_b64,
            created_at=time.time(),
        )

    return config.connect_url(pub_b64, nonce)


def complete(query: dict) -> dict:
    """
    Validate a callback from the control plane and persist the enrolled
    identity to the OS keystore.

    `query` is the dict of query-string parameters from /api/mc-callback.

    Returns:
      {"ok": True, "identity": DeviceIdentity}  on success
      {"ok": False, "error": <code>, "message": <msg>}  on validation failure
    """
    nonce = (query.get("nonce") or "").strip()
    if not nonce:
        return _err("missing_nonce", "Sign-in callback was missing its CSRF token.")

    with _lock:
        pending = _pending.pop(nonce, None)
        # Defensive: if many stale entries piled up, clear them too.
        _gc_expired_locked()

    if pending is None:
        return _err("enrollment_intent_invalid",
                    "Sign-in expired or did not match this Mission Control. Click 'Enable Remote Access' again.")

    # Field presence
    enrollment_token = (query.get("enrollment_token") or "").strip()
    username         = (query.get("username") or "").strip()
    device_id        = (query.get("device_id") or "").strip()
    hostname         = (query.get("hostname") or "").strip()
    missing = [name for name, v in (
        ("enrollment_token", enrollment_token),
        ("username", username),
        ("device_id", device_id),
        ("hostname", hostname),
    ) if not v]
    if missing:
        return _err("malformed_callback",
                    f"Sign-in callback was missing fields: {', '.join(missing)}")

    # Field shape — defense against open-redirect / tampering. Username must
    # match the policy from `03-control-plane-api.md` §3.5; hostname must be
    # `<username>.PLATFORM_DOMAIN`.
    if not _USERNAME_RE.fullmatch(username):
        return _err("malformed_callback",
                    "Sign-in returned an invalid username.")
    expected_host = f"{username}.{config.PLATFORM_DOMAIN}"
    if hostname.lower() != expected_host.lower():
        return _err("hostname_mismatch",
                    f"Hostname '{hostname}' didn't match expected '{expected_host}'.")

    # Persist
    identity = device_keys.DeviceIdentity(
        device_id=device_id,
        device_pub_b64=pending.device_pub_b64,
        username=username,
        hostname=hostname,
        enrollment_token=enrollment_token,
    )
    try:
        device_keys.store_identity(identity, pending.device_priv_b64)
    except device_keys.KeystoreUnavailable as e:
        return _err("tunnel_keystore_unavailable",
                    f"Couldn't save secure storage: {e}")

    log.info("enrollment complete for user=%s device_id=%s", username, device_id)

    # Start the tunnel supervisor immediately so the user's status pill
    # reflects the live tunnel rather than waiting for the next refresh.
    try:
        from . import tunnel_supervisor
        tunnel_supervisor.maybe_start(cp_base_url=config.control_plane_base_url())
    except Exception as e:
        log.warning("could not start tunnel supervisor after enrollment: %s", e)

    return {"ok": True, "identity": identity}


def enroll_via_cp(*, cp_base_url: str, email: str, username: str,
                  device_name: str = "Mission Control PC", os_str: str = "win32",
                  mc_version: str = "1.4.2",
                  dev_auth: bool = True) -> device_keys.DeviceIdentity:
    """Direct API enrollment — call /v1/enroll via HTTP, persist to keystore, return identity.

    Bypasses the browser flow (begin/complete). Used when:
      - we have a control plane to talk to (via cp_base_url)
      - we have user identity (email + chosen username) — no Firebase signin needed
      - MC_CP_DEV_AUTH=1 on the CP side so X-Dev-User-Email is honored

    Side effects (on success):
      - real CF tunnel + DNS + Access app provisioned
      - real device row + user row in Firestore
      - keypair generated client-side; private key persisted to OS keystore
      - enrollment_token persisted to OS keystore (only ever returned by /v1/enroll once)

    Raises RuntimeError on any failure; nothing is persisted to keystore on failure.
    """
    import requests

    pub_b64, priv_b64 = device_keys.generate_keypair()
    body = {
        "device_pub_b64": pub_b64,
        "csrf_nonce": secrets.token_urlsafe(32),  # CP accepts when dev auth on
        "username": username,
        "device_name": device_name,
        "os": os_str,
        "mc_version": mc_version,
    }
    headers = {"Content-Type": "application/json"}
    if dev_auth:
        headers["X-Dev-User-Email"] = email
    else:
        # Future: pass Firebase ID token here
        raise NotImplementedError("Non-dev-auth direct enrollment requires Firebase ID token; not wired yet")

    log.info("enrolling via %s (username=%s, email=%s)",
             cp_base_url, username, email)
    try:
        r = requests.post(f"{cp_base_url.rstrip('/')}/enroll",
                          json=body, headers=headers, timeout=60.0)
    except requests.RequestException as e:
        raise RuntimeError(f"Could not reach control plane at {cp_base_url}: {e}") from e

    if r.status_code != 200:
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text[:200]}
        raise RuntimeError(f"/v1/enroll returned HTTP {r.status_code}: {body}")

    rsp = r.json()
    identity = device_keys.DeviceIdentity(
        device_id=rsp["device_id"],
        device_pub_b64=pub_b64,
        username=rsp["username"],
        hostname=rsp["hostname"],
        enrollment_token=rsp["enrollment_token"],
    )

    try:
        device_keys.store_identity(identity, priv_b64)
    except device_keys.KeystoreUnavailable as e:
        raise RuntimeError(f"Could not save to OS keystore: {e}") from e

    log.info("enrollment complete via direct API (user=%s device_id=%s host=%s)",
             username, identity.device_id, identity.hostname)

    # Start the supervisor so the tunnel comes up immediately
    try:
        from . import tunnel_supervisor
        tunnel_supervisor.maybe_start(cp_base_url=cp_base_url)
    except Exception as e:
        log.warning("could not start tunnel supervisor after direct enrollment: %s", e)

    return identity


def list_devices_via_cp(*, cp_base_url: str, email: str, this_device_id: Optional[str] = None,
                        timeout: float = 15.0) -> dict:
    """GET /v1/devices for the user identified by `email` (dev-auth path).

    Returns the response dict directly, e.g.
      { "devices": [...], "tier": "free", "device_cap": 2 }
    On network or HTTP error, returns an error dict with `error` + `message`.
    """
    import requests

    headers = {"X-Dev-User-Email": email}
    if this_device_id:
        headers["X-MC-Device-Id"] = this_device_id

    url = f"{cp_base_url.rstrip('/')}/devices"
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
    except requests.RequestException as e:
        log.warning("list_devices: network error: %s", e)
        return {"error": "network_error", "message": str(e), "devices": []}

    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text[:200]}
    if r.status_code != 200:
        log.warning("list_devices: HTTP %d: %s", r.status_code, body)
        body.setdefault("error", "http_error")
        body.setdefault("devices", [])
    return body


def list_sessions_via_cp(*, cp_base_url: str, email: str, timeout: float = 15.0) -> dict:
    """GET /v1/sessions for the given email."""
    import requests
    url = f"{cp_base_url.rstrip('/')}/sessions"
    try:
        r = requests.get(url, headers={"X-Dev-User-Email": email}, timeout=timeout)
    except requests.RequestException as e:
        return {"error": "network_error", "message": str(e), "sessions": []}
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text[:200]}
    if r.status_code != 200:
        body.setdefault("error", "http_error")
        body.setdefault("sessions", [])
    return body


def revoke_session_via_cp(*, cp_base_url: str, email: str, session_id: str,
                          strict: bool = False,
                          timeout: float = 20.0) -> dict:
    """POST /v1/sessions/{session_id}/revoke.

    With strict=True, the CP returns 503 instead of falling back to revoke-all
    when per-session revoke isn't supported by CF — used by automated cleanup
    loops that must not nuke unrelated named sessions.
    """
    import requests
    url = f"{cp_base_url.rstrip('/')}/sessions/{session_id}/revoke"
    if strict:
        url += "?strict=1"
    try:
        r = requests.post(url, headers={"X-Dev-User-Email": email}, timeout=timeout)
    except requests.RequestException as e:
        return {"error": "network_error", "message": str(e)}
    try:
        body = r.json()
    except Exception:
        body = {"error": "bad_response", "raw": r.text[:200]}
    if r.status_code != 200:
        body.setdefault("error", "http_error")
        body.setdefault("status", r.status_code)
    return body


def revoke_all_sessions_via_cp(*, cp_base_url: str, email: str, timeout: float = 20.0) -> dict:
    """POST /v1/sessions/revoke-all."""
    import requests
    url = f"{cp_base_url.rstrip('/')}/sessions/revoke-all"
    try:
        r = requests.post(url, headers={"X-Dev-User-Email": email}, timeout=timeout)
    except requests.RequestException as e:
        return {"error": "network_error", "message": str(e)}
    try:
        return r.json()
    except Exception:
        return {"error": "bad_response", "raw": r.text[:200]}


def revoke_via_cp(*, cp_base_url: str, device_id: str, enrollment_token: str,
                  timeout: float = 30.0) -> dict:
    """POST /v1/devices/{device_id}/revoke. Returns the response dict.

    Best-effort by design: caller should clear keystore + stop supervisor
    regardless of result. If the network is down or the CP is unreachable,
    the user still gets out locally, and the orphan CF resources can be
    cleaned up by /v1/enroll's self-healing on next enroll.
    """
    import requests

    url = f"{cp_base_url.rstrip('/')}/devices/{device_id}/revoke"
    log.info("revoking device %s via %s", device_id, cp_base_url)
    try:
        r = requests.post(url, json={"enrollment_token": enrollment_token}, timeout=timeout)
    except requests.RequestException as e:
        log.warning("revoke: network error reaching CP: %s", e)
        return {"ok": False, "error": "network_error", "message": str(e)}
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text[:200]}
    if r.status_code != 200:
        log.warning("revoke: CP returned HTTP %d: %s", r.status_code, body)
    else:
        log.info("revoke OK: %s", body)
    return body


def cancel() -> int:
    """Cancel any pending enrollments. Returns count cancelled. For tests."""
    with _lock:
        n = len(_pending)
        _pending.clear()
    return n


def pending_count() -> int:
    """Diagnostic: number of in-flight enrollments."""
    with _lock:
        return len(_pending)


# ─── Internals ───────────────────────────────────────────────────────────────


# Same regex as control plane's username policy (03- §3.5):
#   3–24 chars, lowercase a-z / 0-9, dash allowed but not at start/end or
#   consecutive.
_USERNAME_RE = re.compile(r"^[a-z0-9](-?[a-z0-9])*$")


def _gc_expired() -> None:
    with _lock:
        _gc_expired_locked()


def _gc_expired_locked() -> None:
    """Drop pending entries past TTL. Caller must hold _lock."""
    now = time.time()
    stale = [n for n, p in _pending.items() if now - p.created_at > _PENDING_TTL_SECONDS]
    for n in stale:
        del _pending[n]


def _err(code: str, message: str) -> dict:
    return {"ok": False, "error": code, "message": message}
