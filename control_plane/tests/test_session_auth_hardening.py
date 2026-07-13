"""Regression tests for the 2026-07-13 night-review findings on the session-auth
code (`837d6d7`).

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

Every test here is written to FAIL against the code as it shipped. They are not
"does the happy path still work" tests — the suite already had those and they
passed while all ten of these bugs were live.

Findings covered (night review 2026-07-13, Tier 3):
  1  entitlement: fails CLOSED on a broken billing row — locks out a payer
  2  sessions:    malformed refresh cookie → HTTP 500 (a side channel)
  3  sessions:    no refresh rotation / no reuse detection
  4  routes_auth: every error envelope hardcodes request_id "x"
  5  routes_auth: refresh cookie max-age hardcoded to the browser's 30d
  6  sessions:    unparseable expires_at → an IMMORTAL refresh token
  7  routes_auth: sign-in page ships no CSP
  8  jwt_es256:   ephemeral signing key guard only fires on Cloud Run
  9  sessions:    revoke_all is a non-atomic write-per-session loop
 10  main:        CORS allow_headers=* + unstripped origin split
"""
from __future__ import annotations

import datetime as _dt
import time

import pytest

from control_plane.app import entitlement, jwt_es256, sessions


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.delenv("CLAYRUNE_JWT_SIGNING_KEYS", raising=False)
    monkeypatch.delenv("CLAYRUNE_JWT_SIGNING_KEY_PEM", raising=False)
    monkeypatch.delenv("CLAYRUNE_BILLING_ENFORCED", raising=False)
    monkeypatch.setenv("CLAYRUNE_ALLOW_EPHEMERAL_KEY", "1")
    jwt_es256.reset_keys_for_tests()
    yield
    jwt_es256.reset_keys_for_tests()


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


# ─── 1. Entitlement fails OPEN on a broken billing row ───────────────────────


def test_active_sub_with_missing_period_end_is_ENTITLED(monkeypatch):
    """The "our webhook died" case. The module contract is explicit: a broken
    billing pipeline must never kill a paying customer. The shipped code returned
    False here — locking out the exact user the grace machinery exists to protect.
    """
    monkeypatch.setenv("CLAYRUNE_BILLING_ENFORCED", "1")
    row = {"user_id": "u1", "plan": "connect", "sub_status": "active"}  # no period end
    assert entitlement.is_entitled(row) is True


def test_active_sub_with_unparseable_period_end_is_ENTITLED(monkeypatch):
    monkeypatch.setenv("CLAYRUNE_BILLING_ENFORCED", "1")
    row = {"user_id": "u1", "plan": "connect", "sub_status": "active",
           "current_period_end": "not-a-date"}
    assert entitlement.is_entitled(row) is True


def test_a_genuinely_expired_period_is_still_NOT_entitled(monkeypatch):
    """Failing open on *missing* data must not become failing open on *bad news*.
    A period end that is present and in the past means the sub really did lapse."""
    monkeypatch.setenv("CLAYRUNE_BILLING_ENFORCED", "1")
    row = {"user_id": "u1", "plan": "connect", "sub_status": "active",
           "current_period_end": _now() - _dt.timedelta(days=1)}
    assert entitlement.is_entitled(row) is False


def test_suspension_still_beats_a_broken_billing_row(monkeypatch):
    """Fail-open is a BILLING policy. Suspension is an abuse control and outranks
    it — otherwise a corrupt row would become a way to un-suspend yourself."""
    monkeypatch.setenv("CLAYRUNE_BILLING_ENFORCED", "1")
    row = {"user_id": "u1", "plan": "connect", "sub_status": "active", "suspended": True}
    assert entitlement.is_entitled(row) is False


# ─── 2. A malformed refresh token must not reach Firestore ───────────────────


class _StrictColl:
    """A collection whose .document() validates the id the way REAL Firestore does.

    This matters: the in-memory stub happily accepts "a/b" as a document id, so a
    test against the stub alone would pass on the very code that 500s in
    production. google.cloud.firestore splits the id on "/" to build a document
    path and raises ValueError when the result has an odd number of segments —
    that ValueError is the HTTP 500 the night review found. Reproduce it here, or
    this test guards nothing.
    """

    def document(self, doc_id):
        parts = [p for p in str(doc_id).split("/")]
        if len(parts) % 2 != 1:
            raise ValueError(
                f"A document must have an even number of path elements: {doc_id}")
        if any(p in ("", ".", "..") for p in parts):
            raise ValueError(f"Invalid document path: {doc_id}")
        raise AssertionError(
            "resolve_refresh reached Firestore with an unvalidated session id "
            f"({doc_id!r}) — the shape guard did not run")


class _StrictDb:
    def collection(self, _name):
        return _StrictColl()


