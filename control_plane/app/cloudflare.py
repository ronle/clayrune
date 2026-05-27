"""Cloudflare API client.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

The control plane is the only entity that talks to the Cloudflare API.
Users' `mc-tunnel` processes never see CF credentials. See
`docs/remote-access/03-control-plane-api.md` §5.

Operations needed by /v1/enroll:
  - get_account_id() / get_zone_id()  — looked up once, cached
  - create_named_tunnel(name)         → (uuid, token)
  - set_tunnel_ingress(uuid, hostname, service_url)
  - create_dns_cname(hostname, target)→ dns_record_id
  - create_access_app(hostname, allowed_email) → app_id
  - add_access_app_policy(app_id, email)
Operations for revoke / username change:
  - delete_dns_record(record_id)
  - delete_access_app(app_id)
  - delete_tunnel(uuid)

Construction:
  - `CloudflareClient.from_env()` reads CLOUDFLARE_API_TOKEN, optionally
    CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_ZONE_ID. If account/zone are not
    set, they're discovered on first use via /accounts and /zones?name=...
  - For tests, pass a custom `httpx.AsyncClient` so the transport can be
    mocked: `CloudflareClient(token="x", account_id="acc", zone_id="zone",
                              client=httpx.AsyncClient(transport=MockTransport(...)))`.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)


CF_API_BASE = "https://api.cloudflare.com/client/v4"


# ─── Errors ──────────────────────────────────────────────────────────────────


class CloudflareAPIError(RuntimeError):
    """Non-2xx response or `success: false` body from Cloudflare."""

    def __init__(self, message: str, *, status: int, errors: Optional[list[dict]] = None,
                 endpoint: Optional[str] = None):
        super().__init__(message)
        self.status = status
        self.errors = errors or []
        self.endpoint = endpoint

    def __str__(self) -> str:
        suffix = f" [{self.endpoint}]" if self.endpoint else ""
        if self.errors:
            err_str = "; ".join(f"{e.get('code', '?')}: {e.get('message', '?')}" for e in self.errors)
            return f"{super().__str__()}{suffix} — {err_str}"
        return f"{super().__str__()}{suffix}"


# ─── Client ──────────────────────────────────────────────────────────────────


class CloudflareClient:
    """Async wrapper around the subset of the CF v4 API we need."""

    def __init__(
        self,
        *,
        token: str,
        account_id: Optional[str] = None,
        zone_id: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
        timeout: float = 15.0,
    ) -> None:
        if not token:
            raise ValueError("CloudflareClient requires a non-empty token")
        self._token = token
        self._account_id = account_id
        self._zone_id = zone_id
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=CF_API_BASE,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=timeout,
        )

    @classmethod
    def from_env(cls) -> "CloudflareClient":
        token = os.environ.get("CLOUDFLARE_API_TOKEN")
        if not token:
            raise RuntimeError(
                "CLOUDFLARE_API_TOKEN env var not set. Add it from GCP Secret Manager "
                "(see docs/remote-access/SETUP_CHECKLIST.md §6)."
            )
        return cls(
            token=token,
            account_id=os.environ.get("CLOUDFLARE_ACCOUNT_ID"),
            zone_id=os.environ.get("CLOUDFLARE_ZONE_ID"),
        )

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    # ─── HTTP helper with uniform error handling ──────────────────────────

    async def _call(self, method: str, path: str, *, json: Any = None,
                    params: Optional[dict] = None) -> Any:
        """Make a CF API call. Returns the `result` field; raises CloudflareAPIError on failure."""
        try:
            r = await self._client.request(method, path, json=json, params=params)
        except httpx.HTTPError as e:
            raise CloudflareAPIError(f"network error: {e}", status=0, endpoint=path) from e

        try:
            body = r.json() if r.content else {}
        except ValueError:
            body = {}

        if r.status_code >= 300 or not body.get("success", False):
            raise CloudflareAPIError(
                f"HTTP {r.status_code} from {method} {path}",
                status=r.status_code,
                errors=body.get("errors", []),
                endpoint=path,
            )

        return body.get("result")

    # ─── Account / zone discovery (lazy) ──────────────────────────────────

    async def get_account_id(self) -> str:
        if self._account_id:
            return self._account_id
        accounts = await self._call("GET", "/accounts")
        if not accounts:
            raise CloudflareAPIError("CF token has no account access", status=0,
                                     endpoint="/accounts")
        if len(accounts) > 1:
            log.warning("CF token has access to %d accounts; picking first (%s). "
                        "Set CLOUDFLARE_ACCOUNT_ID to disambiguate.",
                        len(accounts), accounts[0].get("id"))
        self._account_id = accounts[0]["id"]
        return self._account_id

    async def get_zone_id(self, zone_name: Optional[str] = None) -> str:
        if self._zone_id:
            return self._zone_id
        zone_name = zone_name or os.environ.get("CLAYRUNE_PRIMARY_ZONE", "clayrune.io")
        zones = await self._call("GET", "/zones", params={"name": zone_name})
        if not zones:
            raise CloudflareAPIError(
                f"zone {zone_name!r} not found in this CF account", status=0, endpoint="/zones",
            )
        self._zone_id = zones[0]["id"]
        return self._zone_id

    # ─── Tunnel ───────────────────────────────────────────────────────────

    async def create_named_tunnel(self, name: str) -> dict:
        """Create a CF named tunnel. Returns {id, token, ...}.

        `token` is what cloudflared needs (passed via `--token`). It encodes
        the tunnel UUID + secret + account info and is what gets stored on
        the device for the tunnel's lifetime.
        """
        acc = await self.get_account_id()
        result = await self._call(
            "POST", f"/accounts/{acc}/cfd_tunnel",
            json={"name": name, "config_src": "cloudflare"},
        )
        # CF returns id + secret in `result`; the runnable token is fetched separately
        token = await self._call("GET", f"/accounts/{acc}/cfd_tunnel/{result['id']}/token")
        # `token` is a base64 string (raw, not in a dict)
        result["token"] = token
        return result

    async def set_tunnel_ingress(self, tunnel_id: str, *, hostname: str,
                                 service_url: str = "http://localhost:5199") -> None:
        """Configure the tunnel's ingress to point hostname → service_url.

        Always appends a default `http_status:404` catch-all per CF conventions.
        """
        acc = await self.get_account_id()
        await self._call(
            "PUT", f"/accounts/{acc}/cfd_tunnel/{tunnel_id}/configurations",
            json={
                "config": {
                    "ingress": [
                        {"hostname": hostname, "service": service_url},
                        {"service": "http_status:404"},
                    ],
                }
            },
        )

    async def delete_tunnel(self, tunnel_id: str, *, cascade: bool = True) -> None:
        """Delete a tunnel. With cascade=True, kicks active connections first."""
        acc = await self.get_account_id()
        if cascade:
            try:
                await self._call("DELETE",
                                 f"/accounts/{acc}/cfd_tunnel/{tunnel_id}/connections")
            except CloudflareAPIError as e:
                # Not fatal — proceed to delete regardless
                log.warning("cleanup connections for tunnel %s failed: %s", tunnel_id, e)
        await self._call("DELETE", f"/accounts/{acc}/cfd_tunnel/{tunnel_id}")

    # ─── DNS ──────────────────────────────────────────────────────────────

    async def create_dns_cname(self, *, name: str, target_uuid: str,
                               proxied: bool = True) -> dict:
        """Create a proxied CNAME `<name>.<zone>` → `<target_uuid>.cfargotunnel.com`.

        `name` is the subdomain only (e.g. "ron"); CF appends the zone domain.
        Returns the created record dict (incl. `id`).
        """
        zone = await self.get_zone_id()
        return await self._call(
            "POST", f"/zones/{zone}/dns_records",
            json={
                "type": "CNAME",
                "name": name,
                "content": f"{target_uuid}.cfargotunnel.com",
                "proxied": proxied,
                "ttl": 1,  # auto when proxied
            },
        )

    async def delete_dns_record(self, record_id: str) -> None:
        zone = await self.get_zone_id()
        await self._call("DELETE", f"/zones/{zone}/dns_records/{record_id}")

    # ─── Access app + policy ──────────────────────────────────────────────

    async def create_access_app(self, *, hostname: str, allowed_email: str,
                                name: Optional[str] = None,
                                session_duration: str = "24h") -> dict:
        """Create a self-hosted Access app gating `hostname` to `allowed_email`.

        Creates the app + a single allow policy in two calls (CF API design).
        Returns the app dict (incl. `id`).
        """
        acc = await self.get_account_id()
        app = await self._call(
            "POST", f"/accounts/{acc}/access/apps",
            json={
                "name": name or f"Clayrune - {hostname}",
                "domain": hostname,
                "type": "self_hosted",
                "session_duration": session_duration,
                "auto_redirect_to_identity": False,
            },
        )

        try:
            await self._call(
                "POST", f"/accounts/{acc}/access/apps/{app['id']}/policies",
                json={
                    "name": "Owner",
                    "decision": "allow",
                    "include": [{"email": {"email": allowed_email}}],
                },
            )
        except CloudflareAPIError:
            # Roll back the orphan app to keep CF account tidy
            try:
                await self.delete_access_app(app["id"])
            except CloudflareAPIError:
                pass
            raise

        return app

    async def delete_access_app(self, app_id: str) -> None:
        acc = await self.get_account_id()
        await self._call("DELETE", f"/accounts/{acc}/access/apps/{app_id}")

    # ─── Service tokens (mobile pairing) ──────────────────────────────────
    #
    # Used by the mobile-pair flow: each phone gets its OWN service token
    # attached to the user's existing Access app via a dedicated Service Auth
    # policy. Per-device tokens mean revocation is per-phone (lose your
    # phone → revoke that one token; the others keep working). The CP holds
    # the only CF API credential; users never see CF.

    async def create_service_token(self, *, name: str,
                                   duration: str = "8760h") -> dict:
        """Create a CF Access service token. Returns
        {id, client_id, client_secret, name, duration, ...}.

        The `client_secret` is returned ONLY at creation — store it server-side
        if you need to surface it again. CF will never reveal it after this.
        `duration` is a Go duration string; 8760h = 1 year. Tokens auto-rotate
        but a long duration avoids forced re-pair if the device misses the
        rotation window.
        """
        acc = await self.get_account_id()
        return await self._call(
            "POST", f"/accounts/{acc}/access/service_tokens",
            json={"name": name, "duration": duration},
        )

    async def delete_service_token(self, token_id: str) -> None:
        """Permanently delete a service token. Any policy that includes it by
        ID will continue to exist but stop matching — caller is responsible
        for cleaning up the policy if it has no other includes."""
        acc = await self.get_account_id()
        await self._call("DELETE", f"/accounts/{acc}/access/service_tokens/{token_id}")

    async def add_service_token_policy(self, *, app_id: str, token_id: str,
                                       name: Optional[str] = None) -> dict:
        """Attach a service token to an existing Access app via a dedicated
        Service Auth policy. One policy per token (vs sharing one policy with
        many includes) so revoking a single device = deleting one policy +
        one token, no read-modify-write race on a shared list.

        Sets `session_duration: "0s"` — without this, CF Access issues a
        24h `CF_AppSession` cookie on the first authenticated request and
        future requests are authorized by the cookie alone, ignoring the
        underlying token. That means revoke wouldn't actually kick the phone
        out for up to 24h. `0s` means "no cached session — re-validate the
        token on every request"; the APK already sends headers per-request
        so there's no UX cost. Per-policy session_duration overrides the
        app-level default (which stays 24h for browser sessions).

        Returns the created policy dict (incl. `id`)."""
        acc = await self.get_account_id()
        return await self._call(
            "POST", f"/accounts/{acc}/access/apps/{app_id}/policies",
            json={
                "name": name or "Mobile device",
                "decision": "non_identity",
                "session_duration": "0s",
                "include": [{"service_token": {"token_id": token_id}}],
            },
        )

    async def delete_access_policy(self, *, app_id: str, policy_id: str) -> None:
        acc = await self.get_account_id()
        await self._call(
            "DELETE",
            f"/accounts/{acc}/access/apps/{app_id}/policies/{policy_id}",
        )

    # ─── Verify token (used by SETUP_CHECKLIST §5) ────────────────────────

    async def verify_token(self) -> dict:
        """Return CF's verification of our API token. Raises if invalid."""
        return await self._call("GET", "/user/tokens/verify")
