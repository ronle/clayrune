"""Session JWT + JWKS + entitlement + denylist — the CF Access replacement.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

The thing these tests exist to stop is not a cost regression. It is this:

    Cloudflare Access was doing TWO jobs. Authentication, and AUTHORIZATION —
    its per-user email policy is what stopped alice from reaching
    bob.clayrune.io. Remove Access without the `u` claim, and we did not save
    $7/user; we published every customer's dev machine to the internet.

So the load-bearing tests here are the ones about the `u` claim: that it is
minted from the enrolled username in Firestore, that it cannot be influenced by
anything the client sends, and that the Worker's `claims.u === subdomain` check
therefore has something trustworthy to compare against.

`test_worker_verification_contract` re-implements the Worker's verify() step for
step. If you change `jwt_es256.sign()` and that test still passes, the Worker will
still accept our tokens.
"""
from __future__ import annotations

import base64
import datetime as _dt
import json
import time

import pytest

from control_plane.app import entitlement, jwt_es256, sessions


# ─── Keys ────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def fresh_keys(monkeypatch):
    """An ephemeral dev keyring per test."""
    monkeypatch.delenv("CLAYRUNE_JWT_SIGNING_KEYS", raising=False)
    monkeypatch.delenv("CLAYRUNE_JWT_SIGNING_KEY_PEM", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("CLAYRUNE_BILLING_ENFORCED", raising=False)
    jwt_es256.reset_keys_for_tests()
    yield
    jwt_es256.reset_keys_for_tests()


def _b64u(seg: str) -> bytes:
    return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))


def _cookie(resp, name: str) -> str:
    """Pull a cookie out of the Set-Cookie header.

    Not `resp.cookies` — the cookies we set carry `Domain=.clayrune.io` and the
    TestClient talks to `testserver`, so httpx's jar correctly refuses to store
    them. The header is the thing the real browser (and then the Worker) sees.
    """
    for raw in resp.headers.get_list("set-cookie"):
        k, _, rest = raw.partition("=")
        if k.strip() == name:
            return rest.split(";", 1)[0]
    return ""


# ─── JWKS ────────────────────────────────────────────────────────────────────


def test_jwks_shape_is_what_the_worker_imports():
    """The Worker does `crypto.subtle.importKey('jwk', jwk, {name:'ECDSA',
    namedCurve:'P-256'}, ...)` and looks the key up by `kid`. Anything missing
    from this dict is a runtime failure at the edge, not here."""
    doc = jwt_es256.jwks()
    assert isinstance(doc.get("keys"), list) and doc["keys"]
    k = doc["keys"][0]
    assert k["kty"] == "EC"
    assert k["crv"] == "P-256"
    assert k["alg"] == "ES256"
    assert k["use"] == "sig"
    assert k["kid"]
    # x/y must be 32-byte big-endian coordinates, base64url, unpadded
    assert len(_b64u(k["x"])) == 32
    assert len(_b64u(k["y"])) == 32
    assert "=" not in k["x"] and "=" not in k["y"]
    # NO private material, ever
    assert "d" not in k


def test_jwks_endpoint_is_public_and_cacheable(client):
    r = client.get("/v1/jwks")
    assert r.status_code == 200
    assert r.json()["keys"]
    # The Worker caches for 10 min; the header should agree with it.
    assert "max-age=600" in r.headers.get("cache-control", "")


