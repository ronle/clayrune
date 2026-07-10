"""Web push + dashboard presence + mobile pairing — blueprint 1.2.

Moved VERBATIM from server.py (MODERNIZATION_PLAN.md Phase 1). Scope: the
7 /api/push routes, 6 /api/mobile-pair routes, and /api/presence (the
presence gate exists solely as push focus-suppression, so it travels with
push — +1 route vs the plan table, same 209 total). The only symbol the
rest of server.py consumes is _handle_push_signal (stream readers); it is
shim-imported there. Path constants + the two projects-family reads are
late-bound via wire() until their families extract.
"""

import json
import os
import threading
import time as _time
import uuid
from pathlib import Path
from typing import Any, Callable

from flask import Blueprint, jsonify, request

from mc import state
from mc.core import _harden_secret_perms, _log
from mc.state import (
    PRESENCE_FRESH_SEC,
    agent_sessions,
    _presence_lock,
    _presence_state,
    _push_state_lock,
)

bp = Blueprint('push_mobile', __name__)

# ── wired by server.py (see wire()) ──────────────────────────────────────────
load_project: Callable[[str], Any] = None  # type: ignore[assignment]
_cf_session_nonce_from_request: Callable[[], str] = None  # type: ignore[assignment]
_get_remote_provider: Callable[[], Any] = None  # type: ignore[assignment]


def wire(*, data_root, load_project_fn, cf_session_nonce_fn, get_remote_provider_fn):
    """Late-bind cross-family deps (called once from server.py before
    register_blueprint). data_root → the four storage paths; load_project →
    projects family (1.11); CF session nonce → remote family (1.7)."""
    global PUSH_VAPID_PATH, PUSH_SUBS_PATH, PUSH_FCM_KEY_PATH, MOBILE_PAIRING_PATH
    global PUSH_NOTIF_PATH
    global load_project, _cf_session_nonce_from_request, _get_remote_provider
    PUSH_VAPID_PATH = data_root / 'data' / 'push_vapid.json'
    PUSH_SUBS_PATH = data_root / 'data' / 'push_subscriptions.json'
    PUSH_FCM_KEY_PATH = data_root / 'data' / 'firebase_admin.json'
    MOBILE_PAIRING_PATH = data_root / 'data' / 'mobile_pairing.json'
    # Cross-project notification timeline backing the mobile Inbox. NOT under
    # data/projects/ (that dir is the project-records store — a stray file there
    # 500s load_projects; see CLAUDE.md DATA_DIR pollution rule).
    PUSH_NOTIF_PATH = data_root / 'data' / 'notifications.json'
    load_project = load_project_fn
    _cf_session_nonce_from_request = cf_session_nonce_fn
    _get_remote_provider = get_remote_provider_fn


# ── Web push notifications ──────────────────────────────────────────────────
# Browser / PWA push delivery via VAPID. When Claude calls the
# `PushNotification` tool inside an MC-managed session (intercepted from
# stream-json in `_read_agent_stream*`), or when a turn completes for a
# project with `notify_turn_complete=True`, we encrypt + sign a notification
# and deliver it through the browser's push service (FCM / Mozilla / APNs).
# Tapping the notification opens clayrune.io routed to the originating
# session so the user can reply via the existing `/agent/send` endpoint.
#
# Subscriptions are keyed by the CF Access session nonce so they get cleaned
# up alongside revoked CF sessions; non-CF (local) subscribers fall back to
# an endpoint-hash key.

PUSH_VAPID_PATH: Path = None  # type: ignore[assignment]  # wired from _DATA_ROOT
PUSH_SUBS_PATH: Path = None  # type: ignore[assignment]  # wired from _DATA_ROOT

# _push_state_lock / _presence_state / _presence_lock / PRESENCE_FRESH_SEC
# come from mc.state (Phase 0).


# ── Dashboard presence (push focus-suppression gate) ─────────────────────────
# _presence_state / _presence_lock / PRESENCE_FRESH_SEC moved to mc/state.py
# (Phase 0); design rationale documented there.


def _presence_touch(project_id: str, session_id: str) -> None:
    if not project_id or not session_id:
        return
    with _presence_lock:
        _presence_state[(project_id, session_id)] = _time.time()


def _is_being_watched(project_id: str, session_id: str) -> bool:
    """True iff a dashboard has this session's chat open + focused right now."""
    if not project_id or not session_id:
        return False
    with _presence_lock:
        ts = _presence_state.get((project_id, session_id), 0.0)
    return (_time.time() - ts) < PRESENCE_FRESH_SEC


