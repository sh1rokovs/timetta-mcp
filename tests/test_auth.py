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
