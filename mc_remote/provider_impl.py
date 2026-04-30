"""
ClayruneProvider — the concrete RemoteAccessProvider implementation for clayrune.io.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

Wired:
- is_enabled / status: read from keystore + live tunnel supervisor state
- begin_enrollment: full browser-mediated enrollment flow
- disable: stops the tunnel supervisor; keystore preserved for fast re-enable
- disconnect_this_device: stops supervisor, clears keystore (server-side
  revoke wires in once the real CP exists)
- get_caps: returns last attestation's caps from the supervisor

Shape matches `mc_remote_iface.RemoteAccessProvider`.
"""
from __future__ import annotations

import logging
from typing import Optional

from mc_remote_iface import ProviderCaps, ProviderStatus

from . import config, device_keys, enrollment, tunnel_supervisor

log = logging.getLogger(__name__)


class ClayruneProvider:
    """Mission Control Cloud provider — talks to clayrune.io."""

    name = "Mission Control Cloud"
    vendor_url = "https://clayrune.io"

    # ─── Lifecycle / status ───────────────────────────────────────────────
    def is_enabled(self) -> bool:
        """True if the user has run through enrollment on this device."""
        return device_keys.is_enrolled()

    def status(self) -> ProviderStatus:
        """Snapshot of current state. Cheap; safe to poll from request handlers."""
        try:
            identity = device_keys.load_identity()
        except device_keys.KeystoreUnavailable as e:
            return ProviderStatus(
                enrolled=False, online=False,
                hostname=None, username=None, last_seen=None,
                error_code="tunnel_keystore_unavailable",
                error_message=f"Couldn't access secure storage: {e}",
            )

        if identity is None:
            return ProviderStatus(
                enrolled=False, online=False,
                hostname=None, username=None, last_seen=None,
                error_code=None, error_message=None,
            )

        # Merge in supervisor state (online, last attestation result)
        sup_status = tunnel_supervisor.get().status()
        online = bool(sup_status.get("online"))
        running = bool(sup_status.get("running"))
        error_code = sup_status.get("error_code")
        # "connecting" = supervisor is actively trying to come up (running)
        # but isn't online yet AND there's no terminal error to surface.
        # Used by the UI to show "Reconnecting…" instead of "Paused".
        connecting = running and not online and not error_code
        return ProviderStatus(
            enrolled=True,
            online=online,
            hostname=identity.hostname,
            username=identity.username,
            last_seen=sup_status.get("last_seen"),
            error_code=error_code,
            error_message=sup_status.get("error_message"),
            connecting=connecting,
        )

    def get_caps(self) -> Optional[ProviderCaps]:
        """Caps from last attestation response. None if not enrolled or never attested."""
        sup_status = tunnel_supervisor.get().status()
        return sup_status.get("caps")

    # ─── User actions ─────────────────────────────────────────────────────
    def begin_enrollment(self) -> str:
        """Start enrollment.

        Modes:
          - **Direct API mode** (when MC_CP_DEV_AUTH=1 + MC_REMOTE_DEV_USERNAME +
            MC_REMOTE_DEV_EMAIL are set): call /v1/enroll directly via HTTP
            against config.control_plane_base_url(). No browser. Returns a
            data: URL so the frontend skips the launch and just refreshes —
            the supervisor is already running by the time this returns.
          - **Browser mode** (default): generate keypair + CSRF nonce, open
            the user's browser to the platform's signin/enrollment page.
            Falls back to local mock when MC_REMOTE_LOCAL_MOCK=1.
        """
        import os
        dev_auth = os.environ.get("MC_CP_DEV_AUTH") == "1"
        dev_user = os.environ.get("MC_REMOTE_DEV_USERNAME", "").strip()
        dev_email = os.environ.get("MC_REMOTE_DEV_EMAIL", "").strip()

        if dev_auth and dev_user and dev_email:
            cp_url = config.control_plane_base_url()
            log.info("direct-enroll mode: cp=%s user=%s email=%s",
                     cp_url, dev_user, dev_email)
            try:
                identity = enrollment.enroll_via_cp(
                    cp_base_url=cp_url,
                    email=dev_email,
                    username=dev_user,
                )
            except Exception as e:
                # Surface the failure as an exception — the API layer will
                # convert to 5xx + error message for the frontend.
                raise RuntimeError(f"Direct enrollment failed: {e}") from e
            # data: URL signals the frontend's skip_browser path
            return (
                "data:text/html;charset=utf-8,"
                "%3C!doctype%20html%3E%3Cmeta%20charset%3Dutf-8%3E"
                "%3Ctitle%3EEnrolled%3C%2Ftitle%3E"
                "%3Cbody%20style%3D%22font-family%3Asystem-ui%3Bpadding%3A40px%3Btext-align%3Acenter%22%3E"
                "%3Ch2%3EEnrolled%20%26%20connected%3C%2Fh2%3E"
                f"%3Cp%3EHostname%3A%20%3Ccode%3E{identity.hostname}%3C%2Fcode%3E%3C%2Fp%3E"
                "%3Cp%3EYou%20can%20close%20this%20window%20and%20return%20to%20Mission%20Control.%3C%2Fp%3E"
                "%3C%2Fbody%3E"
            )

        # Browser mode (default / production)
        return enrollment.begin()

    def disable(self) -> None:
        """Stop the tunnel; keep credentials for fast re-enable."""
        tunnel_supervisor.get().stop()

    def resume(self) -> None:
        """Restart the tunnel for an already-enrolled device. Idempotent."""
        if not device_keys.is_enrolled():
            raise RuntimeError("Cannot resume: no enrolled device.")
        tunnel_supervisor.maybe_start(cp_base_url=config.control_plane_base_url())

    def disconnect_this_device(self) -> None:
        """Stop the tunnel, revoke on the platform, clear keystore.

        Order:
          1. Stop supervisor (so it doesn't attest with about-to-be-revoked creds)
          2. Read identity from keystore (still needed for /v1/devices/{id}/revoke auth)
          3. POST /v1/devices/{device_id}/revoke — server deletes CF resources
             + Firestore row + releases username claim
          4. Clear local keystore unconditionally (user wants out, even if step 3 failed)

        Network failure on step 3 is non-fatal: the user is still cleanly
        disconnected locally, and /v1/enroll's self-healing wipes any
        orphan CF resources next time the user re-enrolls.
        """
        # 1. Stop the supervisor first
        try:
            tunnel_supervisor.get().stop()
        except Exception as e:
            log.warning("supervisor stop during disconnect raised: %s", e)

        # 2. Read identity (need device_id + enrollment_token to call /revoke)
        try:
            identity = device_keys.load_identity()
        except device_keys.KeystoreUnavailable as e:
            log.warning("could not read identity for revoke: %s", e)
            identity = None

        # 3. Server-side revoke — best-effort
        if identity is not None:
            try:
                enrollment.revoke_via_cp(
                    cp_base_url=config.control_plane_base_url(),
                    device_id=identity.device_id,
                    enrollment_token=identity.enrollment_token,
                )
            except Exception as e:
                log.warning("server-side revoke raised, proceeding to local cleanup: %s", e)

        # 4. Clear keystore unconditionally
        device_keys.clear_identity()