def _load_vapid_keys() -> dict:
    """Return the VAPID keypair, generating + persisting one if missing.

    Private key is stored as the raw 32-byte EC scalar, base64url-encoded.
    `pywebpush.webpush(vapid_private_key=…)` routes through
    `py_vapid.Vapid01.from_string`, which auto-detects RAW (32 bytes after
    decode) vs DER (longer) — but does NOT strip PEM `BEGIN/END` lines, so
    storing the full PEM here would fail signature generation at delivery
    time. Raw is the simplest format that works.
    """
    needs_persist = False
    d = None
    try:
        with open(PUSH_VAPID_PATH, 'r', encoding='utf-8') as f:
            d = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        d = None

    # Migration: an earlier build stored the private key as full PEM, which
    # py_vapid can't parse via from_string. Detect and convert to raw on load
    # so the SAME keypair (and therefore the same public key already shared
    # with subscribed browsers) keeps working — no resubscribe required.
    if isinstance(d, dict) and d.get('public') and d.get('private'):
        priv = d['private']
        if isinstance(priv, str) and priv.startswith('-----BEGIN'):
            try:
                import base64
                from cryptography.hazmat.primitives import serialization
                key = serialization.load_pem_private_key(
                    priv.encode(), password=None,
                )
                priv_int = key.private_numbers().private_value  # pyright: ignore[reportAttributeAccessIssue]  # moved-verbatim typing debt (1.2)
                priv_raw = priv_int.to_bytes(32, 'big')
                d['private'] = base64.urlsafe_b64encode(priv_raw).decode().rstrip('=')
                needs_persist = True
                _log('[push] migrated VAPID private key from PEM to raw format', flush=True)
            except Exception as e:
                _log(f"[push] VAPID PEM migration failed: {e}; regenerating", flush=True)
                d = None  # fall through to regen
        if d is not None:
            if not needs_persist:
                return d

    if d is None:
        try:
            import base64
            from py_vapid import Vapid01
            from cryptography.hazmat.primitives import serialization
            v = Vapid01()
            v.generate_keys()
            pub_bytes = v.public_key.public_bytes(  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.2)
                encoding=serialization.Encoding.X962,
                format=serialization.PublicFormat.UncompressedPoint,
            )
            public_b64 = base64.urlsafe_b64encode(pub_bytes).decode().rstrip('=')
            priv_int = v.private_key.private_numbers().private_value
            priv_raw = priv_int.to_bytes(32, 'big')
            private_b64 = base64.urlsafe_b64encode(priv_raw).decode().rstrip('=')
            d = {'public': public_b64, 'private': private_b64, 'created_at': int(_time.time())}
            needs_persist = True
        except Exception as e:
            _log(f"[push] VAPID generation failed: {e}", flush=True)
            return {}

    if needs_persist:
        PUSH_VAPID_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = PUSH_VAPID_PATH.with_suffix('.json.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, PUSH_VAPID_PATH)
        _harden_secret_perms(PUSH_VAPID_PATH)
    return d


def _load_push_subscriptions() -> dict:
    try:
        with open(PUSH_SUBS_PATH, 'r', encoding='utf-8') as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_push_subscriptions(d: dict) -> None:
    PUSH_SUBS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PUSH_SUBS_PATH.with_suffix('.json.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
    os.replace(tmp, PUSH_SUBS_PATH)


# ── Notification timeline (mobile Inbox source of truth) ─────────────────────
# Every intended push (kind 'agent' | 'turn_complete') is appended here so the
# Inbox is exactly "what pushed", by construction. Rolling history: bounded by
# both age and count so the file can't grow unbounded. Best-effort — a log
# failure never breaks push delivery. Guarded by its own lock; atomic writes.
PUSH_NOTIF_PATH: Path = None  # type: ignore[assignment]  # wired from _DATA_ROOT
_NOTIF_MAX_AGE_SEC = 30 * 24 * 3600   # keep 30 days of history
_NOTIF_MAX_ITEMS = 1000               # hard cap regardless of age
_notif_lock = threading.Lock()


def _load_notifications() -> list:
    try:
        with open(PUSH_NOTIF_PATH, 'r', encoding='utf-8') as f:
            d = json.load(f)
            return d if isinstance(d, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    except Exception as e:
        _log(f"[notif] load failed: {e}", flush=True)
        return []


def _save_notifications(items: list) -> None:
    PUSH_NOTIF_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PUSH_NOTIF_PATH.with_suffix('.json.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False)
    os.replace(tmp, PUSH_NOTIF_PATH)


def _prune_notifications(items: list) -> list:
    """Drop items older than the age window, then cap to the newest N."""
    cutoff = int(_time.time()) - _NOTIF_MAX_AGE_SEC
    kept = [n for n in items if (n.get('ts') or 0) >= cutoff]
    kept.sort(key=lambda n: n.get('ts') or 0, reverse=True)  # newest first
    return kept[:_NOTIF_MAX_ITEMS]


def _append_notification(*, title: str, body: str, url: str, kind: str,
                         project_id: str, session_id: str) -> None:
    """Append one Inbox event. Best-effort — never raises to the caller."""
    if PUSH_NOTIF_PATH is None:
        return
    try:
        p = load_project(project_id) if (load_project and project_id) else None
        project_name = (p or {}).get('name') or project_id or 'Clayrune'
        item = {
            'id': uuid.uuid4().hex,
            'ts': int(_time.time()),
            'project_id': project_id or '',
            'project_name': project_name,
            'title': (title or '')[:120],
            'body': (body or '')[:280],
            'url': url or '',
            'kind': kind or 'agent',
            'session_id': session_id or '',
            'read': False,
        }
        with _notif_lock:
            items = _load_notifications()
            items.insert(0, item)
            _save_notifications(_prune_notifications(items))
    except Exception as e:
        _log(f"[notif] append failed: {e}", flush=True)


def _push_subject() -> str:
    """VAPID `sub` claim. Must be mailto: or https: URL."""
    email = (state.CONFIG.get('user_email') or '').strip()
    if email:
        return f'mailto:{email}'
    return 'mailto:push@clayrune.io'


# ── FCM (native Android shell) ───────────────────────────────────────────────
# Web push delivers to browsers via VAPID; native push to the io.clayrune.app
# APK shell goes through Firebase Cloud Messaging. Both subscription types
# live in the same push_subscriptions.json store and are routed in
# _notify_push() based on sub['type'] (default '' / 'web' for web push,
# 'fcm' for native).

PUSH_FCM_KEY_PATH: Path = None  # type: ignore[assignment]  # wired from _DATA_ROOT
# state._fcm_app / state._fcm_init_error live in mc/state.py (rebound globals — Phase 0
# deferred them; 1.2 moves them with their rebind sites rewritten to state.*).


def _fcm_initialize():
    """Lazy-init the firebase_admin SDK using data/firebase_admin.json.
    Returns the App on success, None on failure. Caches both outcomes so
    repeated calls don't re-attempt initialization on a broken setup.
    """
    if state._fcm_app is not None:
        return state._fcm_app
    if state._fcm_init_error is not None:
        return None
    try:
        if not PUSH_FCM_KEY_PATH.exists():
            state._fcm_init_error = 'firebase_admin.json missing'
            return None
        import firebase_admin
        from firebase_admin import credentials
        cred = credentials.Certificate(str(PUSH_FCM_KEY_PATH))
        state._fcm_app = firebase_admin.initialize_app(cred, name='clayrune-fcm')
        _log('[push/fcm] firebase_admin initialized', flush=True)
        return state._fcm_app
    except Exception as e:
        state._fcm_init_error = f'{type(e).__name__}: {e}'
        _log(f'[push/fcm] init failed: {state._fcm_init_error}', flush=True)
        return None


def _push_send_fcm(sub: dict, payload: dict) -> tuple[bool, str, bool]:
    """Deliver one FCM push. Returns (ok, error_str, drop_subscription).

    Uses a data-only message so the app's FirebaseMessagingService renders
    the notification itself with deep-link routing extras. drop_subscription
    is True iff FCM reports the token is invalid/unregistered.
    """
    app_ = _fcm_initialize()
    if app_ is None:
        return False, f'fcm_init: {state._fcm_init_error}', False
    token = sub.get('token') or ''
    if not token:
        return False, 'no_token', True
    try:
        from firebase_admin import messaging, exceptions as fa_exc
    except Exception as e:
        return False, f'fcm_import: {e}', False
    try:
        # Hybrid payload:
        #   notification block — auto-displays in system tray when app is
        #     killed or backgrounded (Android handles rendering).
        #   data block — survives tap-through with deep-link extras; also
        #     used by Capacitor plugin's pushNotificationReceived event
        #     when the app is in foreground (system tray suppressed).
        # All `data` values must be strings.
        data = {k: str(v) for k, v in payload.items() if v is not None}
        msg = messaging.Message(
            token=token,
            notification=messaging.Notification(
                title=payload.get('title') or 'Clayrune',
                body=payload.get('body') or '',
            ),
            data=data,
            android=messaging.AndroidConfig(
                priority='high',
                ttl=300,
                notification=messaging.AndroidNotification(
                    # Tag groups successive notifications for the same
                    # project so a chatty agent doesn't carpet-bomb the tray.
                    tag=f"clayrune-{payload.get('project_id', '')}",
                ),
            ),
        )
        messaging.send(msg, app=app_)
        return True, '', False
    except fa_exc.NotFoundError:
        # UNREGISTERED — token is permanently invalid; drop the sub.
        return False, 'unregistered', True
    except fa_exc.InvalidArgumentError as e:
        # Malformed token / payload — also unrecoverable.
        return False, f'invalid: {e}', True
    except Exception as e:
        return False, f'{type(e).__name__}: {e}', False


def _notify_push(title: str, body: str, *, url: str = '',
                 project_id: str = '', session_id: str = '',
                 kind: str = 'agent', log_inbox: bool = True) -> dict:
    """Deliver a push notification to every subscribed device that opted in
    for this `kind` (`'agent'` for PushNotification tool, `'turn_complete'`
    for end-of-turn). Removes 404/410 subscriptions automatically.

    Dispatches per-subscription based on `sub['type']`:
      'fcm' → Firebase Cloud Messaging (native Android shell)
      else  → Web push via VAPID (browsers, PWA)

    `log_inbox` records the event to the notification timeline (the Inbox)
    regardless of whether a device is subscribed / delivery succeeds — so the
    Inbox is a complete history of what pushed. Set False for test pushes.
    """
    # Record BEFORE the delivery early-returns so the Inbox captures the event
    # even with no subscribers / no VAPID key configured yet.
    if log_inbox:
        _append_notification(title=title, body=body, url=url, kind=kind,
                             project_id=project_id, session_id=session_id)
    try:
        from pywebpush import webpush, WebPushException
    except Exception as e:
        return {'ok': False, 'error': f'pywebpush_missing: {e}'}
    keys = _load_vapid_keys()
    if not keys.get('private'):
        return {'ok': False, 'error': 'no_vapid_key'}
    subs = _load_push_subscriptions()
    if not subs:
        return {'ok': False, 'error': 'no_subscribers'}
    payload_dict = {
        'title': (title or '')[:120],
        'body': (body or '')[:280],
        'url': url or '/',
        'project_id': project_id,
        'session_id': session_id,
        'kind': kind,
        'ts': int(_time.time()),
    }
    payload = json.dumps(payload_dict)
    sent, failed, removed = 0, 0, []
    last_error = None
    for nonce, sub in list(subs.items()):
        if not isinstance(sub, dict):
            continue
        if kind == 'agent' and not sub.get('notify_agent_push', True):
            continue
        # No per-subscription opt-out for turn_complete: "waiting for me" is
        # THE policy (Ron, 2026-05-16) and has no per-device UI. Control lives
        # at the project level (notify_turn_complete / notify_push_enabled) +
        # the presence focus-suppression gate. A legacy stored
        # notify_turn_complete=False on a sub (set when this was opt-in) must
        # NOT silently swallow the policy — that was the no-push bug.
        pf = sub.get('project_filter')
        if pf and project_id and pf != project_id:
            continue
        sub_type = sub.get('type', '') or ''

        if sub_type == 'fcm':
            ok, err, drop = _push_send_fcm(sub, payload_dict)
            if ok:
                sub['last_used_at'] = int(_time.time())
                sent += 1
            else:
                if drop:
                    removed.append(nonce)
                else:
                    failed += 1
                    last_error = err
                    _log(f"[push/fcm] delivery failed for {nonce[:12]}…: {err}", flush=True)
            continue

        # Web push (default)
        sub_info = {
            'endpoint': sub.get('endpoint'),
            'keys': sub.get('keys', {}),
        }
        if not sub_info['endpoint']:
            continue
        try:
            webpush(
                subscription_info=sub_info,
                data=payload,
                vapid_private_key=keys['private'],
                vapid_claims={'sub': _push_subject()},
                ttl=300,
            )
            sub['last_used_at'] = int(_time.time())
            sent += 1
        except WebPushException as e:
            resp = getattr(e, 'response', None)
            code = resp.status_code if resp is not None else 0
            if code in (404, 410):
                removed.append(nonce)
            else:
                failed += 1
                detail = (resp.text[:200] if resp is not None and resp.text else str(e))
                last_error = f'code={code} {detail}'
                _log(f"[push] delivery failed for {nonce[:12]}…: code={code} {e} body={detail}", flush=True)
        except Exception as e:
            failed += 1
            last_error = f'{type(e).__name__}: {e}'
            _log(f"[push] unexpected error for {nonce[:12]}…: {e}", flush=True)
    if removed:
        for n in removed:
            subs.pop(n, None)
        _log(f"[push] removed {len(removed)} stale subscription(s)", flush=True)
    _save_push_subscriptions(subs)
    return {
        'ok': True, 'sent': sent, 'failed': failed, 'removed': len(removed),
        'last_error': last_error,
    }


def _handle_push_signal(project_id: str, session_id: str, msg: dict) -> None:
    """Fired from stream readers on each parsed stream-json message.

    - assistant + tool_use(PushNotification) → fire `kind='agent'` push.
    - result                                  → fire `kind='turn_complete'`
      push iff the project opted in.

    Wrapped in a broad try so a delivery problem never breaks the reader.
    """
    try:
        # Never notify for internal/background work or private sessions —
        # scribe, condense, hivemind workers/orchestrator all set
        # housekeeping=True; incognito sessions opt out of all signals.
        s = agent_sessions.get(session_id) or {}
        if s.get('housekeeping') or s.get('incognito'):
            return
        msg_type = msg.get('type', '')
        if msg_type == 'assistant' and 'message' in msg:
            for block in msg['message'].get('content', []):
                if (block.get('type') == 'tool_use'
                        and block.get('name') == 'PushNotification'):
                    text = (block.get('input') or {}).get('message') or ''
                    if not text:
                        continue
                    p = load_project(project_id) or {}
                    if not p.get('notify_push_enabled', True):
                        continue
                    # Focus-suppression: user is already watching this chat.
                    if _is_being_watched(project_id, session_id):
                        continue
                    title = (p.get('name') or 'Clayrune')[:60]
                    target = f'/?project={project_id}&session={session_id}'
                    _notify_push(title, text, url=target,
                                 project_id=project_id, session_id=session_id,
                                 kind='agent')
        elif msg_type == 'result':
            p = load_project(project_id) or {}
            if not p.get('notify_push_enabled', True):
                return
            # "Waiting for me" policy: turn-complete push is ON by default;
            # a project may still explicitly opt out (notify_turn_complete=False).
            if not p.get('notify_turn_complete', True):
                return
            # Focus-suppression: don't buzz for a chat the user is watching.
            if _is_being_watched(project_id, session_id):
                return
            title = (p.get('name') or 'Clayrune')[:60]
            target = f'/?project={project_id}&session={session_id}'
            # Use the agent's actual closing message as the body (the
            # stream-json `result` field carries the final assistant text —
            # same content the chat renders). Collapse whitespace so the
            # notification preview is clean; _notify_push caps to 280 chars.
            # Fall back to the static phrase only when there's no text.
            rt = msg.get('result')
            body = ' '.join(rt.split()).strip() if isinstance(rt, str) else ''
            if not body:
                body = 'Waiting for you'
            _notify_push(title, body, url=target,
                         project_id=project_id, session_id=session_id,
                         kind='turn_complete')
    except Exception as e:
        _log(f"[push] _handle_push_signal error: {e}", flush=True)


# Endpoints ──────────────────────────────────────────────────────────────────
@bp.route('/api/push/vapid-public-key')
def push_vapid_public_key():
    keys = _load_vapid_keys()
    return jsonify({'ok': True, 'public_key': keys.get('public', '')})


@bp.route('/api/push/subscribe', methods=['POST'])
def push_subscribe():
    body = request.get_json(silent=True) or {}
    endpoint = body.get('endpoint') or ''
    keys = body.get('keys') or {}
    if not endpoint or not isinstance(keys, dict) or not keys.get('p256dh') or not keys.get('auth'):
        return jsonify({'ok': False, 'error': 'invalid_subscription'}), 400
    nonce = _cf_session_nonce_from_request()
    if not nonce:
        import hashlib
        nonce = 'local:' + hashlib.sha1(endpoint.encode()).hexdigest()[:16]
    label = (body.get('label') or '').strip() or 'Device'
    ua = request.headers.get('User-Agent', '')
    with _push_state_lock:
        subs = _load_push_subscriptions()
        # Dedup-by-endpoint: the browser's PushSubscription.endpoint is stable
        # across CF Access re-OTPs (which change the nonce). If we already have
        # a record with this same endpoint under a different nonce, migrate it
        # (preserve user prefs, drop the stale nonce key). This prevents
        # orphaned subs accumulating every time the CF session expires.
        existing = subs.get(nonce) if isinstance(subs.get(nonce), dict) else {}
        if not existing:
            for k, v in list(subs.items()):
                if k != nonce and isinstance(v, dict) and v.get('endpoint') == endpoint:
                    existing = v
                    subs.pop(k, None)
                    _log(f"[push] migrated subscription {k[:12]}… → {nonce[:12]}… (same endpoint, re-OTP)", flush=True)
                    break
        subs[nonce] = {
            'label': label[:80] if label != 'Device' else (existing.get('label') or label)[:80],  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.2)
            'ua': (ua or '')[:300],
            'endpoint': endpoint,
            'keys': {'p256dh': keys.get('p256dh'), 'auth': keys.get('auth')},
            'project_filter': body.get('project_filter') or existing.get('project_filter'),  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.2)
            'notify_agent_push': bool(body.get('notify_agent_push', existing.get('notify_agent_push', True))),  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.2)
            'notify_turn_complete': bool(body.get('notify_turn_complete', existing.get('notify_turn_complete', False))),  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.2)
            'created_at': existing.get('created_at') or int(_time.time()),  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.2)
            'last_used_at': existing.get('last_used_at') or 0,  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.2)
        }
        _save_push_subscriptions(subs)
    return jsonify({'ok': True, 'nonce': nonce, 'label': label})


