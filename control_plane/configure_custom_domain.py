"""Wire up a custom domain (e.g. api.clayrune.io) to a Cloud Run service.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

Adds two CF resources, idempotently:
  1. Proxied CNAME `<name>.<zone>` -> `<run-app-host>`
  2. An entry in the zone's `http_request_origin` ruleset that rewrites the
     Host header AND origin SNI to `<run-app-host>` for requests where
     `http.host` matches the custom domain. This is required because Cloud
     Run validates the incoming Host header against its `*.run.app` domain.

Usage:

    $env:CLOUDFLARE_API_TOKEN = (gcloud secrets versions access latest --secret=cloudflare-api-token --project=clayrune)
    python -m control_plane.configure_custom_domain \
        --hostname api.clayrune.io \
        --target control-plane-189381911926.us-central1.run.app

    # Dry-run (no changes):
    python -m control_plane.configure_custom_domain --hostname api.clayrune.io --target ...run.app --dry-run

    # Tear-down:
    python -m control_plane.configure_custom_domain --hostname api.clayrune.io --target ...run.app --remove

Token requirements:
  - Zone:DNS:Edit (already had this)
  - Zone:Config Rules:Edit (added 2026-04-30)

After successful run, point MC at the new domain via:
    $env:MC_REMOTE_CP_OVERRIDE = "https://api.clayrune.io/v1"
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any, Optional


_RULE_DESCRIPTION = "mission-control: cloud-run host header override"


def _print(prefix: str, msg: str) -> None:
    sys.stdout.write(f"[configure_custom_domain] {prefix} {msg}\n")
    sys.stdout.flush()


async def _ensure_dns(cf, *, name_only: str, zone_apex: str, target: str,
                      dry_run: bool) -> dict:
    """Create or update a proxied CNAME from `<name_only>.<zone_apex>` -> `target`."""
    fqdn = f"{name_only}.{zone_apex}"
    zone_id = await cf.get_zone_id()
    existing = await cf._call("GET", f"/zones/{zone_id}/dns_records",
                              params={"name": fqdn}) or []

    if existing:
        rec = existing[0]
        if (rec.get("type") == "CNAME" and rec.get("content") == target
                and rec.get("proxied") is True):
            _print("DNS", f"already correct: {fqdn} CNAME -> {target} (proxied)")
            return rec
        _print("DNS", f"{'WOULD update' if dry_run else 'updating'} {fqdn}: "
                      f"type={rec.get('type')} content={rec.get('content')} -> CNAME {target}")
        if not dry_run:
            return await cf._call(
                "PUT", f"/zones/{zone_id}/dns_records/{rec['id']}",
                json={"type": "CNAME", "name": fqdn, "content": target,
                      "proxied": True, "ttl": 1},
            )
        return rec

    _print("DNS", f"{'WOULD create' if dry_run else 'creating'} {fqdn} CNAME -> {target} (proxied)")
    if dry_run:
        return {"_dry_run": True}
    return await cf._call(
        "POST", f"/zones/{zone_id}/dns_records",
        json={"type": "CNAME", "name": fqdn, "content": target,
              "proxied": True, "ttl": 1},
    )


async def _delete_dns(cf, *, name_only: str, zone_apex: str, dry_run: bool) -> None:
    fqdn = f"{name_only}.{zone_apex}"
    zone_id = await cf.get_zone_id()
    existing = await cf._call("GET", f"/zones/{zone_id}/dns_records",
                              params={"name": fqdn}) or []
    for rec in existing:
        _print("DNS", f"{'WOULD delete' if dry_run else 'deleting'} {fqdn} (id={rec['id']})")
        if not dry_run:
            await cf._call("DELETE", f"/zones/{zone_id}/dns_records/{rec['id']}")


def _build_rule(fqdn: str, target: str) -> dict:
    """Build a `route` rule that rewrites Host header + SNI for `fqdn`."""
    return {
        "description": _RULE_DESCRIPTION,
        "expression": f'(http.host eq "{fqdn}")',
        "action": "route",
        "action_parameters": {
            "host_header": target,
            "origin": {"host": target},
            # SNI defaults to the CNAME target which IS our run.app, but be
            # explicit so it's stable even if CF behavior changes:
            "sni": {"value": target},
        },
        "enabled": True,
    }


async def _ensure_origin_rule(cf, *, fqdn: str, target: str, dry_run: bool) -> None:
    """Add or update the host-rewrite rule on the zone's origin entrypoint ruleset.

    Uses the entrypoint endpoint `/zones/{zone}/rulesets/phases/http_request_origin/entrypoint`
    so we don't have to manage ruleset IDs ourselves.
    """
    zone_id = await cf.get_zone_id()
    path = f"/zones/{zone_id}/rulesets/phases/http_request_origin/entrypoint"
    desired = _build_rule(fqdn, target)

    existing = None
    try:
        existing = await cf._call("GET", path)
    except Exception as e:
        # The entrypoint may not exist yet — that's fine, we'll create it via PUT.
        _print("RULE", f"entrypoint not found ({e}); will create")
        existing = None

    rules = list((existing or {}).get("rules") or [])
    # Find any rule with our description marker (or matching expression)
    keep: list[dict] = []
    found_idx = -1
    for i, r in enumerate(rules):
        if (r.get("description") == _RULE_DESCRIPTION
                or r.get("expression") == desired["expression"]):
            found_idx = i
            continue
        keep.append(r)

    new_rules = keep + [desired]

    if found_idx >= 0 and rules[found_idx].get("expression") == desired["expression"] \
            and rules[found_idx].get("action_parameters", {}).get("host_header") == target \
            and rules[found_idx].get("action_parameters", {}).get("origin", {}).get("host") == target:
        _print("RULE", f"already correct: host eq \"{fqdn}\" -> host_header={target}")
        return

    _print("RULE", f"{'WOULD upsert' if dry_run else 'upserting'} origin rule: "
                   f"host eq \"{fqdn}\" -> host_header={target}, origin.host={target}")
    if dry_run:
        return

    body = {
        "name": (existing or {}).get("name") or "default",
        "rules": new_rules,
    }
    await cf._call("PUT", path, json=body)


async def _delete_origin_rule(cf, *, dry_run: bool) -> None:
    zone_id = await cf.get_zone_id()
    path = f"/zones/{zone_id}/rulesets/phases/http_request_origin/entrypoint"
    try:
        existing = await cf._call("GET", path)
    except Exception:
        _print("RULE", "no origin entrypoint ruleset to clean")
        return
    rules = list((existing or {}).get("rules") or [])
    kept = [r for r in rules if r.get("description") != _RULE_DESCRIPTION]
    if len(kept) == len(rules):
        _print("RULE", "no rule with our description marker; nothing to remove")
        return
    _print("RULE", f"{'WOULD remove' if dry_run else 'removing'} {len(rules) - len(kept)} rule(s) tagged '{_RULE_DESCRIPTION}'")
    if dry_run:
        return
    body = {"name": existing.get("name") or "default", "rules": kept}
    await cf._call("PUT", path, json=body)


async def _smoke_test(fqdn: str) -> int:
    """Hit https://<fqdn>/v1/health and report status."""
    try:
        import requests
    except Exception:
        _print("SMOKE", "skipping (requests not installed)")
        return 0
    url = f"https://{fqdn}/v1/health"
    try:
        r = requests.get(url, timeout=15)
        _print("SMOKE", f"GET {url} -> {r.status_code} ({r.text[:120]})")
        return 0 if r.status_code == 200 else 1
    except Exception as e:
        _print("SMOKE", f"GET {url} failed: {e}")
        return 1


