# mc_remote_iface — remote-access provider contract

**Open source. Part of Clayrune core** — ships regardless of whether any
provider is installed.

A *remote-access provider* makes the local dashboard reachable from
outside the user's network. The reference implementation is `mc_remote`
(proprietary, talks to clayrune.io). Forks may ship their own provider
against Tailscale, ngrok, Cloudflare, their own infra, etc. This README
is the contract such a fork implements. Public-facing platform design
lives in `docs/remote-access/` (12 docs); this file is the *code seam*.

## The seam

```
Clayrune core (server.py)
        │  imports, never hard-deps on a provider
        ▼
mc_remote_iface           ← this package (open source, always present)
  ├─ provider.py    : RemoteAccessProvider Protocol + registry
  ├─ dev_stub.py    : fake provider driven by MC_DEV_REMOTE_STUB
  └─ __init__.py    : re-exports get_provider / register_provider / …
        ▲
        │  registers itself at import time
mc_remote (or your fork's provider)   ← proprietary / pluggable
```

`server.py` does `import mc_remote_iface` unconditionally, then
optionally imports a concrete provider; if none registers, every
remote-access endpoint degrades to a "no provider installed" CTA. No
provider ⇒ core still runs.

## Registration mechanism

A provider module **self-registers at import time**:

```python
from mc_remote_iface import register_provider
register_provider(MyProvider())     # MyProvider implements the Protocol
```

- Exactly **one** provider is supported. `register_provider` raises
  `RuntimeError` if a second, different provider registers (single-
  instance invariant — mirrors the one-MC-per-port rule).
- Core retrieves it via `get_provider() -> RemoteAccessProvider | None`.
- `clear_provider()` exists for tests / hot-reload only.

Discovery order in `server.py` (today):
1. `import mc_remote_iface` (always).
2. If `MC_DEV_REMOTE_STUB` is set → `dev_stub.maybe_register_dev_stub()`.
3. Else `import mc_remote` (proprietary; self-registers via its
   `__init__`). Absent ⇒ no provider, core unaffected.

## Required interface (`RemoteAccessProvider`, a `@runtime_checkable` Protocol)

All methods **must be safe to call from Flask request handlers** — fast,
non-blocking; delegate long work to background threads.

| Member | Kind | Contract |
|---|---|---|
| `name` | property → `str` | Human-readable, e.g. `"Clayrune Cloud"` |
| `vendor_url` | property → `str` | Where users sign up / learn more |
| `is_enabled()` | → `bool` | User has switched this provider on for this MC |
| `status()` | → `ProviderStatus` | Cheap, pollable runtime snapshot |
| `get_caps()` | → `ProviderCaps \| None` | Last reported quota/rate caps; `None` if unknown |
| `begin_enrollment()` | → `str` | Returns a URL for the frontend to open; does **not** open a browser; may start a short-lived callback listener |
| `disable()` | → `None` | Stop the tunnel, keep enrollment (cheap re-enable) |
| `resume()` | → `None` | Start tunnel for an enrolled device. **Idempotent**; raise if not enrolled |
| `disconnect_this_device()` | → `None` | Revoke device platform-side + clear local creds; re-enable needs fresh enrollment |

### DTOs

`ProviderStatus` (frozen): `enrolled, online, hostname, username,
last_seen, error_code, error_message, connecting`. `error_code` is one
of `docs/remote-access/error-codes` values or `None`.

`ProviderCaps` (frozen): `bandwidth_quota_period_bytes,
bandwidth_used_period_bytes, rate_limit_rps, max_response_bytes,
max_concurrent_connections`.

## Config / env surface

| Env var | Effect |
|---|---|
| `MC_DEV_REMOTE_STUB` | If set, registers `dev_stub` instead of a real provider. Values: `fresh` (not enrolled — matches a fork's "Coming Soon"), `offline` (enrolled, tunnel down), `online` (enrolled, up), `error` (enrolled, errored). |

The dev stub is the supported way to develop/test core's remote-access
UI without any real provider or network. It also lets fork authors see
the exact states core expects before writing a provider.

## Implementing a fork provider — checklist

1. Create a class satisfying `RemoteAccessProvider` (Protocol is
   `runtime_checkable`; `isinstance` checks structurally).
2. `register_provider(MyProvider())` at import time in your module's
   `__init__`.
3. Make `status()`/`get_caps()` cheap and non-blocking.
4. Map your errors onto `docs/remote-access/` error codes for consistent
   client-side localization.
5. Test against core by importing your module before first
   remote-access request; verify all four `MC_DEV_REMOTE_STUB` states
   render correctly, then swap in the real provider.

## Licensing

This package is MIT (Clayrune core). The reference provider `mc_remote`
and `mc_tunnel` are source-available proprietary (see their
`PROPRIETARY.md` and `docs/remote-access/07-licensing.md` §4, and the
license note in the repo root `README.md`). Forks implementing this
contract are first-class — that is the point of the seam.

— Cross-referenced from `README.md` and
`docs/remote-access/01-architecture.md` §3.1.