@bp.route('/api/push/register-fcm', methods=['POST'])
def push_register_fcm():
    """Register or refresh a Firebase Cloud Messaging token from the native
    Android shell. Body: { token, label?, project_filter?, notify_agent_push?,
    notify_turn_complete? }. Token rotation is handled by storing keyed on
    a hash of the token (stable per device) — re-registers under the same
    key migrate the row.
    """
    body = request.get_json(silent=True) or {}
    token = (body.get('token') or '').strip()
    if not token or len(token) > 4096:
        return jsonify({'ok': False, 'error': 'invalid_token'}), 400
    # Storage key: prefer the CF nonce when present (lets us share lifecycle
    # with web push subs); fall back to a stable token hash otherwise.
    nonce = _cf_session_nonce_from_request()
    if not nonce:
        import hashlib
        nonce = 'fcm:' + hashlib.sha1(token.encode()).hexdigest()[:16]
    label = (body.get('label') or '').strip() or 'Android'
    ua = request.headers.get('User-Agent', '')
    with _push_state_lock:
        subs = _load_push_subscriptions()
        # Dedup by token: if the same FCM token already exists under a
        # different key (token-hash key vs. CF-nonce key, or older nonce),
        # migrate the row.
        existing = subs.get(nonce) if isinstance(subs.get(nonce), dict) else {}
        if not existing:
            for k, v in list(subs.items()):
                if k != nonce and isinstance(v, dict) and v.get('token') == token:
                    existing = v
                    subs.pop(k, None)
                    _log(f"[push/fcm] migrated subscription {k[:12]}… → {nonce[:12]}…", flush=True)
                    break
        subs[nonce] = {
            'type': 'fcm',
            'token': token,
            'label': label[:80] if label != 'Android' else (existing.get('label') or label)[:80],  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.2)
            'ua': (ua or '')[:300],
            'project_filter': body.get('project_filter') or existing.get('project_filter'),  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.2)
            'notify_agent_push': bool(body.get('notify_agent_push', existing.get('notify_agent_push', True))),  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.2)
            'notify_turn_complete': bool(body.get('notify_turn_complete', existing.get('notify_turn_complete', False))),  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.2)
            'created_at': existing.get('created_at') or int(_time.time()),  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.2)
            'last_used_at': existing.get('last_used_at') or 0,  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.2)
        }
        _save_push_subscriptions(subs)
    return jsonify({'ok': True, 'nonce': nonce, 'label': label, 'type': 'fcm'})


