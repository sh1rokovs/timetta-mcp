import httpx
import respx

from timetta_mcp import http_auth, server


# --------------------------------------------------------------------------- #
# Transport / auth wiring                                                     #
# --------------------------------------------------------------------------- #


def test_resolve_transport_defaults_to_stdio(monkeypatch):
    monkeypatch.delenv("TIMETTA_MCP_TRANSPORT", raising=False)
    monkeypatch.setattr(server.sys, "argv", ["timetta-mcp"])
    assert server._resolve_transport() == "stdio"


def test_resolve_transport_env_http(monkeypatch):
    monkeypatch.setenv("TIMETTA_MCP_TRANSPORT", "http")
    monkeypatch.setattr(server.sys, "argv", ["timetta-mcp"])
    assert server._resolve_transport() == "http"


def test_resolve_transport_serve_http_argv(monkeypatch):
    monkeypatch.delenv("TIMETTA_MCP_TRANSPORT", raising=False)
    monkeypatch.setattr(server.sys, "argv", ["timetta-mcp", "serve-http"])
    assert server._resolve_transport() == "http"


def test_build_auth_none_for_static_token(monkeypatch):
    monkeypatch.setenv("TIMETTA_API_TOKEN", "tok")
    monkeypatch.setenv("TIMETTA_MCP_TRANSPORT", "http")
    assert server._build_auth() is None


def test_build_auth_none_for_stdio(monkeypatch):
    monkeypatch.delenv("TIMETTA_API_TOKEN", raising=False)
    monkeypatch.delenv("TIMETTA_MCP_TRANSPORT", raising=False)
    monkeypatch.setattr(server.sys, "argv", ["timetta-mcp"])
    assert server._build_auth() is None


def test_build_auth_returns_proxy_for_http(monkeypatch):
    monkeypatch.delenv("TIMETTA_API_TOKEN", raising=False)
    monkeypatch.setenv("TIMETTA_MCP_TRANSPORT", "http")
    monkeypatch.setattr(server.sys, "argv", ["timetta-mcp"])
    auth = server._build_auth()
    assert auth is not None


# --------------------------------------------------------------------------- #
# Bearer plumbing into the OData client                                       #
# --------------------------------------------------------------------------- #


@respx.mock
async def test_get_client_uses_request_bearer(monkeypatch):
    monkeypatch.delenv("TIMETTA_API_TOKEN", raising=False)
    monkeypatch.setattr(server, "_request_bearer_token", lambda: "client-bearer")
    route = respx.get("https://api.timetta.com/odata/Users").mock(
        return_value=httpx.Response(200, json={"value": []})
    )
    client = server.get_client()
    await client.query("Users")
    assert route.calls.last.request.headers["Authorization"] == "Bearer client-bearer"
    await client.aclose()


def test_request_bearer_token_none_without_context(monkeypatch):
    # Outside an authenticated HTTP request there is no access token.
    assert server._request_bearer_token() is None


# --------------------------------------------------------------------------- #
# Token verifier (validation by use)                                          #
# --------------------------------------------------------------------------- #


@respx.mock
async def test_verify_token_valid(monkeypatch):
    monkeypatch.delenv("TIMETTA_AUTH_PROBE_PATH", raising=False)
    respx.get("https://api.timetta.com/odata/Users").mock(
        return_value=httpx.Response(200, json={"value": []})
    )
    verifier = http_auth.TimettaTokenVerifier("https://api.timetta.com/odata")
    token = await verifier.verify_token("good")
    assert token is not None
    assert token.token == "good"


@respx.mock
async def test_verify_token_rejects_401(monkeypatch):
    monkeypatch.delenv("TIMETTA_AUTH_PROBE_PATH", raising=False)
    respx.get("https://api.timetta.com/odata/Users").mock(
        return_value=httpx.Response(401)
    )
    verifier = http_auth.TimettaTokenVerifier("https://api.timetta.com/odata")
    assert await verifier.verify_token("bad") is None


async def test_verify_token_rejects_empty():
    verifier = http_auth.TimettaTokenVerifier("https://api.timetta.com/odata")
    assert await verifier.verify_token("") is None
    assert await verifier.verify_token("   ") is None


@respx.mock
async def test_verify_token_caches_validation(monkeypatch):
    monkeypatch.delenv("TIMETTA_AUTH_PROBE_PATH", raising=False)
    route = respx.get("https://api.timetta.com/odata/Users").mock(
        return_value=httpx.Response(200, json={"value": []})
    )
    verifier = http_auth.TimettaTokenVerifier("https://api.timetta.com/odata")
    await verifier.verify_token("good")
    await verifier.verify_token("good")
    assert route.call_count == 1  # second call served from the TTL cache


# --------------------------------------------------------------------------- #
# URL helpers                                                                 #
# --------------------------------------------------------------------------- #


def test_public_base_url_from_host_port(monkeypatch):
    monkeypatch.delenv("TIMETTA_PUBLIC_URL", raising=False)
    monkeypatch.setenv("TIMETTA_MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("TIMETTA_MCP_PORT", "9000")
    assert http_auth.public_base_url() == "http://127.0.0.1:9000"


def test_public_base_url_explicit_override(monkeypatch):
    monkeypatch.setenv("TIMETTA_PUBLIC_URL", "https://timetta.example.com/")
    assert http_auth.public_base_url() == "https://timetta.example.com"


def test_http_path_normalizes_leading_slash(monkeypatch):
    monkeypatch.setenv("TIMETTA_MCP_PATH", "mcp")
    assert http_auth.http_path() == "/mcp"