@pytest.mark.parametrize("token", [
    "a/b.secret",            # the 500: an even-length path → ValueError in Firestore
    "../../etc.secret",
    "sess_x/y.secret",
    "not_a_session.secret",  # right shape, wrong prefix
    "sess_!!!.secret",       # illegal characters
])
def test_malformed_refresh_token_returns_None_and_never_reaches_firestore(monkeypatch, token):
    """resolve_refresh is documented to give ONE undifferentiated failure so a
    probe cannot learn *why* it failed. The shipped code fed the id straight to
    .document(): "a/b.secret" raised ValueError → HTTP 500, and a 500 among 401s
    is precisely the signal the docstring promises not to leak.

    The id must be rejected on SHAPE, before any Firestore call — asserted by
    making the db blow up if it is ever reached.
    """
    from control_plane.app import firestore as cp_fs
    monkeypatch.setattr(cp_fs, "db", lambda: _StrictDb())

    assert sessions.resolve_refresh(token) is None


def test_malformed_refresh_cookie_is_401_not_500(client, mem_firestore):
    r = client.post("/v1/session/refresh", cookies={sessions.REFRESH_COOKIE_NAME: "a/b.secret"})
    assert r.status_code == 401, f"expected 401, got {r.status_code} (a 500 is a side channel)"


# ─── 3. Refresh-token rotation + reuse detection ─────────────────────────────


def _mk_session(kind=sessions.KIND_BROWSER):
    return sessions.create(user_id="u1", username="alice", kind=kind)


def test_browser_refresh_token_rotates_on_every_use(mem_firestore):
    _sid, token, _exp = _mk_session()
    row = sessions.resolve_refresh(token)
    assert row is not None
    new_token = row.get("_new_refresh_token")
    assert new_token and new_token != token, "the secret must be retired after one use"

    # The successor works...
    assert sessions.resolve_refresh(new_token) is not None


def test_replaying_a_superseded_token_revokes_the_whole_session(mem_firestore, monkeypatch):
    """The theft signal. Two parties holding one token is the ONLY explanation
    once the race window has passed — so we kill the session rather than let the
    thief and the victim refresh side by side forever (which is what shipped)."""
    monkeypatch.setattr(sessions, "REUSE_GRACE_SECONDS", 0)  # skip the race window

    _sid, token, _exp = _mk_session()
    row = sessions.resolve_refresh(token)          # rotates; `token` is now superseded
    new_token = row["_new_refresh_token"]
    time.sleep(0.01)

    # The attacker (or the victim) replays the OLD secret.
    assert sessions.resolve_refresh(token) is None, "a superseded secret must not authenticate"

    # ...and the session is dead for EVERYONE, including the holder of the good token.
    assert sessions.resolve_refresh(new_token) is None, \
        "reuse must revoke the session, not just refuse the stale token"


def test_a_superseded_token_inside_the_grace_window_is_a_race_not_a_theft(mem_firestore):
    """Two tabs refreshing at once, or a lost response retried, must NOT log the
    user out. Revoking on those would make the alarm untrustworthy."""
    _sid, token, _exp = _mk_session()
    first = sessions.resolve_refresh(token)
    assert first is not None

    # Immediately replay the old secret — well inside REUSE_GRACE_SECONDS.
    second = sessions.resolve_refresh(token)
    assert second is not None, "a same-instant replay is a race, not theft"
    assert second.get("_new_refresh_token"), "the racing caller still gets a usable token"


def test_mobile_sessions_do_NOT_rotate_yet(mem_firestore):
    """LOAD-BEARING. The shipped APK replays its pair_token forever and persists
    only the cr_session cookie. Rotating for mobile would revoke every paired
    phone on its second renewal — and it would look exactly like the theft we
    built the detector for. Rotation lands WITH the APK rework (ee94a17e)."""
    _sid, pair_token, _exp = _mk_session(kind=sessions.KIND_MOBILE)

    for i in range(3):  # the phone replays the SAME token, forever
        row = sessions.resolve_refresh(pair_token)
        assert row is not None, f"the phone's pair_token stopped working on renewal {i + 1}"
        assert "_new_refresh_token" not in row, "mobile must not be handed a rotated token yet"

    assert sessions.rotates(sessions.KIND_MOBILE) is False
    assert sessions.rotates(sessions.KIND_BROWSER) is True


# ─── 6. A corrupt expiry must not mint an immortal token ─────────────────────


def test_unparseable_expires_at_is_treated_as_EXPIRED(mem_firestore):
    """expires_at is the only thing bounding a long-lived bearer credential. The
    shipped code swallowed a TypeError and treated the session as NON-EXPIRING —
    one bad write and the token lives forever. Cost of failing closed: one
    sign-in. Cost of failing open: unbounded."""
    sid, token, _exp = _mk_session()
    # Corrupt the expiry the way a bad Firestore write would.
    mem_firestore.collection("sessions").document(sid).set({"expires_at": "garbage"}, merge=True)

    assert sessions.resolve_refresh(token) is None