@bp.route('/api/push/unsubscribe', methods=['POST'])
def push_unsubscribe():
    body = request.get_json(silent=True) or {}
    nonce = body.get('nonce') or ''
    endpoint = body.get('endpoint') or ''
    token = body.get('token') or ''
    with _push_state_lock:
        subs = _load_push_subscriptions()
        if nonce and nonce in subs:
            subs.pop(nonce, None)
        elif endpoint:
            for k, v in list(subs.items()):
                if isinstance(v, dict) and v.get('endpoint') == endpoint:
                    subs.pop(k, None)
                    break
        elif token:
            for k, v in list(subs.items()):
                if isinstance(v, dict) and v.get('token') == token:
                    subs.pop(k, None)
                    break
        _save_push_subscriptions(subs)
    return jsonify({'ok': True})


@bp.route('/api/push/subscriptions')
def push_subscriptions_list():
    subs = _load_push_subscriptions()
    out = []
    for nonce, s in subs.items():
        if not isinstance(s, dict):
            continue
        out.append({
            'nonce': nonce,
            'label': s.get('label', ''),
            'ua': s.get('ua', ''),
            'type': s.get('type', '') or 'web',
            'created_at': s.get('created_at', 0),
            'last_used_at': s.get('last_used_at', 0),
            'project_filter': s.get('project_filter'),
            'notify_agent_push': bool(s.get('notify_agent_push', True)),
            # Display-only; the per-sub turn_complete gate was removed
            # 2026-05-16 (delivery now ignores this field). Default True so
            # the list view reflects the actual "waiting for me" policy.
            'notify_turn_complete': bool(s.get('notify_turn_complete', True)),
        })
    out.sort(key=lambda x: x.get('last_used_at', 0), reverse=True)
    return jsonify({'ok': True, 'subscriptions': out})