def test_jwks_publishes_inactive_keys_too(monkeypatch):
    """Rotation only works if the OLD key stays published while tokens signed
    with it are still alive. Publish everything; sign with the active one."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    def pem() -> str:
        return ec.generate_private_key(ec.SECP256R1()).private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()

    monkeypatch.setenv("CLAYRUNE_JWT_SIGNING_KEYS", json.dumps([
        {"kid": "new", "pem": pem(), "active": True},
        {"kid": "old", "pem": pem(), "active": False},
    ]))
    jwt_es256.reset_keys_for_tests()

    assert {k["kid"] for k in jwt_es256.jwks()["keys"]} == {"new", "old"}
    assert jwt_es256.signing_key().kid == "new"
    header = json.loads(_b64u(jwt_es256.sign({"sub": "u1"}, ttl_seconds=60).split(".")[0]))
    assert header["kid"] == "new"


# ─── The Worker's verification contract ──────────────────────────────────────


def test_worker_verification_contract():
    """Replays edge-worker/src/index.js `verify()` against a token we minted.

    Every assertion here is a line in the Worker. The one that has bitten every
    hand-rolled JOSE implementation is the signature encoding: `cryptography`
    returns a DER sequence, WebCrypto wants raw r||s. A DER signature verifies
    fine in Python and is silently rejected at the edge.
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    token = jwt_es256.sign(
        {"iss": "https://api.clayrune.io", "aud": "clayrune-dashboard",
         "sub": "u_alice", "u": "alice", "plan": "connect", "entitled": True},
        ttl_seconds=1800,
    )
    h64, p64, s64 = token.split(".")
    header = json.loads(_b64u(h64))
    claims = json.loads(_b64u(p64))

    # `if (header.alg !== 'ES256') return null;`
    assert header["alg"] == "ES256"
    # `keys.find(k => k.kid === header.kid)`
    jwk = next((k for k in jwt_es256.jwks()["keys"] if k["kid"] == header["kid"]), None)
    assert jwk is not None

    # `crypto.subtle.verify({name:'ECDSA', hash:'SHA-256'}, key, sig, data)` —
    # a raw 64-byte r||s over the `<h64>.<p64>` ASCII bytes.
    raw = _b64u(s64)
    assert len(raw) == 64, "signature must be raw r||s, not DER — WebCrypto rejects DER"

    pub = ec.EllipticCurvePublicNumbers(
        x=int.from_bytes(_b64u(jwk["x"]), "big"),
        y=int.from_bytes(_b64u(jwk["y"]), "big"),
        curve=ec.SECP256R1(),
    ).public_key()
    pub.verify(
        encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
        f"{h64}.{p64}".encode("ascii"),
        ec.ECDSA(hashes.SHA256()),
    )

    # `if (claims.exp <= now) return null;` / iss / aud pins
    assert claims["exp"] > int(time.time())
    assert claims["iss"] == "https://api.clayrune.io"
    assert claims["aud"] == "clayrune-dashboard"
    # `if (claims.u !== want) return 403` — the authorization claim must be there
    assert claims["u"] == "alice"


def test_algorithm_is_pinned_not_negotiated():
    """`alg` is a constant on both sides. A token claiming another algorithm —
    including `none` — is rejected before any key is even looked up."""
    token = jwt_es256.sign({"sub": "u1"}, ttl_seconds=60)
    h64, p64, s64 = token.split(".")

    def reheader(alg):
        h = json.dumps({"alg": alg, "typ": "JWT",
                        "kid": jwt_es256.signing_key().kid}).encode()
        return base64.urlsafe_b64encode(h).rstrip(b"=").decode() + f".{p64}.{s64}"

    for alg in ("none", "HS256", "RS256", "ES384"):
        with pytest.raises(jwt_es256.JWTError):
            jwt_es256.verify(reheader(alg))


def test_tampered_payload_fails_and_expiry_is_enforced():
    token = jwt_es256.sign({"sub": "u_alice", "u": "alice"}, ttl_seconds=60)
    h64, p64, s64 = token.split(".")

    forged = base64.urlsafe_b64encode(
        json.dumps({"sub": "u_alice", "u": "bob", "exp": int(time.time()) + 60}).encode()
    ).rstrip(b"=").decode()
    with pytest.raises(jwt_es256.JWTError):
        jwt_es256.verify(f"{h64}.{forged}.{s64}")

    with pytest.raises(jwt_es256.JWTError):
        jwt_es256.verify(jwt_es256.sign({"sub": "u1"}, ttl_seconds=-1))


def test_unknown_kid_is_rejected():
    token = jwt_es256.sign({"sub": "u1"}, ttl_seconds=60)
    jwt_es256.reset_keys_for_tests()  # new ephemeral keyring → old kid is unknown
    with pytest.raises(jwt_es256.JWTError):
        jwt_es256.verify(token)


