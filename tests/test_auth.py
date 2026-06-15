import asyncio
import json
import time

import httpx
import pytest
import respx

from timetta_mcp.auth import (
    StaticCredentials,
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


def test_save_static_and_load_any_returns_static(tmp_path):
    store = TokenStore(tmp_path / "creds.json")
    store.save_static("my-token-api-value")
    creds = store.load_any()
    assert isinstance(creds, StaticCredentials)
    assert creds.api_token == "my-token-api-value"


def test_load_any_returns_oauth_tokens_for_oauth_file(tmp_path):
    store = TokenStore(tmp_path / "creds.json")
    store.save(StoredTokens("a", "r", 123.0, "ep"))
    creds = store.load_any()
    assert isinstance(creds, StoredTokens)
    assert creds.access_token == "a"


def test_load_any_none_when_missing(tmp_path):
    assert TokenStore(tmp_path / "nope.json").load_any() is None


def test_load_any_malformed_static_raises(tmp_path):
    path = tmp_path / "creds.json"
    path.write_text('{"type": "static"}', encoding="utf-8")  # no api_token
    with pytest.raises(ValueError, match="Malformed"):
        TokenStore(path).load_any()


def test_static_credentials_repr_hides_token():
    assert "super-secret-token" not in repr(StaticCredentials("super-secret-token"))


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


@respx.mock
async def test_provider_network_error_raises(tmp_path):
    store = _store_with(tmp_path, expires_at=time.time() - 10)
    respx.post(TOKEN_EP).mock(side_effect=httpx.ConnectError("boom"))
    p = TokenProvider(store, "client")
    with pytest.raises(TimettaError, match="Network error"):
        await p.get_token()


@respx.mock
async def test_provider_200_without_access_token_raises(tmp_path):
    store = _store_with(tmp_path, expires_at=time.time() - 10)
    respx.post(TOKEN_EP).mock(return_value=httpx.Response(200, json={"expires_in": 3600}))
    p = TokenProvider(store, "client")
    with pytest.raises(TimettaError, match="timetta-mcp login"):
        await p.get_token()


@respx.mock
async def test_provider_concurrent_calls_single_refresh(tmp_path):
    store = _store_with(tmp_path, expires_at=time.time() - 10)
    route = respx.post(TOKEN_EP).mock(
        return_value=httpx.Response(200, json={"access_token": "new", "expires_in": 3600})
    )
    p = TokenProvider(store, "client")
    t1, t2 = await asyncio.gather(p.get_token(), p.get_token())
    assert t1 == t2 == "new"
    assert route.call_count == 1


# ---------------------------------------------------------------------------
# Task 4: Resource Owner Password Grant login
# ---------------------------------------------------------------------------

from urllib.parse import parse_qs, urlparse

from timetta_mcp.auth import (
    browser_login,
    build_browser_authorize_url,
    exchange_browser_code,
    generate_pkce,
    parse_redirect,
    password_login,
)


@respx.mock
async def test_password_login_posts_grant_and_saves(tmp_path):
    route = respx.post(TOKEN_EP).mock(
        return_value=httpx.Response(
            200, json={"access_token": "a", "refresh_token": "r", "expires_in": 3600}
        )
    )
    store = TokenStore(tmp_path / "creds.json")
    tokens = await password_login(
        "user@example.com",
        "s3cret",
        auth_url="https://auth.timetta.com",
        client_id="client",
        store=store,
    )
    assert tokens.access_token == "a"
    assert store.load().refresh_token == "r"
    body = route.calls.last.request.read().decode()
    assert "grant_type=password" in body
    assert "username=user%40example.com" in body
    assert "password=s3cret" in body
    assert "client_id=client" in body
    assert "scope=all+offline_access" in body


@respx.mock
async def test_password_login_non_200_raises_with_detail(tmp_path):
    respx.post(TOKEN_EP).mock(
        return_value=httpx.Response(
            400, json={"error": "invalid_grant", "error_description": "bad credentials"}
        )
    )
    store = TokenStore(tmp_path / "creds.json")
    with pytest.raises(TimettaError, match="bad credentials"):
        await password_login(
            "user", "wrong", auth_url="https://auth.timetta.com", client_id="c", store=store
        )
    assert store.load() is None  # nothing persisted on failure


@respx.mock
async def test_password_login_network_error_raises(tmp_path):
    respx.post(TOKEN_EP).mock(side_effect=httpx.ConnectError("boom"))
    store = TokenStore(tmp_path / "creds.json")
    with pytest.raises(TimettaError, match="Network error"):
        await password_login(
            "user", "pw", auth_url="https://auth.timetta.com", client_id="c", store=store
        )


@respx.mock
async def test_password_login_200_without_access_token_raises(tmp_path):
    respx.post(TOKEN_EP).mock(return_value=httpx.Response(200, json={"expires_in": 3600}))
    store = TokenStore(tmp_path / "creds.json")
    with pytest.raises(TimettaError, match="timetta-mcp login"):
        await password_login(
            "user", "pw", auth_url="https://auth.timetta.com", client_id="c", store=store
        )


# ---------------------------------------------------------------------------
# Browser sign-in (web_app, authorization_code + PKCE, manual code paste)
# ---------------------------------------------------------------------------


def test_generate_pkce_challenge_matches_verifier():
    import base64
    import hashlib

    verifier, challenge = generate_pkce()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert challenge == expected and "=" not in challenge


def test_build_browser_authorize_url_uses_web_app_and_fixed_redirect():
    url = build_browser_authorize_url("https://auth.timetta.com", "st", "ch")
    q = parse_qs(urlparse(url).query)
    assert urlparse(url).path == "/connect/authorize"
    assert q["client_id"] == ["web_app"]
    assert q["redirect_uri"] == ["https://app.timetta.com/auth-callback"]
    assert q["scope"] == ["openid profile all"]  # no offline_access
    assert q["response_type"] == ["code"]
    assert q["code_challenge"] == ["ch"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["state"] == ["st"]


def test_parse_redirect_full_url_extracts_code_and_state():
    code, state = parse_redirect(
        "https://app.timetta.com/auth-callback?code=abc&state=xyz"
    )
    assert code == "abc" and state == "xyz"


def test_parse_redirect_bare_code_returns_none_state():
    code, state = parse_redirect("just-a-code")
    assert code == "just-a-code" and state is None


@respx.mock
async def test_exchange_browser_code_posts_authorization_code():
    route = respx.post(TOKEN_EP).mock(
        return_value=httpx.Response(200, json={"access_token": "a", "expires_in": 3600})
    )
    tokens = await exchange_browser_code(TOKEN_EP, "the-code", "the-verifier")
    assert tokens.access_token == "a"
    assert tokens.refresh_token == ""  # web_app issues no refresh token
    body = route.calls.last.request.read().decode()
    assert "grant_type=authorization_code" in body
    assert "client_id=web_app" in body
    assert "code=the-code" in body
    assert "code_verifier=the-verifier" in body


@respx.mock
async def test_browser_login_saves_tokens_with_matching_state(tmp_path):
    respx.post(TOKEN_EP).mock(
        return_value=httpx.Response(200, json={"access_token": "a", "expires_in": 3600})
    )
    store = TokenStore(tmp_path / "creds.json")
    seen = {}

    def opener(url):
        seen["url"] = url

    def prompt(_msg):
        state = parse_qs(urlparse(seen["url"]).query)["state"][0]
        return f"https://app.timetta.com/auth-callback?code=the-code&state={state}"

    tokens = await browser_login(
        auth_url="https://auth.timetta.com", store=store, opener=opener, prompt=prompt
    )
    assert tokens.access_token == "a"
    assert store.load().access_token == "a"
    assert parse_qs(urlparse(seen["url"]).query)["client_id"] == ["web_app"]


async def test_browser_login_state_mismatch_raises(tmp_path):
    store = TokenStore(tmp_path / "creds.json")
    with pytest.raises(TimettaError, match="state"):
        await browser_login(
            auth_url="https://auth.timetta.com",
            store=store,
            opener=lambda url: None,
            prompt=lambda _m: "https://app.timetta.com/auth-callback?code=c&state=WRONG",
        )


async def test_browser_login_empty_paste_raises(tmp_path):
    store = TokenStore(tmp_path / "creds.json")
    with pytest.raises(TimettaError, match="code"):
        await browser_login(
            auth_url="https://auth.timetta.com",
            store=store,
            opener=lambda url: None,
            prompt=lambda _m: "",
        )


async def test_provider_corrupt_file_raises_login_hint(tmp_path):
    path = tmp_path / "creds.json"
    path.write_text("{garbage", encoding="utf-8")
    p = TokenProvider(TokenStore(path), "client")
    with pytest.raises(TimettaError, match="timetta-mcp login"):
        await p.get_token()
