"""Shared test fixtures for control_plane.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

Provides:

  - `mem_firestore`        — an in-memory Firestore stub installed into
                             control_plane.app.firestore.db()
  - `cf_mock`              — an httpx.MockTransport-backed CloudflareClient
                             with a recorder of every API call
  - `client`               — FastAPI TestClient wired to the app with both
                             of the above injected
  - `seeded_versions_keys` — pre-populates `versions/` and `client_secret_keys/`
                             so attestation tests work end-to-end

Pytest is intentionally not strictly required to run these — the helpers
work standalone too (see test_enroll.py for an example of running outside
pytest in case you don't have it installed).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import uuid
from typing import Callable

import httpx
import pytest


# Make the parent package importable when running `pytest control_plane/tests`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Force dev auth on so X-Dev-User-Email works
os.environ.setdefault("MC_CP_DEV_AUTH", "1")
os.environ.setdefault("CLAYRUNE_PRIMARY_ZONE", "clayrune.io")
# Stub CLOUDFLARE_API_TOKEN so CloudflareClient.from_env() doesn't error
# during import (tests inject their own client anyway)
os.environ.setdefault("CLOUDFLARE_API_TOKEN", "test-token")
os.environ.setdefault("CLOUDFLARE_ACCOUNT_ID", "acc-test")
os.environ.setdefault("CLOUDFLARE_ZONE_ID", "zone-test")


# ─── In-memory Firestore stub ────────────────────────────────────────────────


class _MemDoc:
    def __init__(self, doc_id: str, data: dict | None = None, parent_coll: "_MemColl | None" = None):
        self.id = doc_id
        self._data = data
        self._parent = parent_coll

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict | None:
        return dict(self._data) if self._data else None

    @property
    def reference(self) -> "_MemDocRef":
        assert self._parent is not None
        return _MemDocRef(self._parent.store, self._parent.name, self.id)


class _MemDocRef:
    def __init__(self, store, name, doc_id):
        self.store = store
        self.name = name
        self.id = doc_id

    def get(self, transaction=None) -> _MemDoc:
        return _MemDoc(self.id, self.store.get((self.name, self.id)))

    def set(self, data: dict, merge: bool = False) -> None:
        key = (self.name, self.id)
        cur = self.store.get(key, {}) if merge else {}
        cur.update(data)
        self.store[key] = cur

    def delete(self) -> None:
        self.store.pop((self.name, self.id), None)


class _MemQuery:
    def __init__(self, store, name, filters=None, limit_n=None):
        self.store = store
        self.name = name
        self.filters = filters or []
        self.limit_n = limit_n

    def where(self, field, op, value):
        return _MemQuery(self.store, self.name, self.filters + [(field, op, value)], self.limit_n)

    def order_by(self, *_a, **_k):
        return self

    def limit(self, n):
        return _MemQuery(self.store, self.name, self.filters, n)

    def stream(self):
        emitted = 0
        for (n, doc_id), data in list(self.store.items()):
            if n != self.name:
                continue
            ok = True
            for field, op, value in self.filters:
                if op == "==" and data.get(field) != value:
                    ok = False
                    break
            if ok:
                yield _MemDoc(doc_id, data, parent_coll=_MemColl(self.store, self.name))
                emitted += 1
                if self.limit_n is not None and emitted >= self.limit_n:
                    return


class _MemColl:
    def __init__(self, store, name):
        self.store = store
        self.name = name

    def document(self, doc_id):
        return _MemDocRef(self.store, self.name, doc_id)

    def where(self, field, op, value):
        return _MemQuery(self.store, self.name).where(field, op, value)

    def add(self, data):
        ref = _MemDocRef(self.store, self.name, uuid.uuid4().hex)
        ref.set(data)
        return None, ref

    def stream(self):
        return _MemQuery(self.store, self.name).stream()


class _MemTxn:
    def __init__(self, store):
        self.store = store

    def delete(self, ref):
        self.store.pop((ref.name, ref.id), None)

    def set(self, ref, data: dict):
        self.store[(ref.name, ref.id)] = dict(data)


class MemoryFirestore:
    """Drop-in replacement for the google.cloud.firestore.Client we need."""

    def __init__(self):
        self._store: dict[tuple[str, str], dict] = {}

    def collection(self, name) -> _MemColl:
        return _MemColl(self._store, name)

    def transaction(self) -> _MemTxn:
        return _MemTxn(self._store)

    # Convenience for tests
    def dump(self) -> dict:
        out: dict = {}
        for (coll, doc_id), data in self._store.items():
            out.setdefault(coll, {})[doc_id] = data
        return out


@pytest.fixture
def mem_firestore() -> MemoryFirestore:
    """Install a fresh in-memory Firestore stub before each test."""
    from control_plane.app import firestore as cp_fs

    mem = MemoryFirestore()
    cp_fs.db.cache_clear()
    cp_fs.db = lambda: mem  # type: ignore[assignment]

    # Patch firestore.transactional decorator to a passthrough so our stub txns work
    import google.cloud.firestore as gfs  # type: ignore
    gfs.transactional = lambda fn: (lambda txn: fn(txn))

    yield mem


# ─── Cloudflare mock ─────────────────────────────────────────────────────────


class CFRecorder:
    """Records every CF API call the test makes; lets tests assert on them."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict | None]] = []  # (method, path, json_body)
        self.fail_on: dict[tuple[str, str], int] = {}        # (method, path_prefix) -> http status

    def fail_after_n(self, method: str, path_prefix: str, status: int = 500) -> None:
        self.fail_on[(method.upper(), path_prefix)] = status

    def should_fail(self, method: str, path: str) -> int | None:
        for (m, prefix), status in self.fail_on.items():
            if m == method.upper() and path.startswith(prefix):
                return status
        return None