@bp.route('/api/push/subscription/<nonce>', methods=['PATCH'])
def push_subscription_update(nonce):
    body = request.get_json(silent=True) or {}
    with _push_state_lock:
        subs = _load_push_subscriptions()
        if nonce not in subs or not isinstance(subs[nonce], dict):
            return jsonify({'ok': False, 'error': 'not_found'}), 404
        s = subs[nonce]
        if 'label' in body:
            s['label'] = str(body['label'])[:80]
        if 'project_filter' in body:
            s['project_filter'] = body['project_filter'] or None
        if 'notify_agent_push' in body:
            s['notify_agent_push'] = bool(body['notify_agent_push'])
        if 'notify_turn_complete' in body:
            s['notify_turn_complete'] = bool(body['notify_turn_complete'])
        _save_push_subscriptions(subs)
    return jsonify({'ok': True})


@bp.route('/api/push/test', methods=['POST'])
def push_test():
    """Send a test push to every subscribed device.

    Optional body fields:
      title / message — payload text
      url             — deep-link the tap should resolve to (defaults to '/')
      project_id      — alternative to `url`: builds /?project=<>&session=<>
      session_id      — paired with project_id
    """
    body = request.get_json(silent=True) or {}
    title = (body.get('title') or 'Clayrune test').strip()
    msg = (body.get('message') or 'Push notifications are working.').strip()
    pid = (body.get('project_id') or '').strip()
    sid = (body.get('session_id') or '').strip()
    url = (body.get('url') or '').strip()
    if not url and pid:
        url = f'/?project={pid}' + (f'&session={sid}' if sid else '')
    if not url:
        url = '/'
    result = _notify_push(title, msg, url=url, project_id=pid,
                          session_id=sid, kind='agent', log_inbox=False)
    return jsonify(result)


