"""Tests for /v1/enroll.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

Run with:

    pytest control_plane/tests/test_enroll.py -v

These tests use the in-memory Firestore stub + httpx-mocked Cloudflare API
from conftest.py — no real GCP, no real CF, no network. The same code paths
exercise real Firestore/CF when deployed.
"""
from __future__ import annotations

import base64

import pytest


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _fake_pubkey() -> str:
    """A plausible-looking 32-byte b64 string for tests. Doesn't have to be
    a real Ed25519 key for /v1/enroll — only /v1/attest verifies signatures."""
    return base64.b64encode(b"\x01" * 32).decode("ascii")


def _post_enroll(client, body: dict, *, email="dev@clayrune.io",
                 idempotency_key: str | None = None) -> "tuple[int, dict]":
    headers = {"X-Dev-User-Email": email}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    r = client.post("/v1/enroll", json=body, headers=headers)
    return r.status_code, r.json()


def _basic_body(*, username: str, pub: str | None = None,
                csrf: str = "csrf-test-nonce") -> dict:
    return {
        "device_pub_b64": pub or _fake_pubkey(),
        "csrf_nonce": csrf,
        "username": username,
        "device_name": "Test PC",
        "os": "win32-test",
        "mc_version": "1.4.2",
    }


# ─── Happy path ──────────────────────────────────────────────────────────────


def test_enroll_happy_path(client, mem_firestore, cf_recorder):
    status, body = _post_enroll(client, _basic_body(username="ron"))
    assert status == 200, body
    assert body["username"] == "ron"
    assert body["hostname"] == "ron.clayrune.io"
    assert body["device_id"].startswith("dev_")
    assert len(body["enrollment_token"]) >= 40  # 256-bit base64url

    # Cloudflare API was called in the right sequence
    methods = [(m, p.split("?")[0]) for m, p, _ in cf_recorder.calls]
    assert any("cfd_tunnel" in p and m == "POST" and not p.endswith("/configurations") for m, p in methods), \
        "create_named_tunnel was not called"
    assert any(p.endswith("/configurations") and m == "PUT" for m, p in methods), \
        "set_tunnel_ingress was not called"
    assert any("dns_records" in p and m == "POST" for m, p in methods), \
        "create_dns_cname was not called"
    assert any(p.endswith("/access/apps") and m == "POST" for m, p in methods), \
        "create_access_app was not called"

    # Firestore now has the user + device + username row
    dump = mem_firestore.dump()
    assert "users" in dump
    assert "devices" in dump
    assert "usernames" in dump and "ron" in dump["usernames"]

    # Device row carries the CF resource ids + the cf_tunnel_token (cached for /v1/attest)
    device_row = next(iter(dump["devices"].values()))
    assert device_row["cf_tunnel_uuid"].startswith("tun_")
    assert device_row["cf_tunnel_token"].startswith("MOCK_CF_TUNNEL_TOKEN_")
    assert device_row["cf_dns_record_id"].startswith("rec_")
    assert device_row["cf_access_app_id"].startswith("app_")
    assert device_row["hostname_claim"] == "ron.clayrune.io"

    # enrollment_token is stored only as a hash
    assert "enrollment_token_hash" in device_row
    assert device_row["enrollment_token_hash"] != body["enrollment_token"]


# ─── Username conflicts ──────────────────────────────────────────────────────


def test_enroll_username_taken(client):
    s1, _ = _post_enroll(client, _basic_body(username="alice"), email="alice@x.com")
    assert s1 == 200
    s2, body2 = _post_enroll(client, _basic_body(username="alice"), email="bob@x.com")
    assert s2 == 409, body2
    assert body2["code"] == "username_taken"


def test_enroll_username_invalid(client):
    s, body = _post_enroll(client, _basic_body(username="A"))  # too short, uppercase
    assert s == 400, body
    assert body["code"] == "username_invalid"


def test_enroll_username_reserved(client):
    s, body = _post_enroll(client, _basic_body(username="admin"))
    assert s == 409, body
    assert body["code"] == "username_reserved"


# ─── Rollback on partial CF failure ──────────────────────────────────────────


