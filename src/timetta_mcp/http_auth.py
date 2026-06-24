"""Streamable-HTTP OAuth support so the MCP client drives the `Authenticate`
flow itself (e.g. the `/mcp` menu in Claude Code).

Timetta's IdP (`auth.timetta.com`, IdentityServer) is a plain OAuth provider
without Dynamic Client Registration and it does not know the redirect URIs the
MCP client uses. So instead of pointing the client straight at Timetta, this
server fronts it with FastMCP's :class:`OAuthProxy`: the proxy advertises the
RFC 9728 / RFC 8414 metadata the client needs, shims DCR, and bridges the
browser authorization-code + PKCE flow into Timetta's `external` public client.

The token the client ends up presenting is the upstream Timetta access token,
forwarded by the proxy. :func:`server.get_client` reads it back out of the
request context and calls the OData API with it. Because Timetta may issue
opaque (non-JWT) access tokens, the token is verified by *use* — a cheap probe
request against the OData API — rather than by JWKS/introspection, which a
public client cannot rely on.
"""

from __future__ import annotations

import ipaddress
import os
import time

import httpx

from fastmcp.server.auth import AccessToken, TokenVerifier

try:  # import path has moved across fastmcp releases; tolerate both
    from fastmcp.server.auth import OAuthProxy
except ImportError:  # pragma: no cover - depends on installed fastmcp version
    from fastmcp.server.auth.oauth_proxy import OAuthProxy

from .auth import get_auth_url, get_client_id
from .client import DEFAULT_BASE_URL

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
DEFAULT_PATH = "/mcp"
DEFAULT_SCOPES = "all offline_access"
# Probe endpoint used to validate a presented bearer. Overridable because the
# default entity set may differ between Timetta tenants.
DEFAULT_PROBE_PATH = "Users?$top=1"
_VERIFY_TTL = 300.0  # seconds to trust a previously validated token


def odata_base_url() -> str:
    return os.environ.get("TIMETTA_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def http_host() -> str:
    return os.environ.get("TIMETTA_MCP_HOST", DEFAULT_HOST)


def http_port() -> int:
    raw = os.environ.get("TIMETTA_MCP_PORT", str(DEFAULT_PORT))
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_PORT


def http_path() -> str:
    path = os.environ.get("TIMETTA_MCP_PATH", DEFAULT_PATH)
    return path if path.startswith("/") else f"/{path}"


def public_base_url() -> str:
    """Externally reachable base URL of this MCP server (no trailing slash).

    Used by OAuthProxy to build its own metadata and redirect URIs. Override
    with `TIMETTA_PUBLIC_URL` when the server sits behind a proxy/tunnel.
    """
    explicit = os.environ.get("TIMETTA_PUBLIC_URL")
    if explicit:
        return explicit.rstrip("/")
    return f"http://{http_host()}:{http_port()}"


class TimettaTokenVerifier(TokenVerifier):
    """Validate a Timetta bearer by using it against the OData API.

    A 200 from the probe endpoint means Timetta accepted the token; 401/403 mean
    it is invalid/expired. Results are cached briefly so verification does not add
    a round-trip to every MCP request.
    """

    def __init__(self, base_url: str, *, probe_path: str | None = None) -> None:
        super().__init__()
        self._base = base_url.rstrip("/")
        self._probe = probe_path or os.environ.get(
            "TIMETTA_AUTH_PROBE_PATH", DEFAULT_PROBE_PATH
        )
        self._cache: dict[str, float] = {}

    def __repr__(self) -> str:  # never leak token values
        return f"TimettaTokenVerifier(base_url={self._base!r})"

    def _cached_valid(self, token: str) -> bool:
        expires = self._cache.get(token)
        if expires is None:
            return False
        if expires <= time.time():
            self._cache.pop(token, None)
            return False
        return True

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token or not token.strip():
            return None
        if not self._cached_valid(token):
            try:
                async with httpx.AsyncClient(timeout=15.0) as c:
                    resp = await c.get(
                        f"{self._base}/{self._probe}",
                        headers={"Authorization": f"Bearer {token}"},
                    )
            except httpx.RequestError:
                return None
            if resp.status_code != 200:
                return None
            self._cache[token] = time.time() + _VERIFY_TTL
        return AccessToken(
            token=token,
            client_id="timetta",
            scopes=["all"],
            expires_at=None,
            claims={},
        )


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def is_loopback_host(host: str) -> bool:
    """True when `host` binds only the local machine (127.0.0.1, ::1, localhost)."""
    h = (host or "").strip().lower().strip("[]")
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False  # hostname / 0.0.0.0 / :: — treat as externally reachable


def build_oauth_proxy() -> OAuthProxy:
    """Construct the OAuthProxy fronting Timetta's IdP for the HTTP transport."""
    auth_url = get_auth_url()
    scopes = os.environ.get("TIMETTA_OAUTH_SCOPES", DEFAULT_SCOPES)
    # CIMD (Client ID Metadata Documents) lets clients like Claude Code present a
    # URL client id; FastMCP then fetches that document over the network. Behind
    # split-DNS VPNs/proxies the client URL can resolve to a reserved IP, which
    # FastMCP's SSRF guard blocks — breaking auth. Disabling CIMD makes those
    # clients fall back to plain Dynamic Client Registration against the proxy,
    # with no outbound fetch.
    #
    # A loopback bind means a single-user local server, where CIMD is pure
    # ceremony (the proxy authenticates upstream with its own fixed client) and
    # the outbound fetch is a liability — so default it off there. On a non-loopback
    # (hosted) bind, keep FastMCP's default of enabled, where stable client
    # identity and bounded registration storage actually matter. The explicit
    # env var overrides the heuristic either way.
    enable_cimd = _env_flag(
        "TIMETTA_OAUTH_ENABLE_CIMD", default=not is_loopback_host(http_host())
    )
    return OAuthProxy(
        upstream_authorization_endpoint=f"{auth_url}/connect/authorize",
        upstream_token_endpoint=f"{auth_url}/connect/token",
        upstream_client_id=get_client_id(),
        # `external` is a public PKCE client; a secret is only needed for
        # confidential clients and is read from the environment when present.
        upstream_client_secret=os.environ.get("TIMETTA_CLIENT_SECRET", ""),
        token_verifier=TimettaTokenVerifier(odata_base_url()),
        base_url=public_base_url(),
        # Timetta returns a refresh token only when `offline_access` is requested,
        # which OAuthProxy needs to keep the session alive without re-prompting.
        extra_authorize_params={"scope": scopes},
        enable_cimd=enable_cimd,
    )
