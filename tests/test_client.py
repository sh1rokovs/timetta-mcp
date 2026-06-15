import httpx
import pytest
import respx

from timetta_mcp.client import TimettaClient, TimettaError

BASE = "https://api.timetta.com/odata"


@respx.mock
async def test_query_success_sets_header_and_params():
    route = respx.get(f"{BASE}/Users").mock(
        return_value=httpx.Response(200, json={"value": [{"id": "1"}]})
    )
    client = TimettaClient(token="tok")
    rows = await client.query("Users", select="id,name", filter="x eq 1", top=10)

    assert rows == [{"id": "1"}]
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer tok"
    assert req.url.params["$select"] == "id,name"
    assert req.url.params["$filter"] == "x eq 1"
    assert req.url.params["$top"] == "10"
    await client.aclose()


@respx.mock
async def test_query_caps_top_at_200():
    route = respx.get(f"{BASE}/Users").mock(
        return_value=httpx.Response(200, json={"value": []})
    )
    client = TimettaClient(token="t")
    await client.query("Users", top=9999)
    assert route.calls.last.request.url.params["$top"] == "200"
    await client.aclose()


@respx.mock
async def test_query_floors_negative_top_at_zero():
    route = respx.get(f"{BASE}/Users").mock(
        return_value=httpx.Response(200, json={"value": []})
    )
    client = TimettaClient(token="t")
    await client.query("Users", top=-5)
    assert route.calls.last.request.url.params["$top"] == "0"
    await client.aclose()


@respx.mock
async def test_query_401_mentions_token():
    respx.get(f"{BASE}/Users").mock(return_value=httpx.Response(401))
    client = TimettaClient(token="t")
    with pytest.raises(TimettaError, match="TIMETTA_API_TOKEN"):
        await client.query("Users")
    await client.aclose()


@respx.mock
async def test_query_404_mentions_entity():
    respx.get(f"{BASE}/Ghosts").mock(return_value=httpx.Response(404))
    client = TimettaClient(token="t")
    with pytest.raises(TimettaError, match="Ghosts"):
        await client.query("Ghosts")
    await client.aclose()


@respx.mock
async def test_query_500_surfaces_business_message():
    respx.get(f"{BASE}/Users").mock(
        return_value=httpx.Response(500, json={"code": "X", "message": "bad period"})
    )
    client = TimettaClient(token="t")
    with pytest.raises(TimettaError, match="bad period"):
        await client.query("Users")
    await client.aclose()


@respx.mock
async def test_fetch_metadata_xml_returns_text():
    respx.get(f"{BASE}/$metadata").mock(
        return_value=httpx.Response(200, text="<edmx/>")
    )
    client = TimettaClient(token="t")
    assert await client.fetch_metadata_xml() == "<edmx/>"
    await client.aclose()


def test_token_not_in_repr():
    client = TimettaClient(token="super-secret")
    assert "super-secret" not in repr(client)


@respx.mock
async def test_create_posts_body_and_returns_entity():
    route = respx.post(f"{BASE}/Issues").mock(
        return_value=httpx.Response(201, json={"id": "new", "name": "T"})
    )
    client = TimettaClient(token="tok")
    created = await client.create("Issues", {"name": "T"})

    assert created == {"id": "new", "name": "T"}
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer tok"
    assert req.headers["Prefer"] == "return=representation"
    assert req.read() == b'{"name":"T"}'
    await client.aclose()


@respx.mock
async def test_update_patches_by_id_and_returns_body():
    route = respx.patch(f"{BASE}/Issues(abc)").mock(
        return_value=httpx.Response(200, json={"id": "abc", "name": "T2"})
    )
    client = TimettaClient(token="tok")
    updated = await client.update("Issues", "abc", {"name": "T2"})

    assert updated == {"id": "abc", "name": "T2"}
    req = route.calls.last.request
    assert req.method == "PATCH"
    assert req.read() == b'{"name":"T2"}'
    await client.aclose()


@respx.mock
async def test_update_204_returns_confirmation():
    respx.patch(f"{BASE}/Issues(abc)").mock(return_value=httpx.Response(204))
    client = TimettaClient(token="tok")
    updated = await client.update("Issues", "abc", {"name": "T2"})
    assert updated == {"id": "abc", "updated": True}
    await client.aclose()


@respx.mock
async def test_delete_calls_delete_by_id():
    route = respx.delete(f"{BASE}/Issues(abc)").mock(
        return_value=httpx.Response(204)
    )
    client = TimettaClient(token="tok")
    result = await client.delete("Issues", "abc")

    assert result is None
    assert route.calls.last.request.method == "DELETE"
    await client.aclose()


@respx.mock
async def test_400_surfaces_validation_message():
    respx.post(f"{BASE}/Issues").mock(
        return_value=httpx.Response(400, json={"code": "X", "message": "name required"})
    )
    client = TimettaClient(token="t")
    with pytest.raises(TimettaError, match="name required"):
        await client.create("Issues", {})
    await client.aclose()


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
        assert self.tokens, "force_refresh called more times than expected"
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
    assert provider.refreshed is True       # refreshed exactly once
    assert len(provider.tokens) == 1        # popped exactly once
    await client.aclose()
