"""The entitlement predicate. One function, two callers.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

`is_entitled()` is the single source of truth for "may this user have remote
access right now". Per `clayrune-cloud/docs/BILLING_DESIGN.md` §3 it is enforced
at exactly **two** chokepoints and nowhere else:

  1. **JWT mint / refresh** (`app/routes_auth.py`) — the browser + phone path.
     The JWT TTL *is* the revocation lag: a cancellation takes effect within one
     TTL. That is fine for billing.
  2. **`POST /v1/attest`** (`app/routes_attest.py`) — the tunnel path. The
     attestation loop already runs every 10 minutes; it is already the heartbeat.

Resist adding a third. Every extra chokepoint is another place the predicate can
drift out of agreement with itself.

## Fail open on billing, fail closed on identity

These are different failures and they get opposite defaults.

A broken *billing* pipeline (our webhook died, the provider is down, `sub_status`
is stale) must never kill a paying customer — hence the `past_due` grace window,
and hence `CLAYRUNE_BILLING_ENFORCED` defaulting to off. An unverifiable
*identity* fails closed, and that is handled elsewhere: the Worker 503s when it
cannot reach the JWKS, and we refuse to mint a token for a user we cannot
resolve.

## CLAYRUNE_BILLING_ENFORCED

Billing does not exist yet — no `sub_status`, no `plan`, no `current_period_end`
on any user row. Enforcing the predicate literally today would lock out every
existing user, including the ones who are enrolled and working. So while the flag
is off (the default), entitlement means "not suspended". `suspended` is honoured
either way, because suspension is an abuse control, not a billing state.

Flip `CLAYRUNE_BILLING_ENFORCED=1` when the billing webhook is populating
`sub_status` / `current_period_end` for real.
"""
from __future__ import annotations

import datetime as _dt
import os
from typing import Any, Optional


def billing_enforced() -> bool:
    # Read at call time, not import time: tests and the Settings surface flip it.
    return os.environ.get("CLAYRUNE_BILLING_ENFORCED", "0") == "1"


def _as_datetime(v: Any) -> Optional[_dt.datetime]:
    """Firestore timestamps come back as datetime; tolerate ISO strings + None."""
    if v is None:
        return None
    if isinstance(v, _dt.datetime):
        return v if v.tzinfo else v.replace(tzinfo=_dt.timezone.utc)
    if isinstance(v, str):
        try:
            parsed = _dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=_dt.timezone.utc)
    return None


def plan_of(user_row: dict) -> str:
    """`local` / `connect` / `cloud`. Absent → `connect` (the shipping default)."""
    return (user_row or {}).get("plan") or "connect"


def is_entitled(user_row: dict, now: Optional[_dt.datetime] = None) -> bool:
    """May this user reach their dashboard / keep their tunnel alive?

    Mirrors BILLING_DESIGN.md §3 exactly, with the pre-billing switch on top.
    """
    u = user_row or {}
    now = now or _dt.datetime.now(_dt.timezone.utc)

    # Abuse flag wins over everything, enforced or not. This is the same state
    # the KV denylist mirrors to the edge for immediate cutoff.
    if u.get("suspended"):
        return False

    if not billing_enforced():
        return True

    if plan_of(u) == "local":
        return False  # local tier has no remote access at all

    status = u.get("sub_status") or "none"
    if status in ("trialing", "active"):
        period_end = _as_datetime(u.get("current_period_end"))
        return period_end is not None and now < period_end
    if status == "past_due":
        # Fail OPEN through dunning — never kill a paying customer because a
        # payment retry is in flight.
        grace = _as_datetime(u.get("grace_until"))
        return grace is not None and now < grace
    return False
