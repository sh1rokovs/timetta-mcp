"""OAuth 2.0 support for Timetta: token storage, refresh, and login.

`timetta-mcp login` offers three methods:
- Token API — paste a long-lived static token (recommended; works with SSO).
- Email + password — OAuth password grant via the public `external` client.
- Browser — authorization_code + PKCE via the `web_app` client. Its redirect is
  fixed to https://app.timetta.com/auth-callback (no loopback), so the user
  pastes the resulting code back; `web_app` is denied `offline_access`, so there
  is no refresh token and the access token lasts ~1 hour.
"""

from __future__ import annotations

import asyncio
import base64
import getpass
import hashlib
import json
import os
import secrets
import tempfile
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from .client import TimettaError

# Browser sign-in uses the same public client the Timetta web app uses.
BROWSER_CLIENT_ID = "web_app"
BROWSER_REDIRECT_URI = "https://app.timetta.com/auth-callback"
BROWSER_SCOPE = "openid profile all"  # offline_access is rejected for web_app

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


@dataclass
class StaticCredentials:
    """A long-lived Timetta Token API value (no refresh)."""

    api_token: str

    def __repr__(self) -> str:  # never leak the token value
        return "StaticCredentials(...)"


class TokenStore:
    """Reads/writes the credentials file atomically; never leaks token values.

    The file holds either OAuth tokens (`StoredTokens`) or a static Token API
    value (`{"type": "static", "api_token": ...}`).
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def __repr__(self) -> str:
        return f"TokenStore(path={str(self._path)!r})"

    def _read(self) -> dict | None:
        if not self._path.exists():
            return None
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed credentials file {self._path}: {exc}") from exc

    def _write(self, payload: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload)
        fd, tmp_name = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
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

    def load(self) -> StoredTokens | None:
        """Return stored OAuth tokens, or None if the file does not exist.

        Raises ValueError if the file exists but is not valid OAuth-token JSON.
        """
        data = self._read()
        if data is None:
            return None
        try:
            return StoredTokens(
                access_token=data["access_token"],
                refresh_token=data["refresh_token"],
                expires_at=float(data["expires_at"]),
                token_endpoint=data["token_endpoint"],
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError(f"Malformed credentials file {self._path}: {exc}") from exc

    def load_any(self) -> StoredTokens | StaticCredentials | None:
        """Return whichever credential kind the file holds, or None if absent.

        Raises ValueError if the file exists but is malformed.
        """
        data = self._read()
        if data is None:
            return None
        if isinstance(data, dict) and data.get("type") == "static":
            token = data.get("api_token")
            if not token:
                raise ValueError(
                    f"Malformed credentials file {self._path}: missing api_token"
                )
            return StaticCredentials(api_token=token)
        return self.load()

    def save(self, tokens: StoredTokens) -> None:
        self._write(
            {
                "type": "oauth",
                "access_token": tokens.access_token,
                "refresh_token": tokens.refresh_token,
                "expires_at": tokens.expires_at,
                "token_endpoint": tokens.token_endpoint,
            }
        )

    def save_static(self, api_token: str) -> None:
        self._write({"type": "static", "api_token": api_token})


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


async def password_login(
    username: str,
    password: str,
    *,
    auth_url: str | None = None,
    client_id: str | None = None,
    store: TokenStore | None = None,
    scope: str = "all offline_access",
) -> StoredTokens:
    """Log in with the Resource Owner Password Grant and persist the tokens.

    Posts `grant_type=password` to the token endpoint; the Timetta public client
    `external` supports this grant (and `refresh_token`). On success the tokens
    are saved via `store`; nothing is persisted on failure.
    """
    auth_url = (auth_url or get_auth_url()).rstrip("/")
    client_id = client_id or get_client_id()
    store = store or TokenStore(credentials_path())
    token_endpoint = f"{auth_url}/connect/token"

    data = {
        "grant_type": "password",
        "client_id": client_id,
        "username": username,
        "password": password,
        "scope": scope,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            resp = await c.post(token_endpoint, data=data)
    except httpx.RequestError as exc:
        raise TimettaError(f"Network error during login: {exc}") from exc
    if resp.status_code != 200:
        try:
            payload = resp.json()
            detail = payload.get("error_description") or payload.get("error", "")
        except Exception:
            detail = resp.text[:200]
        raise TimettaError(
            f"Login failed: HTTP {resp.status_code} — {detail}".rstrip(" —")
        )
    tokens = tokens_from_response(resp.json(), token_endpoint)
    store.save(tokens)
    return tokens


def generate_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def build_browser_authorize_url(auth_url: str, state: str, challenge: str) -> str:
    params = {
        "response_type": "code",
        "client_id": BROWSER_CLIENT_ID,
        "redirect_uri": BROWSER_REDIRECT_URI,
        "scope": BROWSER_SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{auth_url}/connect/authorize?{urlencode(params)}"


def parse_redirect(pasted: str) -> tuple[str, str | None]:
    """Accept a full redirect URL or a bare code; return (code, state-or-None)."""
    pasted = pasted.strip()
    if pasted.startswith("http") or "?" in pasted or "code=" in pasted:
        q = parse_qs(urlparse(pasted).query)
        return q.get("code", [""])[0], q.get("state", [None])[0]
    return pasted, None


async def exchange_browser_code(
    token_endpoint: str, code: str, verifier: str
) -> StoredTokens:
    data = {
        "grant_type": "authorization_code",
        "client_id": BROWSER_CLIENT_ID,
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": BROWSER_REDIRECT_URI,
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
            f"Code exchange failed: HTTP {resp.status_code} — {detail}".rstrip(" —")
        )
    return tokens_from_response(resp.json(), token_endpoint)


async def browser_login(
    *,
    auth_url: str | None = None,
    store: TokenStore | None = None,
    opener=None,
    prompt=None,
) -> StoredTokens:
    """Browser authorization_code + PKCE via `web_app`; user pastes the code.

    `web_app`'s redirect_uri is fixed to app.timetta.com (no loopback), so after
    signing in the user copies the resulting URL/code back here. There is no
    refresh token (offline_access denied), so the saved access token lasts ~1h.
    """
    auth_url = (auth_url or get_auth_url()).rstrip("/")
    store = store or TokenStore(credentials_path())
    opener = opener or webbrowser.open
    prompt = prompt or input

    verifier, challenge = generate_pkce()
    state = secrets.token_urlsafe(16)
    url = build_browser_authorize_url(auth_url, state, challenge)

    opener(url)
    print("Opened your browser to sign in to Timetta (SSO supported).")
    print(f"If it did not open, visit:\n{url}\n")
    print(
        "After signing in your browser lands on a page at "
        "app.timetta.com/auth-callback (it may show an error — that is fine)."
    )
    pasted = prompt("Paste that full URL (or just the code): ").strip()

    code, returned_state = parse_redirect(pasted)
    if not code:
        raise TimettaError("No authorization code found in the pasted value")
    if returned_state is not None and returned_state != state:
        raise TimettaError("OAuth state mismatch — aborting login")
    tokens = await exchange_browser_code(f"{auth_url}/connect/token", code, verifier)
    store.save(tokens)
    return tokens


def _prompt_credentials() -> tuple[str, str]:
    username = input("Timetta login (email): ").strip()
    password = getpass.getpass("Timetta password: ")
    return username, password


def _choose_method() -> str:
    """Ask which login method to use; returns 'token', 'password' or 'browser'."""
    print("Timetta login method:")
    print("  1) Token API — paste a long-lived token (recommended; works with SSO)")
    print("  2) Email + password (OAuth password grant)")
    print("  3) Browser sign-in (web_app; ~1h session, no auto-refresh)")
    choice = input("Select [1/2/3]: ").strip()
    return {"1": "token", "3": "browser"}.get(choice, "password")


def _token_login() -> None:
    token = getpass.getpass("Paste Timetta Token API value: ").strip()
    if not token:
        print("Login aborted: token is required.")
        raise SystemExit(1)
    TokenStore(credentials_path()).save_static(token)
    print("Timetta Token API saved.")


def _password_login() -> None:
    username, password = _prompt_credentials()
    if not username or not password:
        print("Login aborted: username and password are required.")
        raise SystemExit(1)
    try:
        asyncio.run(password_login(username, password))
    except TimettaError as exc:
        print(f"Login failed: {exc}")
        raise SystemExit(1)
    print("Timetta login successful — credentials saved.")


def _browser_login() -> None:
    try:
        asyncio.run(browser_login())
    except TimettaError as exc:
        print(f"Login failed: {exc}")
        raise SystemExit(1)
    print(
        "Timetta browser login successful — access token saved.\n"
        "Note: no refresh token (web_app), so re-run `timetta-mcp login` "
        "when it expires (~1 hour)."
    )


def login_command() -> None:
    """Entry point for `timetta-mcp login`.

    Offers three methods (see module docstring): Token API, email/password, or
    browser sign-in.
    """
    method = _choose_method()
    if method == "token":
        _token_login()
    elif method == "browser":
        _browser_login()
    else:
        _password_login()


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
