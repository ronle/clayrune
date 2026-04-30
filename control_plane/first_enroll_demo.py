"""First real enrollment demo.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

Drives a single end-to-end /v1/enroll against REAL Cloudflare + REAL Firestore.
This is the milestone validation: if it succeeds, the entire client/CP/CF
chain works.

Usage from PowerShell:

    $env:CLOUDFLARE_API_TOKEN = $token
    $env:FIRESTORE_PROJECT     = "clayrune"
    $env:FIRESTORE_DATABASE    = "default"
    $env:MC_CP_DEV_AUTH        = "1"

    python -m control_plane.first_enroll_demo --username ron --email <your-email>

Side effects (after a successful run):
    - 1 user row in Firestore (`users/<user_id>`)
    - 1 device row in Firestore (`devices/<device_id>`)
    - 1 username claim (`usernames/<username>`)
    - 1 Cloudflare tunnel
    - 1 Cloudflare DNS CNAME (<username>.clayrune.io)
    - 1 Cloudflare Access app gating that hostname to <email>

To clean these up:

    python -m control_plane.first_enroll_demo --cleanup --username ron

(uses /v1/devices/{id}/revoke once that's wired; today it deletes via the
CF API directly + clears the Firestore rows).
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
from pathlib import Path

# Make the package importable when running as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


REQUIRED_ENV = ("CLOUDFLARE_API_TOKEN", "FIRESTORE_PROJECT", "FIRESTORE_DATABASE")


def _check_env() -> None:
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        print(f"ERROR: missing env vars: {missing}", flush=True)
        sys.exit(1)
    if os.environ.get("MC_CP_DEV_AUTH") != "1":
        print("ERROR: set MC_CP_DEV_AUTH=1 to use the dev auth shim", flush=True)
        sys.exit(1)


def _phase_connectivity() -> tuple[str, str]:
    """Verify CF token is active. Return (account_id, zone_id).

    Single asyncio.run() call — multiple runs against the same httpx
    AsyncClient cause "Event loop is closed" on Python 3.14 / Windows.
    """
    print("=" * 64)
    print("Phase 1 — Cloudflare connectivity")
    print("=" * 64)
    from control_plane.app import cloudflare

    async def _run():
        cf = cloudflare.CloudflareClient.from_env()
        try:
            info = await cf.verify_token()
            account_id = await cf.get_account_id()
            zone_id = await cf.get_zone_id()
            return info, account_id, zone_id
        finally:
            await cf.aclose()

    info, account_id, zone_id = asyncio.run(_run())
    print(f"  ✓ CF token active: id={info.get('id')} status={info.get('status')}")
    print(f"  ✓ account_id: {account_id}")
    print(f"  ✓ zone_id:    {zone_id}  (clayrune.io)")
    return account_id, zone_id


def _phase_firestore() -> None:
    """Verify Firestore is reachable + seeded."""
    print()
    print("=" * 64)
    print("Phase 2 — Firestore connectivity + seed verification")
    print("=" * 64)
    from control_plane.app import firestore as fs

    versions = list(fs.db().collection(fs.COL_VERSIONS).stream())
    if not versions:
        print("  ✗ no rows in versions/ — run `python -m control_plane.seed` first")
        sys.exit(1)
    print(f"  ✓ versions/ has {len(versions)} row(s):")
    for v in versions:
        print(f"      {v.id}")

    keys = list(fs.db().collection(fs.COL_CLIENT_KEYS).stream())
    if not keys:
        print("  ✗ no rows in client_secret_keys/ — run `python -m control_plane.seed` first")
        sys.exit(1)
    print(f"  ✓ client_secret_keys/ has {len(keys)} row(s):")
    for k in keys:
        print(f"      {k.id}")


def _phase_enroll(*, username: str, email: str) -> dict:
    """Drive a real /v1/enroll. Returns the response dict."""
    print()
    print("=" * 64)
    print(f"Phase 3 — POST /v1/enroll  (username={username!r}, email={email!r})")
    print("=" * 64)
    print("  Provisioning Cloudflare tunnel + DNS + Access app...")

    from fastapi.testclient import TestClient
    from control_plane.app.main import app

    client = TestClient(app)

    # The "device_pub" here is a placeholder for this demo — the real one
    # is generated client-side by MC's keystore. /v1/enroll doesn't verify
    # it cryptographically (only /v1/attest does), so any 32-byte b64
    # string works for proving the enrollment plumbing.
    pub = base64.b64encode(b"\x10" * 32).decode("ascii")

    r = client.post(
        "/v1/enroll",
        headers={"X-Dev-User-Email": email},
        json={
            "device_pub_b64": pub,
            "csrf_nonce": "first-enroll-demo",
            "username": username,
            "device_name": "First-enroll demo device",
            "os": "win32-demo",
            "mc_version": "1.4.2",
        },
        timeout=60.0,
    )
    body = r.json()

    if r.status_code != 200:
        print()
        print(f"  ✗ /v1/enroll FAILED: HTTP {r.status_code}")
        print(json.dumps(body, indent=2))
        sys.exit(1)

    print(f"  ✓ /v1/enroll succeeded (HTTP {r.status_code})")
    print(f"      device_id: {body['device_id']}")
    print(f"      username : {body['username']}")
    print(f"      hostname : {body['hostname']}")
    print(f"      enrollment_token: {body['enrollment_token'][:24]}... (kept secret; only shown once)")
    return body


def _show_cf_dashboard_links() -> None:
    print()
    print("=" * 64)
    print("Phase 4 — what to verify in the Cloudflare dashboard")
    print("=" * 64)
    print("  1. Tunnel:     https://one.dash.cloudflare.com/  →  Networks → Tunnels")
    print("                 (look for one named `mc-<username>-...`)")
    print("  2. DNS:        https://dash.cloudflare.com/  →  clayrune.io → DNS → Records")
    print("                 (look for a CNAME `<username>` pointing to `<uuid>.cfargotunnel.com`)")
    print("  3. Access app: https://one.dash.cloudflare.com/  →  Access → Applications")
    print("                 (look for `Mission Control - <username>.clayrune.io`)")


async def _phase_cleanup(*, username: str) -> None:
    """Delete the CF resources + Firestore rows for `username`."""
    print()
    print("=" * 64)
    print(f"Cleanup — deleting CF resources + Firestore rows for username={username!r}")
    print("=" * 64)

    from control_plane.app import cloudflare, firestore as fs

    # Find the device row by hostname (most reliable lookup for this script)
    hostname = f"{username}.{os.environ.get('CLAYRUNE_PRIMARY_ZONE', 'clayrune.io')}"
    docs = list(fs.db().collection(fs.COL_DEVICES)
                .where("hostname_claim", "==", hostname).stream())
    if not docs:
        print(f"  no device row found for hostname={hostname!r} — nothing to clean up")
        return

    cf = cloudflare.CloudflareClient.from_env()
    try:
        for d in docs:
            row = d.to_dict() or {}
            print(f"  device {d.id}:")
            for label, key, fn in (
                ("Access app", "cf_access_app_id", cf.delete_access_app),
                ("DNS record", "cf_dns_record_id", cf.delete_dns_record),
                ("Tunnel",     "cf_tunnel_uuid",   cf.delete_tunnel),
            ):
                rid = row.get(key)
                if not rid:
                    continue
                try:
                    await fn(rid)
                    print(f"    ✓ deleted {label}: {rid}")
                except Exception as e:
                    print(f"    ✗ failed to delete {label} {rid}: {e}")

            # Delete Firestore rows
            fs.db().collection(fs.COL_DEVICES).document(d.id).delete()
            print(f"    ✓ deleted devices/{d.id}")

            user_id = row.get("user_id")
            if user_id:
                # Don't delete the user — they may still own other devices.
                # Just release the username claim.
                pass

        # Release the username claim
        try:
            fs.db().collection("usernames").document(username).delete()
            print(f"    ✓ released username claim: {username}")
        except Exception as e:
            print(f"    ✗ failed to release username: {e}")
    finally:
        await cf.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Drive a first end-to-end /v1/enroll against real Cloudflare + Firestore.")
    parser.add_argument("--username", required=True, help="Username to enroll (lowercase, 3–24 chars)")
    parser.add_argument("--email", help="Email to gate Cloudflare Access on (required for enroll)")
    parser.add_argument("--cleanup", action="store_true",
                        help="Delete the CF resources + Firestore rows for --username (no enroll)")
    args = parser.parse_args()

    _check_env()

    if args.cleanup:
        asyncio.run(_phase_cleanup(username=args.username))
        return 0

    if not args.email:
        print("ERROR: --email required when not using --cleanup", flush=True)
        return 1

    _phase_connectivity()
    _phase_firestore()
    body = _phase_enroll(username=args.username, email=args.email)
    _show_cf_dashboard_links()

    print()
    print("=" * 64)
    print("✓ FIRST REAL ENROLLMENT COMPLETE")
    print("=" * 64)
    print(f"  Save this enrollment_token (only shown once): {body['enrollment_token']}")
    print(f"  Hostname: https://{body['hostname']}")
    print()
    print("  Next: run cloudflared with the issued tunnel token to bring the")
    print("        tunnel online, then visit the hostname from your phone.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