# ── Notification timeline (mobile Inbox) endpoints ───────────────────────────
@bp.route('/api/notifications')
def api_notifications():
    """The Inbox feed: newest-first, optional text search + unread filter,
    paginated. `unread` (count) is over the WHOLE store, not just this page."""
    items = _load_notifications()
    q = (request.args.get('q') or '').strip().lower()
    if q:
        def _hit(n):
            return q in (n.get('title', '') + ' ' + n.get('body', '')
                         + ' ' + n.get('project_name', '')).lower()
        items = [n for n in items if _hit(n)]
    unread = sum(1 for n in _load_notifications() if not n.get('read'))
    if request.args.get('unread') in ('1', 'true'):
        items = [n for n in items if not n.get('read')]
    try:
        limit = max(1, min(int(request.args.get('limit', 50)), 200))
        offset = max(0, int(request.args.get('offset', 0)))
    except Exception:
        limit, offset = 50, 0
    total = len(items)
    return jsonify({
        'items': items[offset:offset + limit],
        'total': total,
        'unread': unread,
        'offset': offset,
        'limit': limit,
    })


@bp.route('/api/notifications/read', methods=['POST'])
def api_notifications_read():
    """Mark notifications read. Body: {ids:[...]} or {all:true}."""
    body = request.get_json(silent=True) or {}
    ids = set(body.get('ids') or [])
    mark_all = bool(body.get('all'))
    with _notif_lock:
        items = _load_notifications()
        changed = 0
        for n in items:
            if (mark_all or n.get('id') in ids) and not n.get('read'):
                n['read'] = True
                changed += 1
        if changed:
            _save_notifications(items)
        unread = sum(1 for n in items if not n.get('read'))
    return jsonify({'ok': True, 'changed': changed, 'unread': unread})


@bp.route('/api/notifications/<nid>', methods=['DELETE'])
def api_notifications_delete(nid):
    """Dismiss one notification (removes the Inbox entry, not the chat)."""
    with _notif_lock:
        items = _load_notifications()
        kept = [n for n in items if n.get('id') != nid]
        removed = len(items) - len(kept)
        if removed:
            _save_notifications(kept)
        unread = sum(1 for n in kept if not n.get('read'))
    return jsonify({'ok': True, 'removed': removed, 'unread': unread})


@bp.route('/api/notifications/clear', methods=['POST'])
def api_notifications_clear():
    """Bulk clear. Body: {scope:'all'|'read'} (default 'all')."""
    scope = ((request.get_json(silent=True) or {}).get('scope') or 'all')
    with _notif_lock:
        items = _load_notifications()
        if scope == 'read':
            kept = [n for n in items if not n.get('read')]
        else:
            kept = []
        removed = len(items) - len(kept)
        _save_notifications(kept)
    return jsonify({'ok': True, 'removed': removed, 'unread': len(kept)})


# ── Mobile pairing (WhatsApp-style QR onboarding for the Android APK) ───────
# Stores the CF Access service-token credentials needed by the Clayrune
# Android shell to reach this MC instance. Configured ONCE on the desktop
# dashboard (user-friendly form, validated against the live tunnel), then
# served as a QR code that the APK's SetupActivity scans to auto-fill +
# verify + persist. Removes the need for non-operator users to fish service
# tokens out of the Cloudflare Zero Trust UI.
#
# Storage matches push_vapid.json / firebase_admin.json: plain JSON under
# data/, gitignored, no encryption-at-rest (the secret has to be readable in
# plaintext to render the QR — encryption with a colocated key is theatre).
# It lives in data/, NOT data/projects/, so load_projects() does not see it.

MOBILE_PAIRING_PATH: Path = None  # type: ignore[assignment]  # wired from _DATA_ROOT


def _load_mobile_pairing() -> dict:
    try:
        with open(MOBILE_PAIRING_PATH, 'r', encoding='utf-8') as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {}