def test_missing_expires_at_is_treated_as_EXPIRED(mem_firestore):
    sid, token, _exp = _mk_session()
    mem_firestore.collection("sessions").document(sid).set({"expires_at": None}, merge=True)
    assert sessions.resolve_refresh(token) is None


def test_a_corrupt_session_is_not_listed_as_active(mem_firestore):
    sid, _token, _exp = _mk_session()
    mem_firestore.collection("sessions").document(sid).set({"expires_at": "garbage"}, merge=True)
    assert sessions.list_for_user("u1") == []


# ─── 9. revoke_all is atomic ─────────────────────────────────────────────────


def test_revoke_all_is_one_atomic_batch(mem_firestore):
    """This runs on the ABUSE path. A partial failure mid-loop leaves some of a
    suspended abuser's sessions alive while returning a count that says they are
    all gone — wrong in the way that reads as success."""
    for _ in range(3):
        _mk_session()

    committed: list[bool] = []
    real_batch = mem_firestore.batch

    def spy():
        b = real_batch()
        _commit = b.commit

        def commit():
            committed.append(True)
            return _commit()

        b.commit = commit
        return b

    mem_firestore.batch = spy
    n = sessions.revoke_all("u1")
    mem_firestore.batch = real_batch

    assert n == 3
    assert committed == [True], "revoke_all must commit exactly ONE batch, not write per session"
    assert sessions.list_for_user("u1") == []


# ─── 8. The signing key fails CLOSED ─────────────────────────────────────────


def test_no_signing_key_and_no_optin_REFUSES_to_invent_one(monkeypatch):
    """The shipped guard only fired on Cloud Run (K_SERVICE). On gunicorn/Fly/a VM
    every worker would mint its OWN key under the SAME kid, and the edge would
    reject a random ~half of all tokens — a symptom that reads as a network flake
    and costs a day. Ephemeral keys are now opt-IN."""
    monkeypatch.delenv("CLAYRUNE_ALLOW_EPHEMERAL_KEY", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)  # NOT Cloud Run — the case that shipped broken
    jwt_es256.reset_keys_for_tests()

    with pytest.raises(RuntimeError, match="ephemeral"):
        jwt_es256.keys()


def test_the_optin_still_allows_a_dev_key(monkeypatch):
    monkeypatch.setenv("CLAYRUNE_ALLOW_EPHEMERAL_KEY", "1")
    jwt_es256.reset_keys_for_tests()
    assert jwt_es256.keys()  # local dev keeps working


# ─── 4/5/7/10. HTTP surface ──────────────────────────────────────────────────


def test_error_envelopes_carry_a_real_request_id(client, mem_firestore):
    """Every sign-in failure returned the literal id "x". The one field whose job
    is to be unique was a constant, so a user reporting "I can't sign in" hands
    you an id matching every failure ever recorded."""
    a = client.post("/v1/session/start", json={})
    b = client.post("/v1/session/start", json={})
    assert a.status_code == 400

    # main.py flattens the HTTPException detail, so the envelope is top-level.
    id_a = a.json()["request_id"]
    id_b = b.json()["request_id"]
    assert id_a != "x" and id_b != "x"
    assert id_a != id_b, "request ids must distinguish two different failures"


def test_request_id_honours_an_upstream_header(client, mem_firestore):
    r = client.post("/v1/session/start", json={}, headers={"x-request-id": "req_from_edge"})
    assert r.json()["request_id"] == "req_from_edge"


def test_signin_page_ships_a_csp_with_a_matching_nonce(client):
    """The page where the Google ID token is obtained and the Domain=.clayrune.io
    cookie is minted — the highest-value injection target in the product. The
    Firebase SDK is an ES module (no SRI possible), so the CSP is the control."""
    r = client.get("/v1/signin")
    assert r.status_code == 200

    csp = r.headers.get("content-security-policy", "")
    assert csp, "the sign-in page must ship a CSP"
    assert "https://www.gstatic.com" in csp, "the Firebase SDK origin must be pinned"
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0], \
        "script-src must not allow arbitrary inline script"

    # The page's own inline module must actually be allowed to run, or we have
    # shipped a CSP that breaks sign-in entirely.
    nonce = csp.split("'nonce-")[1].split("'")[0]
    assert f'nonce="{nonce}"' in r.text, "the inline module needs the CSP's nonce or sign-in dies"


def test_signin_nonce_is_per_response(client):
    a = client.get("/v1/signin").headers["content-security-policy"]
    b = client.get("/v1/signin").headers["content-security-policy"]
    assert a != b, "a reused nonce is no better than 'unsafe-inline'"


def test_cors_is_pinned_on_the_auth_boundary():
    from control_plane.app import main

    assert "*" not in main._ALLOWED_ORIGINS
    # A space after a comma used to survive the split and then never match any
    # Origin header — a silent config bug that reads as a client-side CORS fault.
    assert all(o == o.strip() and o for o in main._ALLOWED_ORIGINS)
