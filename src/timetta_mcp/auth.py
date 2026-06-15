"""OAuth 2.0 support for Timetta: token storage, refresh, and browser login."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import http.server
import json
import os
import secrets
import tempfile
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

import httpx

from .client import TimettaError

DEFAULT_AUTH_URL = "https://auth.timetta.com"
DEFAULT_CLIENT_ID = "external"


def get_auth_url() -> str:
    return os.environ.get("TIMETTA_AUTH_URL", DEFAULT_AUTH_URL).rstrip("/")


def get_client_id() -> str:
    return os.environ.get("TIMETTA_CLIENT_ID", DEFAULT_CLIENT_ID)


def default_credentials_path() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return Path(base) / "timetta-mcp" / "credentials.json"
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "timetta-mcp" / "credentials.json"


def credentials_path() -> Path:
    env = os.environ.get("TIMETTA_CREDENTIALS_PATH")
    return Path(env) if env else default_credentials_path()


@dataclass
class StoredTokens:
    access_token: str
    refresh_token: str
    expires_at: float
    token_endpoint: str

    def __repr__(self) -> str:  # never leak token values
        return f"StoredTokens(expires_at={self.expires_at!r})"


class TokenStore:
    """Reads/writes the token file atomically; never leaks token values."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def __repr__(self) -> str:
        return f"TokenStore(path={str(self._path)!r})"

    def load(self) -> StoredTokens | None:
        """Return stored tokens, or None if the file does not exist.

        Raises ValueError if the file exists but is malformed.
        """
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return StoredTokens(
                access_token=data["access_token"],
                refresh_token=data["refresh_token"],
                expires_at=float(data["expires_at"]),
                token_endpoint=data["token_endpoint"],
            )
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            raise ValueError(f"Malformed credentials file {self._path}: {exc}") from exc

    def save(self, tokens: StoredTokens) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "access_token": tokens.access_token,
                "refresh_token": tokens.refresh_token,
                "expires_at": tokens.expires_at,
                "token_endpoint": tokens.token_endpoint,
            }
        )
        fd, tmp_name = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp_name, self._path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass  # best-effort on Windows


def tokens_from_response(
    payload: dict, token_endpoint: str, *, previous_refresh: str | None = None
) -> StoredTokens:
    try:
        access_token = payload["access_token"]
    except KeyError as exc:
        raise TimettaError(
            "Token endpoint returned 200 but no 'access_token' — run `timetta-mcp login`"
        ) from exc
    return StoredTokens(
        access_token=access_token,
        # empty string means no refresh token; the next refresh will fail with invalid_grant
        refresh_token=payload.get("refresh_token") or previous_refresh or "",
        expires_at=time.time() + int(payload.get("expires_in", 3600)),
        token_endpoint=token_endpoint,
    )


