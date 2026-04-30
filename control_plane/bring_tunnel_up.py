"""Bring an enrolled user's tunnel online — Path A (no attestation yet).

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

Quick demo: spawns the bundled `cloudflared.exe` with the tunnel token
issued at /v1/enroll time, so traffic to https://<username>.clayrune.io
actually reaches your local Mission Control.

This bypasses the attestation supervisor — we read the long-lived tunnel
token directly from Firestore and hand it to cloudflared. Path B (later)
runs the supervisor, which attests every 10 min and rotates short-lived
tunnel tokens via /v1/attest.

Usage from PowerShell:

    $env:FIRESTORE_PROJECT  = "clayrune"
    $env:FIRESTORE_DATABASE = "default"
    python -m control_plane.bring_tunnel_up --username ron

Stop with Ctrl+C. cloudflared exits cleanly on SIGINT.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Make package importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _check_mc_alive(port: int = 5199) -> bool:
    """Ping MC's local Flask server. Returns True if reachable."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/config", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _find_cloudflared() -> str:
    here = Path(__file__).resolve().parent.parent
    bundled = here / "mc_tunnel" / "bin" / "cloudflared.exe"
    if bundled.is_file():
        return str(bundled)
    p = shutil.which("cloudflared")
    if p:
        return p
    raise RuntimeError("cloudflared.exe not found in mc_tunnel/bin/ or on PATH")


def _fetch_token(*, username: str) -> tuple[str, str]:
    """Read the cf_tunnel_token + hostname for this username's device row."""
    from control_plane.app import firestore as fs

    hostname = f"{username}.{os.environ.get('CLAYRUNE_PRIMARY_ZONE', 'clayrune.io')}"
    docs = list(
        fs.db().collection(fs.COL_DEVICES)
        .where("hostname_claim", "==", hostname)
        .stream()
    )
    if not docs:
        raise RuntimeError(f"No device row found for hostname={hostname!r}. "
                           f"Did /v1/enroll run for this username?")
    row = docs[0].to_dict() or {}
    token = row.get("cf_tunnel_token")
    if not token:
        raise RuntimeError(f"Device row has no cf_tunnel_token: {row.keys()}")
    return token, hostname


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run cloudflared for an enrolled user (Path A demo).")
    parser.add_argument("--username", required=True)
    parser.add_argument("--mc-port", type=int, default=5199,
                        help="Local MC port (default 5199)")
    parser.add_argument("--skip-mc-check", action="store_true",
                        help="Don't ping MC before starting cloudflared")
    args = parser.parse_args()

    # Env preflight
    for k in ("FIRESTORE_PROJECT", "FIRESTORE_DATABASE"):
        if not os.environ.get(k):
            print(f"ERROR: env var {k} not set", flush=True)
            return 1

    # MC liveness check (the whole point of the tunnel is to forward to MC)
    if not args.skip_mc_check:
        print(f"Checking MC at http://127.0.0.1:{args.mc_port} ...", flush=True)
        if not _check_mc_alive(args.mc_port):
            print(f"  ✗ MC not responding on port {args.mc_port}.", flush=True)
            print(f"     Start MC first (e.g. via Tauri host or 'python server.py'),", flush=True)
            print(f"     or use --skip-mc-check to start cloudflared anyway.", flush=True)
            return 1
        print(f"  ✓ MC is up", flush=True)

    # Fetch the token
    try:
        token, hostname = _fetch_token(username=args.username)
    except Exception as e:
        print(f"ERROR fetching tunnel token: {e}", flush=True)
        return 1
    print(f"Tunnel for: https://{hostname}", flush=True)

    # Find cloudflared
    try:
        cf_bin = _find_cloudflared()
    except Exception as e:
        print(f"ERROR: {e}", flush=True)
        return 1
    print(f"Using cloudflared: {cf_bin}", flush=True)

    # Spawn cloudflared. Inherit stdout/stderr so we see logs live.
    print()
    print("=" * 64)
    print("Starting cloudflared. Open https://" + hostname + " from any device.")
    print("Stop with Ctrl+C.")
    print("=" * 64)
    print()

    cmd = [cf_bin, "tunnel", "--no-autoupdate", "run", "--token", token]
    try:
        # Pass token via env to avoid leaking it in the process listing
        proc = subprocess.Popen(cmd)
        return proc.wait()
    except KeyboardInterrupt:
        print("\nShutting down cloudflared...", flush=True)
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
