"""The suspension denylist — the edge's immediate kill switch.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

The session JWT's 30-minute TTL is a *deliberate* revocation lag. For a
cancellation that is fine — nobody is harmed because a cancelled user kept their
dashboard for another twenty minutes. For **fraud, a chargeback, or an abuse
suspension it is not fine**, and waiting out a TTL is not an acceptable answer.

So suspension writes `u:{user_id}` into the Cloudflare Workers KV namespace the
Worker reads on every request:

    if (env.DENYLIST) {
      const denied = await env.DENYLIST.get(`u:${claims.sub}`);
      if (denied) return new Response('Account suspended', { status: 403 });
    }

~1ms at the edge, and it costs us nothing to keep the no-origin-call property for
the other 99.99% of requests.

**KV is the mirror, Firestore is the truth.** `users/{id}.suspended` is
authoritative; the KV key is a cache of it for the edge. A failed KV write must
therefore never fail the suspension itself — it degrades the lag back to one JWT
TTL, which is the *old* behaviour, not a new hole. It is logged loudly and
`suspend()` reports it so the caller can retry.

Set `CLOUDFLARE_KV_DENYLIST_ID` to the namespace id from
`wrangler kv namespace create DENYLIST`. Unset (local dev) → the writes no-op with
a warning.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from . import cloudflare

log = logging.getLogger(__name__)


def namespace_id() -> Optional[str]:
    return os.environ.get("CLOUDFLARE_KV_DENYLIST_ID") or None


def _key(user_id: str) -> str:
    return f"u:{user_id}"


async def add(user_id: str, *, cf: Optional[cloudflare.CloudflareClient] = None) -> bool:
    """Deny a user at the edge, immediately. True on success.

    False means the edge is still admitting them until their current JWT expires
    — the caller should surface that, not swallow it.
    """
    ns = namespace_id()
    if not ns:
        log.warning("[denylist] CLOUDFLARE_KV_DENYLIST_ID not set — user %s is suspended in "
                    "Firestore but NOT cut off at the edge until their JWT expires", user_id)
        return False
    client = cf or _client()
    try:
        await client.kv_put(namespace_id=ns, key=_key(user_id), value="1")
        log.info("[denylist] added %s", user_id)
        return True
    except Exception as e:
        log.error("[denylist] FAILED to add %s: %s — edge cutoff is degraded to one JWT TTL",
                  user_id, e)
        return False


async def remove(user_id: str, *, cf: Optional[cloudflare.CloudflareClient] = None) -> bool:
    """Un-deny. A failure here is the safe direction (they stay blocked); still
    report it so an un-suspension doesn't silently fail to take effect."""
    ns = namespace_id()
    if not ns:
        return False
    client = cf or _client()
    try:
        await client.kv_delete(namespace_id=ns, key=_key(user_id))
        log.info("[denylist] removed %s", user_id)
        return True
    except Exception as e:
        log.error("[denylist] failed to remove %s: %s", user_id, e)
        return False


def _client() -> cloudflare.CloudflareClient:
    # Late import to reuse the same singleton (and the same test injection) the
    # route modules use, rather than opening a second CF client.
    from .routes_account import _get_cf_client
    return _get_cf_client()