def generate_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def build_authorize_url(
    auth_url: str,
    client_id: str,
    redirect_uri: str,
    challenge: str,
    state: str,
    scope: str = "all offline_access",
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return f"{auth_url}/connect/authorize?{urlencode(params)}"


async def exchange_code(
    token_endpoint: str,
    client_id: str,
    code: str,
    verifier: str,
    redirect_uri: str,
) -> StoredTokens:
    data = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            resp = await c.post(token_endpoint, data=data)
    except httpx.RequestError as exc:
        raise TimettaError(f"Network error exchanging code: {exc}") from exc
    if resp.status_code != 200:
        try:
            payload = resp.json()
            detail = payload.get("error_description") or payload.get("error", "")
        except Exception:
            detail = resp.text[:200]
        raise TimettaError(
            f"Token exchange failed: HTTP {resp.status_code} — {detail}".rstrip(" —")
        )
    return tokens_from_response(resp.json(), token_endpoint)


async def _finish_login(
    *,
    returned_state: str,
    expected_state: str,
    code: str,
    verifier: str,
    redirect_uri: str,
    token_endpoint: str,
    client_id: str,
    store: TokenStore,
) -> StoredTokens:
    if returned_state != expected_state:
        raise TimettaError("OAuth state mismatch — aborting login")
    if not code:
        raise TimettaError("No authorization code received")
    tokens = await exchange_code(token_endpoint, client_id, code, verifier, redirect_uri)
    store.save(tokens)
    return tokens


class _LoopbackServer(http.server.HTTPServer):
    timed_out_flag = False

    def handle_timeout(self) -> None:
        self.timed_out_flag = True


def _capture_redirect(
    make_authorize_url,
    *,
    redirect_port: int = 0,
    timeout: float = 120.0,
) -> tuple[dict[str, str | None], str]:
    """Bind a loopback server, open the browser, capture the OAuth redirect.

    `make_authorize_url` is a callable taking the (now-known) redirect_uri and
    returning the full authorize URL. Returns (captured_params, redirect_uri_used).
    Skips non-OAuth requests (e.g. favicon) and raises on timeout.
    """
    captured: dict[str, str | None] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code = params.get("code", [None])[0]
            error = params.get("error", [None])[0]
            if code or error:
                captured["code"] = code
                captured["state"] = params.get("state", [None])[0]
                captured["error"] = error
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Timetta login complete. You can close this tab.")
            else:
                self.send_response(204)  # favicon/preflight — ignore, keep waiting
                self.end_headers()

        def log_message(self, format, *args) -> None:  # noqa: A002 — silence stderr
            pass

    httpd = _LoopbackServer(("127.0.0.1", redirect_port), Handler)
    actual_port = httpd.server_address[1]
    redirect_uri = f"http://127.0.0.1:{actual_port}/callback"
    httpd.timeout = timeout
    try:
        authorize_url = make_authorize_url(redirect_uri)
        webbrowser.open(authorize_url)
        print(f"If your browser did not open, visit:\n{authorize_url}")
        while not captured and not httpd.timed_out_flag:
            httpd.handle_request()
    finally:
        httpd.server_close()
    if not captured:
        raise TimettaError("Login timed out waiting for the browser redirect")
    return captured, redirect_uri


async def login(
    *,
    auth_url: str | None = None,
    client_id: str | None = None,
    store: TokenStore | None = None,
    redirect_port: int = 0,
    timeout: float = 120.0,
) -> StoredTokens:
    auth_url = (auth_url or get_auth_url()).rstrip("/")
    client_id = client_id or get_client_id()
    store = store or TokenStore(credentials_path())

    verifier, challenge = generate_pkce()
    state = secrets.token_urlsafe(16)

    def make_authorize_url(redirect_uri: str) -> str:
        return build_authorize_url(auth_url, client_id, redirect_uri, challenge, state)

    captured, redirect_uri = _capture_redirect(
        make_authorize_url, redirect_port=redirect_port, timeout=timeout
    )
    if captured.get("error"):
        raise TimettaError(f"Authorization failed: {captured['error']}")
    return await _finish_login(
        returned_state=captured.get("state") or "",
        expected_state=state,
        code=captured.get("code") or "",
        verifier=verifier,
        redirect_uri=redirect_uri,
        token_endpoint=f"{auth_url}/connect/token",
        client_id=client_id,
        store=store,
    )


def login_command() -> None:
    """Entry point for `timetta-mcp login`."""
    try:
        asyncio.run(login())
    except TimettaError as exc:
        print(f"Login failed: {exc}")
        raise SystemExit(1)
    print("Timetta login successful — credentials saved.")


class TokenProvider:
    """Process-level provider: serves a valid access_token, refreshing as needed."""

    def __init__(self, store: TokenStore, client_id: str, *, leeway: float = 60.0) -> None:
        self._store = store
        self._client_id = client_id
        self._leeway = leeway
        self._lock = asyncio.Lock()
        self._tokens: StoredTokens | None = None

    def __repr__(self) -> str:
        return f"TokenProvider(client_id={self._client_id!r})"

    def can_refresh(self) -> bool:
        return True

    def _ensure_loaded(self) -> StoredTokens:
        if self._tokens is None:
            try:
                self._tokens = self._store.load()
            except ValueError as exc:
                raise TimettaError(
                    "Timetta credentials file is corrupted — run `timetta-mcp login`"
                ) from exc
        if self._tokens is None:
            raise TimettaError(
                "No valid Timetta credentials — run `timetta-mcp login`"
            )
        return self._tokens

    def _is_valid(self, tokens: StoredTokens) -> bool:
        return tokens.expires_at - self._leeway > time.time()

    async def get_token(self) -> str:
        async with self._lock:
            tokens = self._ensure_loaded()
            if not self._is_valid(tokens):
                tokens = await self._refresh_locked()
            return tokens.access_token

    async def force_refresh(self) -> str:
        async with self._lock:
            self._ensure_loaded()
            tokens = await self._refresh_locked()
            return tokens.access_token

    async def _refresh_locked(self) -> StoredTokens:
        current = self._tokens
        assert current is not None  # guaranteed by _ensure_loaded under the lock
        data = {
            "grant_type": "refresh_token",
            "client_id": self._client_id,
            "refresh_token": current.refresh_token,
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as c:
                resp = await c.post(current.token_endpoint, data=data)
        except httpx.RequestError as exc:
            raise TimettaError(
                f"Network error refreshing Timetta token: {exc}"
            ) from exc
        if resp.status_code != 200:
            raise TimettaError(
                "Failed to refresh Timetta token — run `timetta-mcp login`"
            )
        self._tokens = tokens_from_response(
            resp.json(), current.token_endpoint, previous_refresh=current.refresh_token
        )
        self._store.save(self._tokens)
        return self._tokens
