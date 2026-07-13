#!/usr/bin/env python3
"""One-shot: tear down every Cloudflare Access app + service token we created.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

Enrollment no longer provisions Cloudflare Access (see `app/routes_account.py`),
but the apps and service tokens we already created for existing users are still
sitting in the account — still counting toward the 500-application cap, and still
billable the moment the account crosses 50 seats. This deletes them.

**Run this only after the edge Worker is deployed and verified.** The Access apps
are, right now, the only thing standing between the public internet and every
enrolled user's dev machine. Deleting them before the Worker enforces
`claims.u === subdomain` publishes everyone's box. Order:

    1. Deploy the Worker (`clayrune-cloud/edge-worker`), with the DENYLIST binding.
    2. Confirm: a signed-in user reaches their own subdomain; a signed-in user gets
       403 on someone else's; a signed-out visitor gets bounced to sign-in.
    3. Then run this.

Usage:

    python control_plane/teardown_access.py              # dry run — lists, deletes nothing
    python control_plane/teardown_access.py --apply      # actually delete

Env: CLOUDFLARE_API_TOKEN (+ optional CLOUDFLARE_ACCOUNT_ID), CLAYRUNE_PRIMARY_ZONE.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from control_plane.app import cloudflare  # noqa: E402


async def main(apply: bool) -> int:
    zone = os.environ.get("CLAYRUNE_PRIMARY_ZONE", "clayrune.io").lower()
    cf = cloudflare.CloudflareClient.from_env()
    acc = await cf.get_account_id()

    mode = "DELETING" if apply else "DRY RUN (nothing will be deleted)"
    print(f"account={acc} zone={zone} — {mode}\n")

    # ── Access applications ──────────────────────────────────────────────
    apps = await cf._call("GET", f"/accounts/{acc}/access/apps") or []
    ours = [a for a in apps if (a.get("domain") or "").lower().endswith("." + zone)]
    print(f"Access applications on *.{zone}: {len(ours)} (of {len(apps)} total)")
    deleted_apps = 0
    for a in ours:
        print(f"  - {a.get('domain')}  ({a.get('id')})")
        if apply:
            try:
                await cf.delete_access_app(a["id"])
                deleted_apps += 1
            except Exception as e:
                print(f"    !! delete failed: {e}")

    # ── Service tokens (the old mobile-pairing credential) ───────────────
    tokens = await cf._call("GET", f"/accounts/{acc}/access/service_tokens") or []
    ours_t = [t for t in tokens if (t.get("name") or "").startswith("clayrune-")]
    print(f"\nService tokens named clayrune-*: {len(ours_t)} (of {len(tokens)} total)")
    deleted_tokens = 0
    for t in ours_t:
        print(f"  - {t.get('name')}  ({t.get('id')})")
        if apply:
            try:
                await cf.delete_service_token(t["id"])
                deleted_tokens += 1
            except Exception as e:
                print(f"    !! delete failed: {e}")

    await cf.aclose()

    if apply:
        print(f"\nDeleted {deleted_apps} Access apps, {deleted_tokens} service tokens.")
        print("Paired phones are now dead until the Android shell ships the "
              "refresh-token flow. Tell users to re-pair.")
    else:
        print("\nDry run. Re-run with --apply to delete.")
        print("DO NOT run --apply until the edge Worker is deployed and verified — "
              "these Access apps are currently the only authorization in the path.")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true",
                   help="actually delete (default: dry run)")
    args = p.parse_args()
    raise SystemExit(asyncio.run(main(args.apply)))
