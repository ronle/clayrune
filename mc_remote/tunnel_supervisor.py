"""
tunnel_supervisor — Background attestation + cloudflared lifecycle.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

Owns two concurrent loops:

    Attestation loop  (slow, default 10 min)
        ─ Calls /v1/nonce + /v1/attest
        ─ On success: starts/swaps cloudflared with the issued tunnel token
        ─ On failure: exponential backoff, online=false

    Watchdog loop     (fast, default 5s)
        ─ Polls cloudflared.is_alive()
        ─ Surfaces crashes immediately so the Settings panel reflects them
        ─ Triggers an out-of-band attestation retry if cloudflared dies

`online` semantics: True iff the last attestation succeeded AND cloudflared
is currently alive. Either condition flipping false drops `online`.

cloudflared in MC_REMOTE_LOCAL_MOCK mode is a no-op stub (see cloudflared.py)
so the full lifecycle can be exercised without a real binary or CF account.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os as _os
import threading
import time
from dataclasses import dataclass
from typing import Optional

import requests

from . import attestation, cloudflared, config, device_keys

log = logging.getLogger(__name__)


_DEFAULT_INTERVAL_S = float(_os.environ.get("MC_REMOTE_ATTEST_INTERVAL_S", "600"))
_WATCHDOG_INTERVAL_S = float(_os.environ.get("MC_REMOTE_WATCHDOG_S", "5"))
_BACKOFF_MIN_S = 5.0
_BACKOFF_MAX_S = 60.0


@dataclass
class SupervisorState:
    running: bool = False
    last_attestation: Optional[attestation.AttestationResult] = None
    started_at: Optional[_dt.datetime] = None
    stopping: bool = False


class TunnelSupervisor:
    """Single supervisor instance per MC process."""

    def __init__(self) -> None:
        self._state = SupervisorState()
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._wake_attest = threading.Event()  # forces an out-of-band attest
        self._attest_thread: Optional[threading.Thread] = None
        self._watchdog_thread: Optional[threading.Thread] = None
        self._session: Optional[requests.Session] = None
        self._cp_base_url: Optional[str] = None

    # ─── Lifecycle ────────────────────────────────────────────────────────

    def start(self, *, cp_base_url: Optional[str] = None) -> None:
        """Spawn the attestation + watchdog threads. Idempotent."""
        with self._lock:
            if self._state.running:
                return
            if device_keys.load_identity() is None:
                raise RuntimeError("Cannot start supervisor: no enrolled identity in keystore")
            self._cp_base_url = cp_base_url
            self._cancel.clear()
            self._wake_attest.clear()
            self._session = requests.Session()
            self._state = SupervisorState(
                running=True,
                started_at=_dt.datetime.now(_dt.timezone.utc),
            )
            self._attest_thread = threading.Thread(
                target=self._attest_loop, name="mc-tunnel-attest", daemon=True,
            )
            self._watchdog_thread = threading.Thread(
                target=self._watchdog_loop, name="mc-tunnel-watchdog", daemon=True,
            )
            self._attest_thread.start()
            self._watchdog_thread.start()
            log.info("tunnel supervisor started (cp=%s)",
                     cp_base_url or config.control_plane_base_url())

    def stop(self, *, timeout: float = 5.0) -> None:
        """Signal cancel, stop cloudflared, wait for threads to exit."""
        with self._lock:
            if not self._state.running:
                return
            self._state.stopping = True
        self._cancel.set()
        self._wake_attest.set()
        # Stop cloudflared OUTSIDE the lock — it may take a couple of seconds
        try:
            cloudflared.get().stop()
        except Exception as e:
            log.warning("cloudflared stop raised during supervisor.stop(): %s", e)
        for t in (self._attest_thread, self._watchdog_thread):
            if t is not None:
                t.join(timeout=timeout)
        with self._lock:
            self._state = SupervisorState()
            if self._session is not None:
                try:
                    self._session.close()
                except Exception:
                    pass
                self._session = None
            self._attest_thread = None
            self._watchdog_thread = None
        log.info("tunnel supervisor stopped")

    def is_running(self) -> bool:
        with self._lock:
            return self._state.running

    # ─── Status ───────────────────────────────────────────────────────────

    def status(self) -> dict:
        """Snapshot for the Settings panel. Cheap; no I/O."""
        with self._lock:
            s = self._state
            last = s.last_attestation
            running = s.running

        cf = cloudflared.get()
        cf_alive = False
        cf_err: Optional[str] = None
        try:
            cf_alive = cf.is_alive()
            cf_err = cf.last_error()
        except Exception as e:
            cf_err = f"cloudflared status error: {e}"

        # Online iff last attestation OK AND cloudflared currently up
        online = bool(isinstance(last, attestation.AttestationOk) and cf_alive)

        out: dict = {
            "running": running,
            "online": online,
            "cloudflared_alive": cf_alive,
            "last_seen": None,
            "error_code": None,
            "error_message": None,
            "caps": None,
        }
        if isinstance(last, attestation.AttestationOk):
            out["last_seen"] = last.received_at.isoformat(timespec="seconds").replace("+00:00", "Z")
            out["caps"] = last.caps
            # If attestation OK but cloudflared down, surface that to UI
            if not cf_alive:
                out["error_code"] = "tunnel_cloudflared_down"
                out["error_message"] = (
                    cf_err or "Tunnel daemon stopped responding. Reconnecting…"
                )
        elif isinstance(last, attestation.AttestationFailure):
            out["error_code"] = last.code
            out["error_message"] = last.message
            out["last_seen"] = last.received_at.isoformat(timespec="seconds").replace("+00:00", "Z")
        return out

    # ─── Attestation loop ─────────────────────────────────────────────────

    def _attest_loop(self) -> None:
        backoff = _BACKOFF_MIN_S
        while not self._cancel.is_set():
            try:
                result = attestation.attest_once(
                    session=self._session,                 # type: ignore[arg-type]
                    cp_base_url=self._cp_base_url,
                )
            except Exception as e:
                log.exception("attestation crashed: %s", e)
                with self._lock:
                    self._state.last_attestation = attestation.AttestationFailure(
                        code="internal_error", message=str(e), http_status=0,
                        received_at=_dt.datetime.now(_dt.timezone.utc),
                    )
                self._wait_for_next(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX_S)
                continue

            with self._lock:
                self._state.last_attestation = result

            if isinstance(result, attestation.AttestationOk):
                # Hand the issued tunnel token to cloudflared. start() is
                # idempotent: same token = no-op, different = restart.
                try:
                    cloudflared.get().swap_token(result.tunnel_token)
                except cloudflared.CloudflaredError as e:
                    log.error("cloudflared could not start: %s", e)
                    # We have a valid attestation, but cloudflared is unusable.
                    # status() will surface tunnel_cloudflared_down via the
                    # cf_alive=False check; backoff and retry.
                    self._wait_for_next(_BACKOFF_MIN_S)
                    continue

                backoff = _BACKOFF_MIN_S

                # Honor server-issued directives (force_logout, etc.)
                if self._handle_directives(result.directives):
                    return

                delta = (result.next_attestation_after - _dt.datetime.now(_dt.timezone.utc)).total_seconds()
                sleep_for = max(5.0, min(delta, _DEFAULT_INTERVAL_S))
            else:
                sleep_for = backoff
                backoff = min(backoff * 2, _BACKOFF_MAX_S)

            self._wait_for_next(sleep_for)

    # ─── Watchdog loop ────────────────────────────────────────────────────

    def _watchdog_loop(self) -> None:
        """Poll cloudflared health between attestations.

        If cloudflared crashes, wake the attestation thread for an
        out-of-band retry (which will re-issue start() on the next OK).
        """
        was_alive: Optional[bool] = None
        while not self._cancel.is_set():
            self._cancel.wait(timeout=_WATCHDOG_INTERVAL_S)
            if self._cancel.is_set():
                return
            try:
                alive = cloudflared.get().is_alive()
            except Exception:
                alive = False
            if was_alive is True and alive is False:
                log.warning("cloudflared crashed; waking attest loop for re-issue")
                self._wake_attest.set()
            was_alive = alive

    def _wait_for_next(self, seconds: float) -> None:
        """Cancel-aware sleep; also returns early if watchdog wakes us."""
        # Wait on either cancel or wake_attest
        end = time.time() + seconds
        while not self._cancel.is_set():
            remaining = end - time.time()
            if remaining <= 0:
                return
            if self._wake_attest.wait(timeout=min(remaining, 1.0)):
                self._wake_attest.clear()
                return

    def _handle_directives(self, directives: list[dict]) -> bool:
        """Return True if a terminal directive was processed and the loop should exit."""
        for d in directives:
            t = d.get("type")
            if t in ("force_logout", "update_required"):
                log.warning("server requested %s; supervisor exiting", t)
                self._cancel.set()
                try:
                    cloudflared.get().stop()
                except Exception:
                    pass
                return True
            # Other directives observed but non-terminal.
        return False


# ─── Singleton accessor ──────────────────────────────────────────────────────

_supervisor: Optional[TunnelSupervisor] = None
_supervisor_lock = threading.Lock()


def get() -> TunnelSupervisor:
    global _supervisor
    with _supervisor_lock:
        if _supervisor is None:
            _supervisor = TunnelSupervisor()
        return _supervisor


def maybe_start(*, cp_base_url: Optional[str] = None) -> bool:
    """Start the supervisor IF an identity is enrolled. Returns True if started.

    Safe to call repeatedly — idempotent.
    """
    if device_keys.load_identity() is None:
        return False
    sup = get()
    if sup.is_running():
        return False
    sup.start(cp_base_url=cp_base_url)
    return True