async def _run(*, hostname: str, target: str, dry_run: bool, remove: bool,
               skip_smoke: bool) -> int:
    try:
        from control_plane.app import cloudflare
    except Exception as e:
        _print("ERROR", f"failed to import cloudflare module: {e}")
        return 2

    cf = cloudflare.CloudflareClient(token=os.environ.get("CLOUDFLARE_API_TOKEN", ""))

    # Split hostname into subdomain + zone apex.
    parts = hostname.split(".")
    if len(parts) < 3:
        _print("ERROR", f"hostname must be <sub>.<zone>.<tld> (got: {hostname})")
        return 2
    name_only = parts[0]
    zone_apex = ".".join(parts[1:])

    # Sanity: zone in env should match.
    expected_zone = os.environ.get("CLAYRUNE_PRIMARY_ZONE", "clayrune.io")
    if zone_apex != expected_zone:
        _print("WARN", f"hostname zone '{zone_apex}' != CLAYRUNE_PRIMARY_ZONE '{expected_zone}'")

    if remove:
        try:
            await _delete_origin_rule(cf, dry_run=dry_run)
        except Exception as e:
            _print("ERROR", f"removing origin rule: {e}")
            return 1
        try:
            await _delete_dns(cf, name_only=name_only, zone_apex=zone_apex, dry_run=dry_run)
        except Exception as e:
            _print("ERROR", f"deleting DNS: {e}")
            return 1
        _print("DONE", f"removed custom domain {hostname}{' (dry-run)' if dry_run else ''}")
        return 0

    try:
        await _ensure_dns(cf, name_only=name_only, zone_apex=zone_apex,
                          target=target, dry_run=dry_run)
    except Exception as e:
        _print("ERROR", f"ensuring DNS: {e}")
        return 1
    try:
        await _ensure_origin_rule(cf, fqdn=hostname, target=target, dry_run=dry_run)
    except Exception as e:
        _print("ERROR", f"ensuring origin rule: {e}")
        return 1

    if dry_run:
        _print("DONE", f"dry-run for {hostname} -> {target} (no changes made)")
        return 0

    _print("DONE", f"configured {hostname} -> {target}")

    if skip_smoke:
        return 0

    # CF DNS + Origin Rule changes propagate within seconds for proxied records.
    _print("SMOKE", "waiting 5s for propagation, then testing...")
    await asyncio.sleep(5)
    return await _smoke_test(hostname)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m control_plane.configure_custom_domain")
    p.add_argument("--hostname", required=True, help="Custom domain, e.g. api.clayrune.io")
    p.add_argument("--target", required=True,
                   help="Cloud Run domain, e.g. control-plane-189381911926.us-central1.run.app")
    p.add_argument("--dry-run", action="store_true", help="Plan only; make no changes.")
    p.add_argument("--remove", action="store_true", help="Tear down the domain config.")
    p.add_argument("--skip-smoke", action="store_true", help="Skip the post-change HTTP probe.")
    args = p.parse_args(argv)

    if not os.environ.get("CLOUDFLARE_API_TOKEN"):
        _print("ERROR", "CLOUDFLARE_API_TOKEN env var is required.")
        return 2

    return asyncio.run(_run(hostname=args.hostname, target=args.target,
                            dry_run=args.dry_run, remove=args.remove,
                            skip_smoke=args.skip_smoke))


if __name__ == "__main__":
    sys.exit(main())
