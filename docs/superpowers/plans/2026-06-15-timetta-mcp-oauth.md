# Timetta MCP OAuth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add OAuth 2.0 (authorization_code + PKCE, browser login + auto-refresh from a file) to the Timetta MCP server, alongside the existing static `TIMETTA_API_TOKEN` mode.

**Architecture:** All OAuth logic is isolated in a new `auth.py` (config, `TokenStore`, `TokenProvider`, browser `login()`). `TimettaClient` is refactored to pull its bearer token from a *token provider* on every request and retry once on 401 after a forced refresh. `server.get_client()` selects the mode; `timetta-mcp login` is a one-time CLI subcommand that runs the browser flow.

**Tech Stack:** Python 3.10+, httpx, mcp (FastMCP), pytest + pytest-asyncio + respx. PKCE/loopback/browser use only the stdlib (`secrets`, `hashlib`, `base64`, `http.server`, `webbrowser`, `urllib`). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-15-timetta-mcp-oauth-design.md`

---

## File Structure

- **Create `src/timetta_mcp/auth.py`** — config helpers (`get_auth_url`, `get_client_id`, `credentials_path`, `default_credentials_path`), `StoredTokens`, `TokenStore`, `TokenProvider`, `StaticTokenProvider`, PKCE/login building blocks (`generate_pkce`, `build_authorize_url`, `tokens_from_response`, `exchange_code`, `_finish_login`), and orchestration (`login`, `login_command`).
- **Modify `src/timetta_mcp/client.py`** — `TimettaClient` takes a token provider; `Authorization` set per-request in `_send`; retry once on 401 when the provider can refresh.
- **Modify `src/timetta_mcp/server.py`** — `get_client()` mode selection, process-level `TokenProvider` singleton, `main()` dispatches the `login` subcommand.
- **Create `tests/test_auth.py`** — `TokenStore`, `TokenProvider`, PKCE/login building blocks.
- **Modify `tests/test_client.py`** — add 401 → refresh → retry test.
- **Modify `tests/test_server.py`** — update the two "missing token" tests for the new OAuth-fallback message.
- **Modify `README.md`** — document OAuth mode and `timetta-mcp login`.

Run all tests with: `uv run pytest -q`

---

## Task 1: auth.py config + StoredTokens + TokenStore

**Files:**
- Create: `src/timetta_mcp/auth.py`
- Test: `tests/test_auth.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_auth.py`:

```python
import json
import time

from timetta_mcp.auth import (
    StoredTokens,
    TokenStore,
    credentials_path,
    default_credentials_path,
    get_auth_url,
    get_client_id,
)


def test_get_auth_url_default(monkeypatch):
    monkeypatch.delenv("TIMETTA_AUTH_URL", raising=False)
    assert get_auth_url() == "https://auth.timetta.com"


def test_get_auth_url_env_override(monkeypatch):
    monkeypatch.setenv("TIMETTA_AUTH_URL", "https://auth.example.com/")
    assert get_auth_url() == "https://auth.example.com"


def test_get_client_id_env_override(monkeypatch):
    monkeypatch.setenv("TIMETTA_CLIENT_ID", "my-client")
    assert get_client_id() == "my-client"


def test_credentials_path_env_override(monkeypatch, tmp_path):
    target = tmp_path / "creds.json"
    monkeypatch.setenv("TIMETTA_CREDENTIALS_PATH", str(target))
    assert credentials_path() == target


def test_default_credentials_path_is_under_timetta_mcp():
    p = default_credentials_path()
    assert p.name == "credentials.json"
    assert p.parent.name == "timetta-mcp"


def test_token_store_round_trip(tmp_path):
    store = TokenStore(tmp_path / "creds.json")
    tokens = StoredTokens(
        access_token="a",
        refresh_token="r",
        expires_at=123.0,
        token_endpoint="https://auth.timetta.com/connect/token",
    )
    store.save(tokens)
    loaded = store.load()
    assert loaded == tokens


def test_token_store_load_missing_returns_none(tmp_path):
    assert TokenStore(tmp_path / "nope.json").load() is None