def _cf_mock_handler(recorder: CFRecorder) -> Callable[[httpx.Request], httpx.Response]:
    """Build an httpx.MockTransport handler that emulates a minimal CF API."""

    def handler(req: httpx.Request) -> httpx.Response:
        method = req.method.upper()
        path = req.url.path  # e.g. /client/v4/accounts/...
        # Strip the /client/v4 prefix
        if path.startswith("/client/v4"):
            path = path[len("/client/v4"):]

        body = None
        if req.content:
            try:
                body = json.loads(req.content)
            except Exception:
                body = None
        recorder.calls.append((method, path, body))

        # Simulated failure
        fail_status = recorder.should_fail(method, path)
        if fail_status is not None:
            return httpx.Response(fail_status, json={
                "success": False,
                "errors": [{"code": 9999, "message": f"simulated failure on {method} {path}"}],
                "result": None,
            })

        # ─── Account / zone discovery ──────────────────────────────────────
        if method == "GET" and path == "/accounts":
            return _ok([{"id": "acc-test", "name": "test-account"}])

        if method == "GET" and path == "/zones":
            return _ok([{"id": "zone-test", "name": req.url.params.get("name", "clayrune.io")}])

        # ─── Tunnel ────────────────────────────────────────────────────────
        if method == "POST" and "/cfd_tunnel" in path and not path.endswith("/configurations"):
            tunnel_id = "tun_" + uuid.uuid4().hex[:12]
            return _ok({"id": tunnel_id, "name": (body or {}).get("name", "")})

        if method == "GET" and path.endswith("/token"):
            # Tunnel token fetch — CF returns a string in `result`
            return _ok("MOCK_CF_TUNNEL_TOKEN_" + uuid.uuid4().hex[:24])

        if method == "PUT" and path.endswith("/configurations"):
            return _ok({"version": 1})

        if method == "DELETE" and "/cfd_tunnel/" in path:
            return _ok(None)

        # ─── DNS ───────────────────────────────────────────────────────────
        if method == "POST" and "/dns_records" in path:
            rec_id = "rec_" + uuid.uuid4().hex[:12]
            return _ok({"id": rec_id, **(body or {})})

        if method == "DELETE" and "/dns_records/" in path:
            return _ok(None)

        # ─── Access apps ───────────────────────────────────────────────────
        if method == "POST" and path.endswith("/access/apps"):
            app_id = "app_" + uuid.uuid4().hex[:12]
            return _ok({"id": app_id, **(body or {})})

        if method == "POST" and "/access/apps/" in path and path.endswith("/policies"):
            return _ok({"id": "pol_" + uuid.uuid4().hex[:12], **(body or {})})

        if method == "DELETE" and "/access/apps/" in path:
            return _ok(None)

        # ─── User token verify ─────────────────────────────────────────────
        if method == "GET" and path == "/user/tokens/verify":
            return _ok({"id": "tok-test", "status": "active"})

        # Fallback
        return httpx.Response(404, json={
            "success": False,
            "errors": [{"code": 7000, "message": f"unmocked CF endpoint: {method} {path}"}],
            "result": None,
        })

    return handler


def _ok(result) -> httpx.Response:
    return httpx.Response(200, json={
        "success": True,
        "errors": [],
        "messages": [],
        "result": result,
    })


@pytest.fixture
def cf_recorder() -> CFRecorder:
    return CFRecorder()


@pytest.fixture
def cf_mock(cf_recorder):
    """Install a mocked CloudflareClient into the route module."""
    from control_plane.app import cloudflare, routes_account

    transport = httpx.MockTransport(_cf_mock_handler(cf_recorder))
    http_client = httpx.AsyncClient(
        base_url=cloudflare.CF_API_BASE,
        transport=transport,
        headers={"Authorization": "Bearer test", "Content-Type": "application/json"},
    )
    client = cloudflare.CloudflareClient(
        token="test", account_id="acc-test", zone_id="zone-test", client=http_client,
    )
    routes_account.set_cf_client_for_tests(client)
    yield client
    routes_account.reset_cf_client()


# ─── FastAPI TestClient ─────────────────────────────────────────────────────


@pytest.fixture
def client(mem_firestore, cf_mock):
    """FastAPI TestClient with Firestore + CF mock both injected."""
    from fastapi.testclient import TestClient
    from control_plane.app.main import app

    return TestClient(app)