def test_access_ttl_is_clamped_to_the_workers_contract(monkeypatch):
    """15–60 min. Shorter hammers the refresh endpoint; longer makes the
    revocation lag indefensible to a customer who just cancelled."""
    monkeypatch.setenv("CLAYRUNE_SESSION_TTL_S", "5")
    assert sessions.access_ttl_seconds() == 15 * 60
    monkeypatch.setenv("CLAYRUNE_SESSION_TTL_S", "86400")
    assert sessions.access_ttl_seconds() == 60 * 60


# ─── The `u` claim — the authorization claim ─────────────────────────────────


def test_u_claim_comes_from_firestore_never_from_the_client(mem_firestore):
    """THE test. `u` is what the Worker compares against the subdomain. If it
    could be influenced by the client, alice could mint herself `u: "bob"` and
    walk into bob.clayrune.io — exactly what CF Access's email policy prevented."""
    user_row = {"user_id": "u_alice", "username": "alice", "email": "alice@example.com"}
    token, _ = sessions.mint_access_jwt(user_row, session_id="sess_x")
    claims = jwt_es256.verify(token, issuer=sessions.issuer(), audience=sessions.audience())
    assert claims["u"] == "alice"
    assert claims["sub"] == "u_alice"
    assert claims["sid"] == "sess_x"

    # `mint_access_jwt` takes the row, not a request. There is no parameter for a
    # caller to pass a username through — and a row with no username is refused
    # outright rather than minting a token with an empty/absent `u`, which the
    # Worker would compare against a real subdomain.
    with pytest.raises(ValueError):
        sessions.mint_access_jwt({"user_id": "u_x", "username": ""}, session_id="s")


def test_entitled_claim_reflects_live_state(mem_firestore, monkeypatch):
    monkeypatch.setenv("CLAYRUNE_BILLING_ENFORCED", "1")
    row = {"user_id": "u1", "username": "ron", "plan": "connect",
           "sub_status": "active",
           "current_period_end": _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=5)}
    token, _ = sessions.mint_access_jwt(row, session_id="s1")
    assert jwt_es256.verify(token)["entitled"] is True

    row["sub_status"] = "canceled"
    token, _ = sessions.mint_access_jwt(row, session_id="s1")
    assert jwt_es256.verify(token)["entitled"] is False


# ─── Entitlement predicate ───────────────────────────────────────────────────


def _now():
    return _dt.datetime.now(_dt.timezone.utc)


def test_suspension_beats_everything_even_unenforced_billing(monkeypatch):
    """Suspension is an abuse control, not a billing state. It must bite whether
    or not billing enforcement is switched on — otherwise a fraudster keeps their
    box for as long as we haven't finished building Stripe."""
    row = {"suspended": True, "plan": "connect", "sub_status": "active",
           "current_period_end": _now() + _dt.timedelta(days=30)}
    monkeypatch.setenv("CLAYRUNE_BILLING_ENFORCED", "0")
    assert entitlement.is_entitled(row) is False
    monkeypatch.setenv("CLAYRUNE_BILLING_ENFORCED", "1")
    assert entitlement.is_entitled(row) is False


def test_billing_unenforced_does_not_lock_out_existing_users(monkeypatch):
    """No user row has `sub_status` yet. Enforcing the predicate literally today
    would lock out every enrolled user, including the ones paying attention."""
    monkeypatch.setenv("CLAYRUNE_BILLING_ENFORCED", "0")
    assert entitlement.is_entitled({"user_id": "u1", "username": "ron"}) is True


def test_past_due_fails_open_through_the_grace_window(monkeypatch):
    """Never kill a paying customer because OUR webhook broke or their card is
    mid-retry. Fail open on billing; fail closed on identity."""
    monkeypatch.setenv("CLAYRUNE_BILLING_ENFORCED", "1")
    inside = {"plan": "connect", "sub_status": "past_due",
              "grace_until": _now() + _dt.timedelta(days=3)}
    outside = {"plan": "connect", "sub_status": "past_due",
               "grace_until": _now() - _dt.timedelta(days=1)}
    no_window = {"plan": "connect", "sub_status": "past_due"}
    assert entitlement.is_entitled(inside) is True
    assert entitlement.is_entitled(outside) is False
    assert entitlement.is_entitled(no_window) is False


