"""Development stub provider for UI testing.

OPEN SOURCE. Part of Mission Control core.

A fake `RemoteAccessProvider` that lets you preview the "enrolled" and
"online" Settings panel states without setting up any real infrastructure
(no clayrune.io, no Cloudflare, no cryptography).

Activate by setting the env var:

    MC_DEV_REMOTE_STUB=offline   # enrolled, tunnel down
    MC_DEV_REMOTE_STUB=online    # enrolled, tunnel up
    MC_DEV_REMOTE_STUB=error     # enrolled, tunnel error
    MC_DEV_REMOTE_STUB=fresh     # not enrolled (matches "Coming Soon" state for forks)

This module does NOT auto-register itself. Import + activate explicitly
from your dev launcher or the main MC startup code if `MC_DEV_REMOTE_STUB`
is set. See `_maybe_register_dev_stub()` below.
"""
from __future__ import annotations

import datetime as _dt
import os
from typing import Optional

from .provider import (
    ProviderCaps,
    ProviderStatus,
    RemoteAccessProvider,
    register_provider,
)


class _DevStubProvider:
    """Fake provider. Returns whatever `MC_DEV_REMOTE_STUB` says."""

    name = "Mission Control Cloud (DEV STUB)"
    vendor_url = "https://clayrune.io"

    def _mode(self) -> str:
        return os.environ.get("MC_DEV_REMOTE_STUB", "offline").strip().lower()

    def is_enabled(self) -> bool:
        return self._mode() != "fresh"

    def status(self) -> ProviderStatus:
        m = self._mode()
        now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

        if m == "fresh":
            return ProviderStatus(False, False, None, None, None, None, None)
        if m == "online":
            return ProviderStatus(
                enrolled=True, online=True,
                hostname="ron.clayrune.io", username="ron",
                last_seen=now_iso, error_code=None, error_message=None,
            )
        if m == "error":
            return ProviderStatus(
                enrolled=True, online=False,
                hostname="ron.clayrune.io", username="ron",
                last_seen=now_iso,
                error_code="bad_client_signature",
                error_message="Couldn't connect — try restarting Mission Control.",
            )
        # default: offline (enrolled but tunnel not running)
        return ProviderStatus(
            enrolled=True, online=False,
            hostname="ron.clayrune.io", username="ron",
            last_seen=now_iso, error_code=None, error_message=None,
        )

    def get_caps(self) -> Optional[ProviderCaps]:
        if self._mode() in ("fresh",):
            return None
        return ProviderCaps(
            bandwidth_quota_period_bytes=5 * 1024 ** 3,
            bandwidth_used_period_bytes=237 * 1024 ** 2,
            rate_limit_rps=60,
            max_response_bytes=10 * 1024 ** 2,
            max_concurrent_connections=20,
        )

    def begin_enrollment(self) -> str:
        # When the dev stub is active, instantly transition to "online" state
        # without actually opening a browser or running enrollment. This is
        # the cheapest preview path for UI work.
        os.environ["MC_DEV_REMOTE_STUB"] = "online"
        # Return a `data:` URL that just shows a confirmation page, so the
        # frontend's "open browser" toast still has something to point at.
        # No real network involvement.
        return (
            "data:text/html;charset=utf-8,"
            "%3C!doctype%20html%3E%3Chtml%3E%3Cbody%20style%3D%22"
            "font-family%3Asystem-ui%3Bpadding%3A40px%3Btext-align%3Acenter%22%3E"
            "%3Ch2%3EDev%20stub%20enrolled%20%E2%9C%93%3C%2Fh2%3E"
            "%3Cp%3EReturn%20to%20Mission%20Control%20%E2%80%94%20the%20Settings%20"
            "panel%20will%20now%20show%20the%20%3Cstrong%3Eonline%3C%2Fstrong%3E%20"
            "state.%3C%2Fp%3E%3Cp%3EYou%20can%20close%20this%20tab.%3C%2Fp%3E"
            "%3C%2Fbody%3E%3C%2Fhtml%3E"
        )

    def disable(self) -> None:
        os.environ["MC_DEV_REMOTE_STUB"] = "offline"

    def resume(self) -> None:
        os.environ["MC_DEV_REMOTE_STUB"] = "online"

    def disconnect_this_device(self) -> None:
        os.environ["MC_DEV_REMOTE_STUB"] = "fresh"


def maybe_register_dev_stub() -> bool:
    """Register the dev stub if `MC_DEV_REMOTE_STUB` is set. Returns True if registered."""
    if not os.environ.get("MC_DEV_REMOTE_STUB"):
        return False
    try:
        register_provider(_DevStubProvider())
    except RuntimeError:
        # A real provider got there first; that's fine — don't override it.
        return False
    return True