def test_token_store_save_is_atomic_overwrite(tmp_path):
    path = tmp_path / "creds.json"
    store = TokenStore(path)
    store.save(StoredTokens("a1", "r1", 1.0, "e"))
    store.save(StoredTokens("a2", "r2", 2.0, "e"))
    on_disk = json.loads(path.read_text())
    assert on_disk["access_token"] == "a2"


def test_stored_tokens_repr_hides_secrets():
    t = StoredTokens("super-secret-access", "super-secret-refresh", 1.0, "e")
    text = repr(t)
    assert "super-secret-access" not in text
    assert "super-secret-refresh" not in text


def test_token_store_repr_hides_path_secrets(tmp_path):
    store = TokenStore(tmp_path / "creds.json")
    store.save(StoredTokens("super-secret-access", "r", time.time(), "e"))
    assert "super-secret-access" not in repr(store)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_auth.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'timetta_mcp.auth'`

- [ ] **Step 3: Write minimal implementation**

Create `src/timetta_mcp/auth.py`:

```python
"""OAuth 2.0 support for Timetta: token storage, refresh, and browser login."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

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
        if not self._path.exists():
            return None
        data = json.loads(self._path.read_text(encoding="utf-8"))
        return StoredTokens(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=float(data["expires_at"]),
            token_endpoint=data["token_endpoint"],
        )

    def save(self, tokens: StoredTokens) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "access_token": tokens.access_token,
                    "refresh_token": tokens.refresh_token,
                    "expires_at": tokens.expires_at,
                    "token_endpoint": tokens.token_endpoint,
                }
            ),
            encoding="utf-8",
        )
        os.replace(tmp, self._path)
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass  # best-effort on Windows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_auth.py -q`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add src/timetta_mcp/auth.py tests/test_auth.py
git commit -m "feat: auth config and token store"
```

---

## Task 2: TokenProvider + StaticTokenProvider

**Files:**
- Modify: `src/timetta_mcp/auth.py`
- Test: `tests/test_auth.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_auth.py`:

```python
import httpx
import pytest
import respx

from timetta_mcp.auth import TokenProvider
from timetta_mcp.client import TimettaError

TOKEN_EP = "https://auth.timetta.com/connect/token"


def _store_with(tmp_path, **kwargs):
    store = TokenStore(tmp_path / "creds.json")
    defaults = dict(
        access_token="old-access",
        refresh_token="old-refresh",
        expires_at=time.time() + 9999,
        token_endpoint=TOKEN_EP,
    )
    defaults.update(kwargs)
    store.save(StoredTokens(**defaults))
    return store


async def test_provider_returns_cached_token_when_valid(tmp_path):
    store = _store_with(tmp_path)
    p = TokenProvider(store, "client")
    assert await p.get_token() == "old-access"
    assert p.can_refresh() is True


async def test_provider_raises_when_no_credentials(tmp_path):
    p = TokenProvider(TokenStore(tmp_path / "nope.json"), "client")
    with pytest.raises(TimettaError, match="timetta-mcp login"):
        await p.get_token()


@respx.mock
async def test_provider_refreshes_when_expired_and_rotates(tmp_path):
    store = _store_with(tmp_path, expires_at=time.time() - 10)
    route = respx.post(TOKEN_EP).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_in": 3600,
            },
        )
    )
    p = TokenProvider(store, "client")
    assert await p.get_token() == "new-access"
    # rotated refresh token persisted to disk
    assert store.load().refresh_token == "new-refresh"
    sent = route.calls.last.request
    body = sent.read().decode()
    assert "grant_type=refresh_token" in body
    assert "refresh_token=old-refresh" in body
    assert "client_id=client" in body


@respx.mock
async def test_provider_keeps_old_refresh_when_response_omits_it(tmp_path):
    store = _store_with(tmp_path, expires_at=time.time() - 10)
    respx.post(TOKEN_EP).mock(
        return_value=httpx.Response(
            200, json={"access_token": "new-access", "expires_in": 3600}
        )
    )
    p = TokenProvider(store, "client")
    await p.get_token()
    assert store.load().refresh_token == "old-refresh"


@respx.mock
async def test_provider_refresh_failure_raises_login_hint(tmp_path):
    store = _store_with(tmp_path, expires_at=time.time() - 10)
    respx.post(TOKEN_EP).mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"})
    )
    p = TokenProvider(store, "client")
    with pytest.raises(TimettaError, match="timetta-mcp login"):
        await p.get_token()


@respx.mock
async def test_force_refresh_ignores_validity(tmp_path):
    store = _store_with(tmp_path)  # currently valid
    respx.post(TOKEN_EP).mock(
        return_value=httpx.Response(
            200, json={"access_token": "forced", "refresh_token": "r2", "expires_in": 3600}
        )
    )
    p = TokenProvider(store, "client")
    assert await p.get_token() == "old-access"  # valid, no refresh
    assert await p.force_refresh() == "forced"  # forced regardless
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_auth.py -q`
Expected: FAIL with `ImportError: cannot import name 'TokenProvider'`

- [ ] **Step 3: Write minimal implementation**

Add to the top imports of `src/timetta_mcp/auth.py`:

```python
import asyncio
import time

import httpx

from .client import TimettaError
```

Add to `src/timetta_mcp/auth.py` (after `TokenStore`):

```python
def tokens_from_response(
    payload: dict, token_endpoint: str, *, previous_refresh: str | None = None
) -> StoredTokens:
    return StoredTokens(
        access_token=payload["access_token"],
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
                await self._refresh_locked()
            return self._tokens.access_token

    async def force_refresh(self) -> str:
        async with self._lock:
            self._ensure_loaded()
            await self._refresh_locked()
            return self._tokens.access_token

    async def _refresh_locked(self) -> None:
        current = self._tokens
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_auth.py -q`
Expected: PASS (all auth tests pass)

- [ ] **Step 5: Commit**

```bash
git add src/timetta_mcp/auth.py tests/test_auth.py
git commit -m "feat: token provider with refresh and rotation"
```

---

## Task 3: TimettaClient uses a token provider + 401 retry

**Files:**
- Modify: `src/timetta_mcp/client.py:15-21` (`__init__`), `src/timetta_mcp/client.py:86-103` (`_send`)
- Test: `tests/test_client.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_client.py`:

```python
class _RefreshOnceProvider:
    """Test double: first token is stale (server 401s), refresh yields a good one."""

    def __init__(self):
        self.tokens = ["stale", "fresh"]
        self.refreshed = False

    async def get_token(self):
        return self.tokens[0]

    def can_refresh(self):
        return True

    async def force_refresh(self):
        self.refreshed = True
        self.tokens.pop(0)
        return self.tokens[0]


@respx.mock
async def test_query_retries_once_after_401_refresh():
    route = respx.get(f"{BASE}/Users").mock(
        side_effect=[
            httpx.Response(401),
            httpx.Response(200, json={"value": [{"id": "1"}]}),
        ]
    )
    provider = _RefreshOnceProvider()
    client = TimettaClient(token_provider=provider)
    rows = await client.query("Users")

    assert rows == [{"id": "1"}]
    assert provider.refreshed is True
    assert route.calls.last.request.headers["Authorization"] == "Bearer fresh"
    await client.aclose()


@respx.mock
async def test_query_second_401_raises():
    respx.get(f"{BASE}/Users").mock(return_value=httpx.Response(401))
    provider = _RefreshOnceProvider()
    client = TimettaClient(token_provider=provider)
    with pytest.raises(TimettaError):
        await client.query("Users")
    await client.aclose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_client.py::test_query_retries_once_after_401_refresh -q`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'token_provider'`

- [ ] **Step 3: Write minimal implementation**

Replace `__init__` in `src/timetta_mcp/client.py` (lines 15-24):

```python
class TimettaClient:
    def __init__(
        self,
        token: str | None = None,
        *,
        token_provider=None,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        if token_provider is None:
            if token is None:
                raise TimettaError("TimettaClient needs a token or token_provider")
            token_provider = _StaticToken(token)
        self._provider = token_provider
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=30.0)

    def __repr__(self) -> str:  # never leak the token
        return f"TimettaClient(base_url={self._base!r})"
```

Add this minimal static provider just above the `TimettaClient` class (keeps `TimettaClient(token=...)` working without importing from `auth.py`, avoiding a circular import since `auth` imports from `client`):

```python
class _StaticToken:
    def __init__(self, token: str) -> None:
        self._token = token

    async def get_token(self) -> str:
        return self._token

    def can_refresh(self) -> bool:
        return False
```

Replace `_send` in `src/timetta_mcp/client.py` (lines 86-103) with:

```python
    async def _send(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str | int] | None = None,
        json: dict | None = None,
        headers: dict[str, str] | None = None,
        what: str,
    ) -> httpx.Response:
        resp = await self._request(method, url, params=params, json=json, headers=headers)
        if resp.status_code == 401 and self._provider.can_refresh():
            await self._provider.force_refresh()
            resp = await self._request(
                method, url, params=params, json=json, headers=headers
            )
        self._raise_for_status(resp, what)
        return resp

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str | int] | None = None,
        json: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        token = await self._provider.get_token()
        merged = {"Authorization": f"Bearer {token}", **(headers or {})}
        try:
            return await self._client.request(
                method, url, params=params, json=json, headers=merged
            )
        except httpx.RequestError as exc:
            raise TimettaError(f"Network error talking to Timetta: {exc}") from exc
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_client.py -q`
Expected: PASS (all client tests pass, including the existing static-token ones — `_StaticToken.can_refresh()` is False so the legacy 401 test still raises with the "TIMETTA_API_TOKEN" message)

- [ ] **Step 5: Commit**

```bash
git add src/timetta_mcp/client.py tests/test_client.py
git commit -m "feat: client pulls token from provider and retries on 401"
```

---

## Task 4: PKCE, authorize URL, code exchange, finish-login

**Files:**
- Modify: `src/timetta_mcp/auth.py`
- Test: `tests/test_auth.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_auth.py`:

```python
import base64
import hashlib
from urllib.parse import parse_qs, urlparse

from timetta_mcp.auth import (
    build_authorize_url,
    exchange_code,
    generate_pkce,
    _finish_login,
)


def test_generate_pkce_challenge_matches_verifier():
    verifier, challenge = generate_pkce()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert challenge == expected
    assert "=" not in challenge


def test_build_authorize_url_has_pkce_and_state():
    url = build_authorize_url(
        "https://auth.timetta.com",
        "client",
        "http://127.0.0.1:5000/callback",
        "the-challenge",
        "the-state",
    )
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    assert parsed.path == "/connect/authorize"
    assert q["response_type"] == ["code"]
    assert q["client_id"] == ["client"]
    assert q["redirect_uri"] == ["http://127.0.0.1:5000/callback"]
    assert q["code_challenge"] == ["the-challenge"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["state"] == ["the-state"]
    assert q["scope"] == ["all offline_access"]


@respx.mock
async def test_exchange_code_posts_pkce_and_returns_tokens():
    route = respx.post(TOKEN_EP).mock(
        return_value=httpx.Response(
            200,
            json={"access_token": "a", "refresh_token": "r", "expires_in": 3600},
        )
    )
    tokens = await exchange_code(
        TOKEN_EP, "client", "the-code", "the-verifier", "http://127.0.0.1:5000/callback"
    )
    assert tokens.access_token == "a"
    assert tokens.refresh_token == "r"
    assert tokens.expires_at > time.time()
    body = route.calls.last.request.read().decode()
    assert "grant_type=authorization_code" in body
    assert "code=the-code" in body
    assert "code_verifier=the-verifier" in body


@respx.mock
async def test_finish_login_state_mismatch_raises(tmp_path):
    store = TokenStore(tmp_path / "creds.json")
    with pytest.raises(TimettaError, match="state"):
        await _finish_login(
            returned_state="bad",
            expected_state="good",
            code="c",
            verifier="v",
            redirect_uri="http://127.0.0.1:5000/callback",
            token_endpoint=TOKEN_EP,
            client_id="client",
            store=store,
        )


@respx.mock
async def test_finish_login_saves_tokens(tmp_path):
    respx.post(TOKEN_EP).mock(
        return_value=httpx.Response(
            200, json={"access_token": "a", "refresh_token": "r", "expires_in": 3600}
        )
    )
    store = TokenStore(tmp_path / "creds.json")
    await _finish_login(
        returned_state="good",
        expected_state="good",
        code="c",
        verifier="v",
        redirect_uri="http://127.0.0.1:5000/callback",
        token_endpoint=TOKEN_EP,
        client_id="client",
        store=store,
    )
    assert store.load().access_token == "a"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_auth.py -q`
Expected: FAIL with `ImportError: cannot import name 'generate_pkce'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/timetta_mcp/auth.py` imports:

```python
import base64
import hashlib
import secrets
from urllib.parse import urlencode
```

Add to `src/timetta_mcp/auth.py`:

```python
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
        raise TimettaError(f"Token exchange failed: HTTP {resp.status_code}")
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_auth.py -q`
Expected: PASS (all auth tests pass)

- [ ] **Step 5: Commit**

```bash
git add src/timetta_mcp/auth.py tests/test_auth.py
git commit -m "feat: pkce, authorize url and code exchange"
```

---

## Task 5: login() orchestration + CLI subcommand

**Files:**
- Modify: `src/timetta_mcp/auth.py` (add `login`, `login_command`)
- Modify: `src/timetta_mcp/server.py:196-198` (`main`)
- Test: `tests/test_auth.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_auth.py`:

```python
import timetta_mcp.auth as auth_mod


@respx.mock
async def test_login_drives_loopback_and_saves(tmp_path, monkeypatch):
    respx.post(TOKEN_EP).mock(
        return_value=httpx.Response(
            200, json={"access_token": "a", "refresh_token": "r", "expires_in": 3600}
        )
    )
    store = TokenStore(tmp_path / "creds.json")

    # Capture the authorize URL instead of opening a browser, and simulate the
    # redirect by returning the same `state` plus a code.
    captured = {}

    def fake_capture(authorize_url, redirect_uri, expected_state):
        captured["url"] = authorize_url
        return {"code": "the-code", "state": expected_state}

    monkeypatch.setattr(auth_mod, "_capture_redirect", fake_capture)

    tokens = await auth_mod.login(
        auth_url="https://auth.timetta.com",
        client_id="client",
        store=store,
        redirect_port=5555,
    )
    assert tokens.access_token == "a"
    assert store.load().refresh_token == "r"
    assert captured["url"].startswith("https://auth.timetta.com/connect/authorize?")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_auth.py::test_login_drives_loopback_and_saves -q`
Expected: FAIL with `AttributeError: module 'timetta_mcp.auth' has no attribute '_capture_redirect'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/timetta_mcp/auth.py` imports:

```python
import http.server
import urllib.parse
import webbrowser
```

Add to `src/timetta_mcp/auth.py`:

```python
def _capture_redirect(
    authorize_url: str, redirect_uri: str, expected_state: str
) -> dict[str, str | None]:
    """Open the browser, serve exactly one loopback request, return its query."""
    parsed = urllib.parse.urlparse(redirect_uri)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 0
    captured: dict[str, str | None] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)
            captured["code"] = params.get("code", [None])[0]
            captured["state"] = params.get("state", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Timetta login complete. You can close this tab.")

        def log_message(self, *args) -> None:  # silence stderr noise
            pass

    httpd = http.server.HTTPServer((host, port), Handler)
    try:
        webbrowser.open(authorize_url)
        print(f"If your browser did not open, visit:\n{authorize_url}")
        httpd.handle_request()  # blocks until the single redirect arrives
    finally:
        httpd.server_close()
    return captured


async def login(
    *,
    auth_url: str | None = None,
    client_id: str | None = None,
    store: TokenStore | None = None,
    redirect_port: int = 0,
) -> StoredTokens:
    auth_url = (auth_url or get_auth_url()).rstrip("/")
    client_id = client_id or get_client_id()
    store = store or TokenStore(credentials_path())

    verifier, challenge = generate_pkce()
    state = secrets.token_urlsafe(16)
    redirect_uri = f"http://127.0.0.1:{redirect_port}/callback"
    authorize_url = build_authorize_url(
        auth_url, client_id, redirect_uri, challenge, state
    )
    result = _capture_redirect(authorize_url, redirect_uri, state)
    return await _finish_login(
        returned_state=result.get("state") or "",
        expected_state=state,
        code=result.get("code") or "",
        verifier=verifier,
        redirect_uri=redirect_uri,
        token_endpoint=f"{auth_url}/connect/token",
        client_id=client_id,
        store=store,
    )


def login_command() -> None:
    """Entry point for `timetta-mcp login`."""
    import asyncio

    try:
        asyncio.run(login())
    except TimettaError as exc:
        print(f"Login failed: {exc}")
        raise SystemExit(1)
    print("Timetta login successful — credentials saved.")
```

Note: with `redirect_port=0` the OS assigns a free port, which requires the OAuth client to allow any-port loopback redirects (RFC 8252). If the registered client needs a fixed port, pass `redirect_port` / set it in `login_command` to that value.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_auth.py::test_login_drives_loopback_and_saves -q`
Expected: PASS

- [ ] **Step 5: Wire the CLI subcommand**

Replace `main()` in `src/timetta_mcp/server.py` (lines 196-198) with:

```python
def main() -> None:
    """Console entry point — `timetta-mcp` serves over stdio; `login` runs OAuth."""
    import sys

    argv = sys.argv[1:]
    if argv and argv[0] == "login":
        from .auth import login_command

        login_command()
        return
    mcp.run()
```

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (everything green so far)

- [ ] **Step 7: Commit**

```bash
git add src/timetta_mcp/auth.py src/timetta_mcp/server.py tests/test_auth.py
git commit -m "feat: browser login flow and login subcommand"
```

---

## Task 6: server.get_client() mode selection

**Files:**
- Modify: `src/timetta_mcp/server.py:16-21` (`get_client`)
- Modify: `tests/test_server.py:85-89` and `tests/test_server.py:118-123`
- Test: `tests/test_server.py`

- [ ] **Step 1: Update the two "missing token" tests and add mode tests**

In `tests/test_server.py`, replace `test_missing_token_returns_error_text` (lines 85-89) and `test_create_entity_missing_token_returns_error` (lines 118-123) with:

```python
async def test_missing_token_falls_back_to_oauth_login_hint(monkeypatch, tmp_path):
    monkeypatch.delenv("TIMETTA_API_TOKEN", raising=False)
    monkeypatch.setenv("TIMETTA_CREDENTIALS_PATH", str(tmp_path / "none.json"))
    server._reset_token_provider()
    out = await server._list_entities()
    assert out.startswith("Error:")
    assert "timetta-mcp login" in out


async def test_create_entity_missing_token_falls_back_to_login_hint(monkeypatch, tmp_path):
    monkeypatch.delenv("TIMETTA_API_TOKEN", raising=False)
    monkeypatch.setenv("TIMETTA_CREDENTIALS_PATH", str(tmp_path / "none.json"))
    server._reset_token_provider()
    out = await server._create_entity("Issues", {"name": "T"})
    assert out.startswith("Error:")
    assert "timetta-mcp login" in out


async def test_static_token_mode_uses_static_provider(monkeypatch):
    monkeypatch.setenv("TIMETTA_API_TOKEN", "tok")
    client = server.get_client()
    assert await client._provider.get_token() == "tok"
    assert client._provider.can_refresh() is False
    await client.aclose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server.py -q`
Expected: FAIL with `AttributeError: module 'timetta_mcp.server' has no attribute '_reset_token_provider'`

- [ ] **Step 3: Write minimal implementation**

Replace `get_client()` in `src/timetta_mcp/server.py` (lines 16-21) with:

```python
from .auth import TokenProvider, TokenStore, credentials_path, get_client_id

_token_provider: TokenProvider | None = None


def _reset_token_provider() -> None:
    """Drop the cached provider (used by tests after changing env)."""
    global _token_provider
    _token_provider = None


def _get_token_provider() -> TokenProvider:
    global _token_provider
    if _token_provider is None:
        _token_provider = TokenProvider(TokenStore(credentials_path()), get_client_id())
    return _token_provider


def get_client() -> TimettaClient:
    base_url = os.environ.get("TIMETTA_BASE_URL", DEFAULT_BASE_URL)
    static = os.environ.get("TIMETTA_API_TOKEN")
    if static:
        return TimettaClient(token=static, base_url=base_url)
    return TimettaClient(token_provider=_get_token_provider(), base_url=base_url)
```

Note: the `from .auth import ...` line goes with the other imports at the top of `server.py` (next to `from .client import ...` on line 11); it is shown here together with `get_client` only for context.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_server.py -q`
Expected: PASS

Note: `TimettaError` raised lazily by the provider on first `get_token()` is caught by the existing `except Exception` blocks in the tool wrappers (`server.py:36`, `80`, `94`, etc.), so the error surfaces as `Error: ...` text exactly like the old behavior.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (all tests green)

- [ ] **Step 6: Commit**

```bash
git add src/timetta_mcp/server.py tests/test_server.py
git commit -m "feat: select static or oauth auth in get_client"
```

---

## Task 7: Document OAuth mode

**Files:**
- Modify: `README.md` (auth/config section near line 19-21)

- [ ] **Step 1: Update the config table and add a login section**

In `README.md`, extend the environment-variable table (the block containing `TIMETTA_API_TOKEN` around line 19-21) so it reads:

```markdown
| Variable | Required | Default | Notes |
|---|---|---|---|
| `TIMETTA_API_TOKEN` | one of the two | — | Static Token API value (Bearer), TTL 1 year. Takes priority when set. |
| `TIMETTA_CLIENT_ID` | for OAuth | `external` | Public OAuth client id (PKCE, loopback redirect) used by `timetta-mcp login`. |
| `TIMETTA_AUTH_URL` | no | `https://auth.timetta.com` | OAuth auth server. |
| `TIMETTA_CREDENTIALS_PATH` | no | platform default | Where OAuth tokens are stored. Default: `%APPDATA%\timetta-mcp\credentials.json` (Windows), `~/.config/timetta-mcp/credentials.json` (POSIX). |
| `TIMETTA_BASE_URL` | no | `https://api.timetta.com/odata` | OData base URL. |
```

Then add a new section after the table:

```markdown
## Authentication

Two modes, chosen automatically:

1. **Static token (CI / automation).** Set `TIMETTA_API_TOKEN`. Used whenever present.
2. **OAuth browser login.** Leave `TIMETTA_API_TOKEN` unset, set `TIMETTA_CLIENT_ID`,
   then run a one-time login:

   ```bash
   timetta-mcp login
   ```

   This opens your browser to Timetta, you authorize, and the refresh token is
   saved to `TIMETTA_CREDENTIALS_PATH`. The server then refreshes the access
   token automatically (no further interaction). Re-run `timetta-mcp login` if
   the refresh token expires (~15 days of inactivity).
```

- [ ] **Step 2: Verify the docs render and nothing else broke**

Run: `uv run pytest -q`
Expected: PASS (docs change only; suite stays green)

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document oauth login mode"
```

---

## Final verification

- [ ] Run the full suite: `uv run pytest -q` — expected: all green.
- [ ] Manual smoke (requires a registered public OAuth client with a loopback redirect): `TIMETTA_CLIENT_ID=<id> uv run timetta-mcp login` opens the browser, completes, and writes `credentials.json`.
- [ ] Confirm static mode still works: `TIMETTA_API_TOKEN=<token> uv run pytest tests/test_client.py -q`.

## Notes / known dependencies

- **OAuth client registration is external.** The browser flow needs a public client (PKCE, no secret) whose registered redirect allows `http://127.0.0.1` loopback. With `redirect_port=0` (default) the OS picks a free port — the client must permit any-port loopback (RFC 8252). If it requires a fixed port, set it in `login_command()` via `login(redirect_port=<port>)` and register that exact `redirect_uri`.
- **No password grant.** ROPG / password storage is intentionally out of scope.
- **`access_token` is cached in the file** alongside `refresh_token` so restarts are cheap; the `expires_at` leeway is 60 s.