def test_local_plan_and_expired_period_have_no_remote_access(monkeypatch):
    monkeypatch.setenv("CLAYRUNE_BILLING_ENFORCED", "1")
    assert entitlement.is_entitled({"plan": "local", "sub_status": "active",
                                    "current_period_end": _now() + _dt.timedelta(days=9)}) is False
    assert entitlement.is_entitled({"plan": "connect", "sub_status": "active",
                                    "current_period_end": _now() - _dt.timedelta(seconds=1)}) is False
    assert entitlement.is_entitled({"plan": "connect", "sub_status": "none"}) is False


# ─── Session store ───────────────────────────────────────────────────────────


def test_refresh_token_is_stored_hashed_only(mem_firestore):
    _, token, _ = sessions.create(user_id="u1", username="ron")
    rows = mem_firestore.dump()["sessions"]
    blob = json.dumps(rows, default=str)
    secret = token.split(".", 1)[1]
    assert secret not in blob, "the refresh secret must never be persisted in the clear"
    assert sessions.resolve_refresh(token) is not None


def test_revoke_is_per_session_and_ownership_is_enforced(mem_firestore):
    """CF's per-session revoke was so unreliable the old code fell back to
    nuking every session the user had. Ours revokes one — and refuses to revoke
    a session belonging to someone else, no matter who asks."""
    s1, t1, _ = sessions.create(user_id="u1", username="ron")
    _, t2, _ = sessions.create(user_id="u1", username="ron")

    assert sessions.revoke(s1, user_id="u_attacker") is False
    assert sessions.resolve_refresh(t1) is not None, "another user must not revoke my session"

    assert sessions.revoke(s1, user_id="u1") is True
    assert sessions.resolve_refresh(t1) is None
    assert sessions.resolve_refresh(t2) is not None, "revoke must not be a blast radius"

    assert sessions.revoke_all("u1") == 1
    assert sessions.resolve_refresh(t2) is None


def test_bad_refresh_tokens_all_fail_the_same_way(mem_firestore):
    sid, token, _ = sessions.create(user_id="u1", username="ron")
    assert sessions.resolve_refresh("") is None
    assert sessions.resolve_refresh("garbage") is None
    assert sessions.resolve_refresh(f"{sid}.wrong-secret") is None
    assert sessions.resolve_refresh(f"sess_nope.{token.split('.', 1)[1]}") is None


def test_expired_refresh_token_is_dead(mem_firestore):
    sid, token, _ = sessions.create(user_id="u1", username="ron")
    mem_firestore.collection("sessions").document(sid).set(
        {"expires_at": _now() - _dt.timedelta(seconds=1)}, merge=True)
    assert sessions.resolve_refresh(token) is None


# ─── /v1/session/* endpoints ─────────────────────────────────────────────────


def _enroll_user(mem, *, user_id="u_ron", username="ron", **extra):
    mem.collection("users").document(user_id).set(
        {"user_id": user_id, "username": username, "email": f"{username}@example.com", **extra})
    return user_id


def test_refresh_is_the_live_entitlement_chokepoint(client, mem_firestore, monkeypatch):
    """The whole design rests on this: the edge cannot know anything the token
    doesn't say, so refresh is the ONE moment we re-read the world. If it minted
    from the old token's claims instead of from Firestore, a cancellation would
    never take effect."""
    uid = _enroll_user(mem_firestore)
    _, token, _ = sessions.create(user_id=uid, username="ron")

    r = client.post("/v1/session/refresh", json={"refresh_token": token})
    assert r.status_code == 200, r.text
    assert r.json()["entitled"] is True
    assert jwt_es256.verify(_cookie(r, sessions.COOKIE_NAME))["u"] == "ron"

    # Now suspend them — nothing about the session changed, only the world.
    mem_firestore.collection("users").document(uid).set({"suspended": True}, merge=True)

    r = client.post("/v1/session/refresh", json={"refresh_token": token})
    assert r.status_code == 402
    assert r.json()["code"] == "not_entitled"
    assert r.json()["paywall_url"]