def _save_mobile_pairing(d: dict) -> None:
    MOBILE_PAIRING_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = MOBILE_PAIRING_PATH.with_suffix('.json.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=2)
    tmp.replace(MOBILE_PAIRING_PATH)
    _harden_secret_perms(MOBILE_PAIRING_PATH)


def _mobile_pair_mask(secret: str) -> str:
    """Return a `••••abcd` style mask of the last 4 chars for display."""
    if not isinstance(secret, str) or len(secret) < 4:
        return '••••'
    return '••••' + secret[-4:]


def _mobile_pair_verify(tunnel_url: str, client_id: str,
                        client_secret: str) -> tuple[bool, str]:
    """Hit the tunnel root with CF service-token headers; success means the
    creds + URL combo actually authorise. Returns (ok, error_or_empty)."""
    import urllib.request
    import urllib.error
    if not tunnel_url or not client_id or not client_secret:
        return False, 'missing fields'
    url = tunnel_url.rstrip('/') + '/'
    req = urllib.request.Request(url, method='GET')
    req.add_header('CF-Access-Client-Id', client_id)
    req.add_header('CF-Access-Client-Secret', client_secret)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            code = resp.getcode()
            if code != 200:
                return False, f'HTTP {code} from tunnel'
            body = resp.read(4096).decode('utf-8', errors='replace')
            # The dashboard root returns the MC HTML — sanity-check we hit
            # MC and not a CF Access challenge / 200-but-wrong-content.
            if 'Clayrune' not in body and 'Mission Control' not in body:
                return False, 'tunnel returned 200 but body did not look like MC'
        return True, ''
    except urllib.error.HTTPError as e:
        return False, f'HTTP {e.code} ({e.reason})'
    except urllib.error.URLError as e:
        return False, f'connection failed: {e.reason}'
    except Exception as e:
        return False, f'unexpected error: {e}'


def _mobile_pair_uri(d: dict) -> str:
    """Compose the clayrune://pair?... URI the APK SetupActivity scans."""
    from urllib.parse import urlencode
    qs = urlencode({
        'v': '1',
        'u': d.get('tunnel_url') or '',
        'i': d.get('client_id') or '',
        's': d.get('client_secret') or '',
    })
    return f'clayrune://pair?{qs}'


@bp.route('/api/mobile-pair/config', methods=['GET'])
def mobile_pair_get():
    d = _load_mobile_pairing()
    if not d.get('tunnel_url') or not d.get('client_id') or not d.get('client_secret'):
        return jsonify({'configured': False})
    return jsonify({
        'configured': True,
        'tunnel_url': d['tunnel_url'],
        'client_id': d['client_id'],
        'client_secret_masked': _mobile_pair_mask(d['client_secret']),
        'pair_uri': _mobile_pair_uri(d),
        'updated_at': d.get('updated_at'),
    })


@bp.route('/api/mobile-pair/config', methods=['PUT'])
def mobile_pair_put():
    body = request.get_json(silent=True) or {}
    tunnel_url = (body.get('tunnel_url') or '').strip()
    client_id = (body.get('client_id') or '').strip()
    client_secret = (body.get('client_secret') or '').strip()
    skip_verify = bool(body.get('skip_verify'))
    if tunnel_url and not tunnel_url.startswith(('http://', 'https://')):
        tunnel_url = 'https://' + tunnel_url
    if not tunnel_url or not client_id or not client_secret:
        return jsonify({'ok': False, 'error': 'tunnel_url, client_id, client_secret required'}), 400
    if not skip_verify:
        ok, err = _mobile_pair_verify(tunnel_url, client_id, client_secret)
        if not ok:
            return jsonify({'ok': False, 'error': err}), 400
    d = {
        'tunnel_url': tunnel_url,
        'client_id': client_id,
        'client_secret': client_secret,
        'updated_at': _time.time(),
    }
    _save_mobile_pairing(d)
    return jsonify({
        'ok': True,
        'configured': True,
        'tunnel_url': tunnel_url,
        'client_id': client_id,
        'client_secret_masked': _mobile_pair_mask(client_secret),
        'pair_uri': _mobile_pair_uri(d),
        'updated_at': d['updated_at'],
    })


@bp.route('/api/mobile-pair/config', methods=['DELETE'])
def mobile_pair_delete():
    try:
        MOBILE_PAIRING_PATH.unlink()
    except FileNotFoundError:
        pass
    return jsonify({'ok': True, 'configured': False})


# ─── Auto-pair (Path B / control plane) ─────────────────────────────────────
#
# Sister flow to /api/mobile-pair/config (manual operator paste). When the
# user is Path B-enrolled the dashboard hides the manual form and uses these
# endpoints instead: MC asks the CP to mint a per-device CF service token
# and returns the QR URI. No CF dashboard, no service-token paste.
#
# The CF client_secret returned by the CP is the only thing that can pair
# the phone. It is NOT persisted server-side — the QR is shown once at
# creation, then forgotten. Re-pairing = revoke + create new. This matches
# CF's own "secret shown once" semantics and keeps data/mobile_pairing.json
# free of secrets we don't strictly need to hold.

def _mobile_pair_auto_uri(*, tunnel_url: str, client_id: str, client_secret: str) -> str:
    """Compose the clayrune://pair?... URI from auto-minted creds.

    Same scheme as _mobile_pair_uri (the manual flow) so the APK's
    SetupActivity sees one consistent payload format regardless of source.
    """
    from urllib.parse import urlencode
    qs = urlencode({
        'v': '1',
        'u': tunnel_url,
        'i': client_id,
        's': client_secret,
    })
    return f'clayrune://pair?{qs}'


def _mobile_pair_load_keystore_identity():
    """Return (this_device_id, auth_kwargs, error_dict|None). Centralises the
    keystore + dev-shim resolution shared by all three auto-pair endpoints."""
    try:
        from mc_remote import device_keys
    except Exception as e:
        return None, {}, {'error': 'import_error', 'message': str(e)}
    try:
        identity = device_keys.load_identity()
    except Exception:
        identity = None
    if not identity:
        # Dev-shim fallback for headless / pre-Firebase test installs.
        email = os.environ.get('MC_REMOTE_DEV_EMAIL', '').strip()
        if not email:
            return None, {}, {'error': 'not_enrolled',
                              'message': "Click 'Enable Remote Access' first."}
        return None, {'email': email}, None
    return identity.device_id, {
        'auth_device_id': identity.device_id,
        'enrollment_token': identity.enrollment_token,
    }, None


@bp.route('/api/mobile-pair/generate', methods=['POST'])
def mobile_pair_generate():
    """Mint a new per-device mobile-pairing token via the control plane.

    Body: {"label": "Ron's Pixel"} — free-form, shown in the dashboard list.

    Response (one-time — the client_secret is not retrievable later):
      { ok: true, token_id, label, hostname, pair_uri, client_id, created_at }
    """
    body = request.get_json(silent=True) or {}
    label = (body.get('label') or '').strip()[:48] or 'Mobile device'

    p = _get_remote_provider()
    if p is None:
        return jsonify({'error': 'no_provider',
                        'message': 'Remote access provider not configured.'}), 501

    this_device_id, auth_kwargs, err = _mobile_pair_load_keystore_identity()
    if err is not None:
        return jsonify(err), 503
    if not this_device_id:
        return jsonify({'error': 'not_enrolled',
                        'message': 'No keystore identity — finish Path B enrollment first.'}), 409

    try:
        from mc_remote import enrollment as _mc_enrollment, config
    except Exception as e:
        return jsonify({'error': 'import_error', 'message': str(e)}), 500

    body_out = _mc_enrollment.create_mobile_token_via_cp(
        cp_base_url=config.control_plane_base_url(),
        device_id=this_device_id,
        label=label,
        **auth_kwargs,  # pyright: ignore[reportArgumentType]  # moved-verbatim typing debt (1.2)
    )
    if body_out.get('error') or not body_out.get('ok'):
        status = body_out.get('status') or 502
        try: status = int(status)
        except Exception: status = 502
        return jsonify(body_out), status

    hostname = body_out.get('hostname') or ''
    client_id = body_out.get('client_id') or ''
    client_secret = body_out.get('client_secret') or ''
    if not (hostname and client_id and client_secret):
        return jsonify({'error': 'cp_incomplete_response',
                        'message': 'Control plane response missing creds.',
                        'cp_body': body_out}), 502

    tunnel_url = hostname if hostname.startswith(('http://', 'https://')) else 'https://' + hostname
    pair_uri = _mobile_pair_auto_uri(tunnel_url=tunnel_url, client_id=client_id,
                                     client_secret=client_secret)

    return jsonify({
        'ok': True,
        'token_id': body_out.get('token_id'),
        'cf_token_id': body_out.get('cf_token_id'),
        'label': body_out.get('label') or label,
        'hostname': hostname,
        'tunnel_url': tunnel_url,
        'client_id': client_id,
        'pair_uri': pair_uri,
        'created_at': body_out.get('created_at'),
    })


@bp.route('/api/mobile-pair/tokens', methods=['GET'])
def mobile_pair_tokens_list():
    """List the user's paired phones via the control plane."""
    p = _get_remote_provider()
    if p is None:
        return jsonify({'error': 'no_provider', 'tokens': []}), 501
    this_device_id, auth_kwargs, err = _mobile_pair_load_keystore_identity()
    if err is not None:
        return jsonify({**err, 'tokens': []}), 503
    if not this_device_id:
        return jsonify({'error': 'not_enrolled', 'tokens': []}), 409
    try:
        from mc_remote import enrollment as _mc_enrollment, config
    except Exception as e:
        return jsonify({'error': 'import_error', 'message': str(e), 'tokens': []}), 500
    return jsonify(_mc_enrollment.list_mobile_tokens_via_cp(
        cp_base_url=config.control_plane_base_url(),
        device_id=this_device_id,
        **auth_kwargs,  # pyright: ignore[reportArgumentType]  # moved-verbatim typing debt (1.2)
    ))


@bp.route('/api/mobile-pair/tokens/<token_id>', methods=['DELETE'])
def mobile_pair_token_delete(token_id):
    """Revoke a paired phone via the control plane."""
    p = _get_remote_provider()
    if p is None:
        return jsonify({'error': 'no_provider'}), 501
    this_device_id, auth_kwargs, err = _mobile_pair_load_keystore_identity()
    if err is not None:
        return jsonify(err), 503
    if not this_device_id:
        return jsonify({'error': 'not_enrolled'}), 409
    try:
        from mc_remote import enrollment as _mc_enrollment, config
    except Exception as e:
        return jsonify({'error': 'import_error', 'message': str(e)}), 500
    return jsonify(_mc_enrollment.delete_mobile_token_via_cp(
        cp_base_url=config.control_plane_base_url(),
        device_id=this_device_id,
        token_id=token_id,
        **auth_kwargs,  # pyright: ignore[reportArgumentType]  # moved-verbatim typing debt (1.2)
    ))


@bp.route('/api/presence', methods=['POST'])
def api_presence():
    """Heartbeat from a dashboard that has chat(s) open + visible + focused.

    Body: {"watching": [{"project_id": "..", "session_id": ".."}, ...]}.
    Each pair is timestamped; while fresh (< PRESENCE_FRESH_SEC) push for
    that session is suppressed (the user is already looking at it). The
    frontend stops pinging on blur/hide, so presence goes stale and push
    resumes automatically — no explicit "I left" signal needed.
    """
    body = request.get_json(silent=True) or {}
    watching = body.get('watching') or []
    n = 0
    if isinstance(watching, list):
        for w in watching:
            if not isinstance(w, dict):
                continue
            _presence_touch((w.get('project_id') or '').strip(),
                            (w.get('session_id') or '').strip())
            n += 1
    return jsonify({'ok': True, 'touched': n})


