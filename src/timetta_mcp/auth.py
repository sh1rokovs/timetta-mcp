"""OAuth 2.0 support for Timetta: token storage, refresh, and browser login."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

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
            self._tokens = self._store.load()
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
