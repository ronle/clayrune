"""Admin tool: wipe all CF + Firestore state for a given username.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

Use when:
  - You need to fully reset a username for re-enrollment in tests.
  - Self-healing enrollment is failing (e.g. Firestore + CF state are
    inconsistent in a way the per-request collision retries can't recover from).
  - You're cleaning up after a development session and want a known-clean state.

Usage:

    # Against deployed Cloud Run + production CF account:
    $env:CLOUDFLARE_API_TOKEN = (gcloud secrets versions access latest --secret=cloudflare-api-token --project=clayrune)
    $env:GOOGLE_APPLICATION_CREDENTIALS = "$HOME/.config/gcloud/application_default_credentials.json"
    $env:FIRESTORE_PROJECT = "clayrune"
    $env:FIRESTORE_DATABASE = "default"
    python -m control_plane.force_cleanup --username ron

    # Dry-run (lists what would be deleted, no actual deletes):
    python -m control_plane.force_cleanup --username ron --dry-run

    # Skip the username-claim release (rare; useful if you want to keep
    # the username reserved while wiping device state):
    python -m control_plane.force_cleanup --username ron --keep-username

What it does (in order):
  1. Lists + deletes CF Access apps whose domain matches `<user>.clayrune.io`
  2. Lists + deletes CF DNS records for that hostname (filter by name=)
  3. Lists + deletes CF tunnels named `mc-<user>-*` (cleans up connections first)
  4. Deletes Firestore device rows where `hostname_claim == <user>.clayrune.io`
  5. Releases the `usernames/<user>` claim (unless --keep-username)

Idempotent: safe to re-run on already-clean state.
Best-effort: continues on individual delete failures, prints a summary at end.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any


def _print(prefix: str, msg: str) -> None:
    sys.stdout.write(f"[force_cleanup] {prefix} {msg}\n")
    sys.stdout.flush()


async def _run(username: str, *, zone_root: str, dry_run: bool, keep_username: bool) -> int:
    """Returns exit code: 0 = clean, non-zero on hard failure."""
    hostname = f"{username}.{zone_root}"
    summary = {"access_apps": 0, "dns_records": 0, "tunnels": 0, "devices": 0,
               "usernames": 0, "errors": 0}

    # Lazy imports — let argparse fail fast on bad args before pulling deps.
    try:
        from control_plane.app import cloudflare, firestore as fs
    except Exception as e:
        _print("ERROR", f"failed to import control_plane.app: {e}")
        return 2

    cf = cloudflare.CloudflareClient(token=os.environ.get("CLOUDFLARE_API_TOKEN", ""))

    # 1. Access apps
    try:
        acc = await cf.get_account_id()
        apps = await cf._call("GET", f"/accounts/{acc}/access/apps") or []
        for app in apps:
            if app.get("domain", "").lower() != hostname.lower():
                continue
            _print("ACCESS_APP", f"{'WOULD delete' if dry_run else 'deleting'} {app['id']} (name={app.get('name','')})")
            if not dry_run:
                try:
                    await cf.delete_access_app(app["id"])
                    summary["access_apps"] += 1
                except Exception as e:
                    summary["errors"] += 1
                    _print("ERROR", f"deleting access app {app['id']}: {e}")
    except Exception as e:
        summary["errors"] += 1
        _print("ERROR", f"listing access apps: {e}")

    # 2. DNS records (server-side filter by name)
    try:
        zone_id = await cf.get_zone_id()
        records = await cf._call("GET", f"/zones/{zone_id}/dns_records",
                                 params={"name": hostname}) or []
        for r in records:
            _print("DNS", f"{'WOULD delete' if dry_run else 'deleting'} {r['id']} type={r.get('type')} content={r.get('content','')[:48]}")
            if not dry_run:
                try:
                    await cf.delete_dns_record(r["id"])
                    summary["dns_records"] += 1
                except Exception as e:
                    summary["errors"] += 1
                    _print("ERROR", f"deleting DNS record {r['id']}: {e}")
    except Exception as e:
        summary["errors"] += 1
        _print("ERROR", f"listing DNS records: {e}")

    # 3. Tunnels named mc-<username>-*
    try:
        tunnels = await cf._call("GET", f"/accounts/{acc}/cfd_tunnel") or []
        prefix = f"mc-{username}-"
        for t in tunnels:
            if t.get("deleted_at"):
                continue
            if not (t.get("name") or "").startswith(prefix):
                continue
            _print("TUNNEL", f"{'WOULD delete' if dry_run else 'deleting'} {t['id']} (name={t.get('name')})")
            if not dry_run:
                try:
                    await cf.delete_tunnel(t["id"])
                    summary["tunnels"] += 1
                except Exception as e:
                    summary["errors"] += 1
                    _print("ERROR", f"deleting tunnel {t['id']}: {e}")
    except Exception as e:
        summary["errors"] += 1
        _print("ERROR", f"listing tunnels: {e}")

    # 4. Firestore device rows
    try:
        db = fs.db()
        docs = list(db.collection(fs.COL_DEVICES)
                      .where("hostname_claim", "==", hostname).stream())
        for d in docs:
            _print("DEVICE", f"{'WOULD delete' if dry_run else 'deleting'} devices/{d.id}")
            if not dry_run:
                try:
                    db.collection(fs.COL_DEVICES).document(d.id).delete()
                    summary["devices"] += 1
                except Exception as e:
                    summary["errors"] += 1
                    _print("ERROR", f"deleting devices/{d.id}: {e}")
    except Exception as e:
        summary["errors"] += 1
        _print("ERROR", f"listing device rows: {e}")

    # 5. Release the username claim
    if not keep_username:
        try:
            ref = fs.db().collection("usernames").document(username)
            snap = ref.get()
            if snap.exists:
                _print("USERNAME", f"{'WOULD release' if dry_run else 'releasing'} usernames/{username}")
                if not dry_run:
                    try:
                        ref.delete()
                        summary["usernames"] += 1
                    except Exception as e:
                        summary["errors"] += 1
                        _print("ERROR", f"deleting usernames/{username}: {e}")
            else:
                _print("USERNAME", f"usernames/{username} already absent")
        except Exception as e:
            summary["errors"] += 1
            _print("ERROR", f"reading usernames/{username}: {e}")

    _print("SUMMARY", f"{'(dry-run) ' if dry_run else ''}{summary}")
    return 1 if summary["errors"] else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m control_plane.force_cleanup",
                                description="Wipe all CF + Firestore state for a username.")
    p.add_argument("--username", required=True, help="Lowercase username to wipe (e.g. 'ron')")
    p.add_argument("--zone-root", default=os.environ.get("CLAYRUNE_PRIMARY_ZONE", "clayrune.io"),
                   help="Primary zone root (default: clayrune.io)")
    p.add_argument("--dry-run", action="store_true",
                   help="List what would be deleted; perform no actual deletes.")
    p.add_argument("--keep-username", action="store_true",
                   help="Don't release the usernames/<user> claim.")
    args = p.parse_args(argv)

    if not os.environ.get("CLOUDFLARE_API_TOKEN"):
        _print("ERROR", "CLOUDFLARE_API_TOKEN env var is required.")
        return 2
    if not os.environ.get("FIRESTORE_PROJECT"):
        _print("ERROR", "FIRESTORE_PROJECT env var is required (e.g. 'clayrune').")
        return 2

    return asyncio.run(_run(args.username, zone_root=args.zone_root,
                            dry_run=args.dry_run, keep_username=args.keep_username))


if __name__ == "__main__":
    sys.exit(main())
