"""
mc_remote — Mission Control Cloud remote-access provider.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.
Distributed only under the Mission Control Cloud Terms of Service.

This package implements the open `mc_remote_iface.RemoteAccessProvider` Protocol
against the clayrune.io platform. It manages:
  - Per-device Ed25519 keypair (OS keystore)
  - Browser-mediated enrollment flow
  - Spawning and supervising the proprietary `mc-tunnel` Rust binary
  - Surfacing tunnel state to MC's frontend

Importing this package registers it as the active provider. MC core's
Settings panel discovers it via `mc_remote_iface.get_provider()`.

See `docs/remote-access/01-architecture.md` for system context.
"""
from __future__ import annotations

import logging

from . import config

log = logging.getLogger(__name__)


def _maybe_register() -> None:
    """Register with mc_remote_iface unless explicitly disabled via env."""
    if not config.REMOTE_ACCESS_ENABLED:
        log.info("mc_remote disabled via env (MC_REMOTE_ENABLED=0); not registering")
        return
    try:
        from mc_remote_iface import register_provider
    except ImportError:
        log.warning("mc_remote_iface not importable; provider not registered")
        return

    from .provider_impl import ClayruneProvider
    register_provider(ClayruneProvider())
    log.info("mc_remote registered as remote-access provider: %s", config.summary())

    # If a device is already enrolled (user enrolled in a previous run),
    # start the tunnel supervisor automatically so the panel shows the
    # right state without requiring the user to click anything.
    #
    # Runs on a daemon thread because:
    # - tunnel_supervisor.maybe_start() calls device_keys.load_identity()
    #   which calls keyring.get_password()
    # - On headless Linux (Ubuntu desktop VM at first boot, server VMs,
    #   WSL without DBUS_SESSION_BUS_ADDRESS, etc.) keyring's secretstorage
    #   backend tries to talk to org.freedesktop.secrets over D-Bus, and
    #   when that service isn't running OR D-Bus is unavailable, the call
    #   blocks INDEFINITELY waiting for a reply
    # - Doing this synchronously at module-import time meant `import
    #   mc_remote` from server.py never returned, so server.py never
    #   reached app.run() and nothing bound to port 5199
    # The daemon thread isolates the hang: if keyring hangs forever, the
    # main MC process still starts and serves traffic; remote-access just
    # stays "not yet started" until the user explicitly clicks Enable.
    import threading
    def _bg_autostart() -> None:
        try:
            from . import tunnel_supervisor
            started = tunnel_supervisor.maybe_start(
                cp_base_url=config.control_plane_base_url(),
            )
            if started:
                log.info("tunnel supervisor auto-started (existing enrollment)")
        except Exception as e:
            log.warning("could not auto-start tunnel supervisor: %s", e)
    threading.Thread(
        target=_bg_autostart,
        name="mc_remote-autostart",
        daemon=True,
    ).start()


_maybe_register()