def test_enroll_rollback_on_dns_failure(client, mem_firestore, cf_recorder):
    """If DNS-create fails after tunnel-create succeeded, we should:
       - delete the orphan tunnel (rollback)
       - return a clean error
       - NOT persist user or device
       - release the username so retry works
    """
    cf_recorder.fail_after_n("POST", "/zones/zone-test/dns_records", status=500)

    status, body = _post_enroll(client, _basic_body(username="charlie"))
    assert status == 503, body
    assert body["code"] == "provisioning_failed"

    # Tunnel was created then deleted as part of rollback
    methods = [(m, p.split("?")[0]) for m, p, _ in cf_recorder.calls]
    assert any(m == "POST" and "cfd_tunnel" in p and not p.endswith("/configurations")
               for m, p in methods), "tunnel was never created"
    assert any(m == "DELETE" and "/cfd_tunnel/" in p for m, p in methods), \
        "tunnel was not rolled back"

    # No persisted user, device, or username claim
    dump = mem_firestore.dump()
    assert dump.get("devices", {}) == {}
    assert dump.get("usernames", {}) == {}, "username claim was not released"

    # Retry now succeeds (proving rollback fully released state). Clear the
    # simulated failure first so the retry's CF calls succeed.
    cf_recorder.fail_on.clear()
    s2, body2 = _post_enroll(client, _basic_body(username="charlie"))
    assert s2 == 200, body2


# ─── Idempotency ─────────────────────────────────────────────────────────────


def test_enroll_idempotency_returns_cached(client, mem_firestore, cf_recorder):
    """Repeating /v1/enroll with the same Idempotency-Key returns the same
    response and doesn't double-provision CF resources."""
    body1 = _basic_body(username="dave")
    s1, r1 = _post_enroll(client, body1, idempotency_key="idem-key-1")
    assert s1 == 200

    cf_calls_before_replay = len(cf_recorder.calls)

    s2, r2 = _post_enroll(client, body1, idempotency_key="idem-key-1")
    assert s2 == 200
    assert r1 == r2  # same response body
    # No new CF calls
    assert len(cf_recorder.calls) == cf_calls_before_replay


# ─── Auth ────────────────────────────────────────────────────────────────────


def test_enroll_requires_dev_email_when_no_firebase(client):
    """No X-Dev-User-Email and no Authorization → 401."""
    r = client.post("/v1/enroll", json=_basic_body(username="eve"))
    assert r.status_code == 401
    assert r.json()["code"] == "unauthorized"


# ─── Standalone runner (for `python test_enroll.py` without pytest) ──────────


def _run_standalone() -> None:
    """Run all tests above as a script. Useful when pytest isn't installed."""
    import inspect
    import sys

    # We need to manually wire the fixtures
    import os
    os.environ.setdefault("MC_CP_DEV_AUTH", "1")
    os.environ.setdefault("CLAYRUNE_PRIMARY_ZONE", "clayrune.io")
    os.environ.setdefault("CLOUDFLARE_API_TOKEN", "test-token")
    os.environ.setdefault("CLOUDFLARE_ACCOUNT_ID", "acc-test")
    os.environ.setdefault("CLOUDFLARE_ZONE_ID", "zone-test")

    from .conftest import (
        MemoryFirestore, CFRecorder, _cf_mock_handler,
    )
    import httpx

    failures = 0
    passes = 0
    test_fns = {
        name: fn for name, fn in inspect.getmembers(sys.modules[__name__], inspect.isfunction)
        if name.startswith("test_")
    }
    for name, fn in test_fns.items():
        # Build fresh fixtures
        from control_plane.app import firestore as cp_fs, routes_account, cloudflare
        mem = MemoryFirestore()
        if hasattr(cp_fs.db, "cache_clear"):
            cp_fs.db.cache_clear()
        cp_fs.db = lambda: mem  # type: ignore
        import google.cloud.firestore as gfs
        gfs.transactional = lambda f: (lambda txn: f(txn))

        rec = CFRecorder()
        transport = httpx.MockTransport(_cf_mock_handler(rec))
        http_client = httpx.AsyncClient(
            base_url=cloudflare.CF_API_BASE,
            transport=transport,
            headers={"Authorization": "Bearer test", "Content-Type": "application/json"},
        )
        cf_client = cloudflare.CloudflareClient(
            token="test", account_id="acc-test", zone_id="zone-test", client=http_client,
        )
        routes_account.set_cf_client_for_tests(cf_client)

        from fastapi.testclient import TestClient
        from control_plane.app.main import app
        c = TestClient(app)

        sig = inspect.signature(fn)
        kwargs = {}
        for p in sig.parameters:
            if p == "client":          kwargs[p] = c
            elif p == "mem_firestore": kwargs[p] = mem
            elif p == "cf_recorder":   kwargs[p] = rec

        try:
            fn(**kwargs)
            print(f"  PASS  {name}")
            passes += 1
        except AssertionError as e:
            print(f"  FAIL  {name}: {e}")
            failures += 1
        except Exception as e:
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
            failures += 1
        finally:
            routes_account.reset_cf_client()

    print()
    print(f"=== {passes} passed, {failures} failed ===")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    import sys
    sys.exit(_run_standalone())
