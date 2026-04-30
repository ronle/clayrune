"""
Remote-access provider Protocol — the contract a provider must fulfill.

OPEN SOURCE. Part of Mission Control core.

This file is the documented interface a fork can implement to plug their own
remote-access infrastructure into MC's frontend Settings panel. See
`docs/remote-access/07-licensing.md` §4 for the open-core rationale.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable


# ─── Status / caps DTOs ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class ProviderStatus:
    """A snapshot of the provider's runtime state. Returned by .status()."""

    enrolled: bool                  # Has the user completed enrollment?
    online: bool                    # Is the tunnel currently up?
    hostname: Optional[str]         # e.g. "ron.clayrune.io" — None if not enrolled
    username: Optional[str]
    last_seen: Optional[str]        # ISO 8601 of last successful attestation
    error_code: Optional[str]       # One of `error_codes.md` codes; None if ok
    error_message: Optional[str]    # Human-readable; localized client-side
    connecting: bool = False        # True when the provider is actively trying
                                    # to bring the tunnel up (e.g. after Resume)
                                    # but online is not yet True. Distinguishes
                                    # "intentionally paused" from "reconnecting".


@dataclass(frozen=True)
class ProviderCaps:
    """Bandwidth / rate-limit caps as last reported by the platform."""

    bandwidth_quota_period_bytes: int
    bandwidth_used_period_bytes: int
    rate_limit_rps: int
    max_response_bytes: int
    max_concurrent_connections: int


# ─── Provider Protocol ───────────────────────────────────────────────────────

@runtime_checkable
class RemoteAccessProvider(Protocol):
    """
    The interface implemented by any remote-access provider.

    Implementations:
      - `mc_remote` (proprietary, talks to clayrune.io) — the reference impl
      - Forks of MC may ship their own provider (Tailscale, ngrok, custom)

    All methods MUST be safe to call from MC's Flask request handlers
    (i.e. fast, non-blocking — long work delegated to background threads).
    """

    # ─── Identity ─────────────────────────────────────────────────────────
    @property
    def name(self) -> str:
        """Human-readable provider name. e.g. 'Mission Control Cloud'."""
        ...

    @property
    def vendor_url(self) -> str:
        """Where users learn about / sign up for this provider."""
        ...

    # ─── Lifecycle ────────────────────────────────────────────────────────
    def is_enabled(self) -> bool:
        """True if the user has chosen to enable this provider on this MC."""
        ...

    def status(self) -> ProviderStatus:
        """Current runtime state. Cheap; safe to poll."""
        ...

    def get_caps(self) -> Optional[ProviderCaps]:
        """Last reported caps. None if unknown / not enrolled."""
        ...

    # ─── User actions ─────────────────────────────────────────────────────
    def begin_enrollment(self) -> str:
        """
        Start enrollment. Returns the URL to open in the user's browser.
        Does NOT open the browser itself — MC's frontend handles that.
        Side effect: starts a short-lived listener for the OAuth-style
        callback if the provider needs one.
        """
        ...

    def disable(self) -> None:
        """Stop the tunnel. Keeps enrollment so re-enable is cheap."""
        ...

    def resume(self) -> None:
        """Reverse of disable(): start the tunnel for an already-enrolled device.

        Implementations should be idempotent — calling resume() while the
        tunnel is already running should be a no-op rather than an error.
        Implementations should raise if the device is not enrolled.
        """
        ...

    def disconnect_this_device(self) -> None:
        """
        Revoke this device on the platform side and clear local credentials.
        Re-enabling will require fresh enrollment.
        """
        ...


# ─── Registration ────────────────────────────────────────────────────────────

_provider: Optional[RemoteAccessProvider] = None


def register_provider(p: RemoteAccessProvider) -> None:
    """Called by a provider module at import time to register itself."""
    global _provider
    if _provider is not None and _provider is not p:
        raise RuntimeError(
            f"Multiple remote-access providers registered: "
            f"{_provider.name!r} and {p.name!r}. Only one is supported."
        )
    _provider = p


def get_provider() -> Optional[RemoteAccessProvider]:
    """Return the registered provider, or None if no provider is installed."""
    return _provider


def clear_provider() -> None:
    """Test-helper / hot-reload helper. Drops the registered provider."""
    global _provider
    _provider = None
