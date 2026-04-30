"""
mc_remote_iface — Remote-access provider interface.

OPEN SOURCE. Part of Mission Control core.
This module ships in MC core regardless of whether any remote-access
provider is installed.

A "remote-access provider" makes the dashboard reachable from outside the
user's local network. The reference implementation is `mc_remote` (proprietary,
talks to clayrune.io). Forks of MC are welcome to ship their own provider
against Tailscale, ngrok, their own infrastructure, etc.

Usage from MC core:
    from mc_remote_iface import get_provider, ProviderStatus

    p = get_provider()
    if p is None:
        # Show "No remote access provider installed" CTA
        ...
    else:
        status = p.status()
        ...

Providers register themselves at import time:
    from mc_remote_iface import register_provider
    register_provider(MyProvider())

Open-core licensing context: `docs/remote-access/07-licensing.md` §4.
"""
from __future__ import annotations

from .provider import (
    RemoteAccessProvider,
    ProviderStatus,
    ProviderCaps,
    get_provider,
    register_provider,
    clear_provider,
)

__all__ = [
    "RemoteAccessProvider",
    "ProviderStatus",
    "ProviderCaps",
    "get_provider",
    "register_provider",
    "clear_provider",
]
