import json
import time

import httpx
import pytest
import respx

from timetta_mcp.auth import (
    StoredTokens,
    TokenStore,
    TokenProvider,
    credentials_path,
    default_credentials_path,
    get_auth_url,
    get_client_id,
)
from timetta_mcp.client import TimettaError


def test_get_auth_url_default(monkeypatch):
    monkeypatch.delenv("TIMETTA_AUTH_URL", raising=False)
    assert get_auth_url() == "https://auth.timetta.com"


def test_get_auth_url_env_override(monkeypatch):
    monkeypatch.setenv("TIMETTA_AUTH_URL", "https://auth.example.com/")
    assert get_auth_url() == "https://auth.example.com"


def test_get_client_id_env_override(monkeypatch):
    monkeypatch.setenv("TIMETTA_CLIENT_ID", "my-client")
    assert get_client_id() == "my-client"


def test_get_client_id_default(monkeypatch):
    monkeypatch.delenv("TIMETTA_CLIENT_ID", raising=False)
    assert get_client_id() == "external"


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


def test_token_store_load_malformed_raises(tmp_path):
    path = tmp_path / "creds.json"
    path.write_text("{not valid json", encoding="utf-8")
    store = TokenStore(path)
    with pytest.raises(ValueError, match="Malformed"):
        store.load()


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


# ---------------------------------------------------------------------------
# Task 2: TokenProvider
# ---------------------------------------------------------------------------

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