def test_refresh_401_and_402_are_different_things(client, mem_firestore):
    """401 = we don't know who you are (→ sign in). 402 = we know exactly who you
    are and you haven't paid (→ checkout). Collapsing them bounces a lapsed
    customer through sign-in forever instead of to a page where they can pay."""
    uid = _enroll_user(mem_firestore, user_id="u_lapsed", username="lapsed",
                       suspended=True)
    _, good = sessions.create(user_id=uid, username="lapsed")[0:2]

    assert client.post("/v1/session/refresh", json={"refresh_token": "nope"}).status_code == 401
    assert client.post("/v1/session/refresh", json={"refresh_token": good}).status_code == 402


def test_refusing_to_mint_does_not_destroy_the_session(client, mem_firestore):
    """A suspension can be lifted and a card retry can succeed. The user should
    not have to re-authenticate to find that out."""
    uid = _enroll_user(mem_firestore, suspended=True)
    _, token, _ = sessions.create(user_id=uid, username="ron")

    assert client.post("/v1/session/refresh", json={"refresh_token": token}).status_code == 402
    mem_firestore.collection("users").document(uid).set({"suspended": False}, merge=True)
    assert client.post("/v1/session/refresh", json={"refresh_token": token}).status_code == 200


def test_revoked_session_cannot_refresh(client, mem_firestore):
    uid = _enroll_user(mem_firestore)
    sid, token, _ = sessions.create(user_id=uid, username="ron")
    sessions.revoke(sid, user_id=uid)
    r = client.post("/v1/session/refresh", json={"refresh_token": token})
    assert r.status_code == 401
    assert r.json()["code"] == "session_invalid"


def test_username_change_propagates_into_the_u_claim(client, mem_firestore):
    """The `u` claim is re-read from Firestore on every refresh. Without this a
    renamed user's session stays pinned to the old subdomain and the Worker 403s
    them off their own dashboard."""
    uid = _enroll_user(mem_firestore)
    _, token, _ = sessions.create(user_id=uid, username="ron")

    mem_firestore.collection("users").document(uid).set({"username": "ronald"}, merge=True)
    r = client.post("/v1/session/refresh", json={"refresh_token": token})
    assert r.status_code == 200
    assert r.json()["username"] == "ronald"
    assert jwt_es256.verify(_cookie(r, sessions.COOKIE_NAME))["u"] == "ronald"


def test_session_cookie_flags(client, mem_firestore):
    """`Domain=.clayrune.io` is what lets one cookie work on every user
    subdomain; HttpOnly keeps it away from XSS; the Worker reads it by name."""
    uid = _enroll_user(mem_firestore)
    _, token, _ = sessions.create(user_id=uid, username="ron")
    r = client.post("/v1/session/refresh", json={"refresh_token": token})

    setc = r.headers.get("set-cookie", "")
    assert sessions.COOKIE_NAME + "=" in setc
    assert "HttpOnly" in setc
    assert "Domain=.clayrune.io" in setc
    assert "SameSite=lax" in setc.lower() or "samesite=lax" in setc.lower()


def test_signin_page_will_not_become_an_open_redirect(client):
    """The Worker appends `?return_to=<whatever the visitor asked for>`, so that
    parameter is attacker-controlled and lands on a page with a live session
    cookie in scope."""
    from control_plane.app.routes_auth import _safe_return_to

    assert _safe_return_to("https://ron.clayrune.io/") == "https://ron.clayrune.io/"
    assert _safe_return_to("https://clayrune.io/upgrade") == "https://clayrune.io/upgrade"
    for bad in ("https://evil.com/", "https://clayrune.io.evil.com/", "http://ron.clayrune.io/",
                "javascript:alert(1)", "//evil.com", "https://notclayrune.io/"):
        assert _safe_return_to(bad) is None, bad

    assert client.get("/v1/signin", params={"return_to": "https://evil.com/"}).status_code == 200
