import json

from timetta_mcp import server

SAMPLE_XML = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx" Version="4.0">
  <edmx:DataServices>
    <Schema xmlns="http://docs.oasis-open.org/odata/ns/edm" Namespace="Default">
      <EntityType Name="User">
        <Property Name="id" Type="Edm.Guid" Nullable="false"/>
      </EntityType>
      <EntityContainer Name="Container">
        <EntitySet Name="Users" EntityType="Default.User"/>
      </EntityContainer>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>
"""


class FakeClient:
    def __init__(self):
        self.closed = False
        self.last_query = None

    async def query(self, entity, **kwargs):
        self.last_query = (entity, kwargs)
        return [{"id": "1", "entity": entity}]

    async def fetch_metadata_xml(self):
        return SAMPLE_XML

    async def aclose(self):
        self.closed = True


def _patch(monkeypatch, fake):
    monkeypatch.setattr(server, "get_client", lambda: fake)


async def test_query_odata_returns_json_and_closes(monkeypatch):
    fake = FakeClient()
    _patch(monkeypatch, fake)
    out = await server._query_odata("Users", select="id", top=5)
    data = json.loads(out)
    assert data[0]["entity"] == "Users"
    assert fake.last_query == ("Users", {
        "filter": None, "select": "id", "expand": None,
        "orderby": None, "top": 5, "skip": None,
    })
    assert fake.closed is True


async def test_list_entities_returns_json(monkeypatch):
    _patch(monkeypatch, FakeClient())
    out = await server._list_entities()
    assert json.loads(out) == ["Users"]


async def test_get_entity_schema_returns_json(monkeypatch):
    _patch(monkeypatch, FakeClient())
    out = await server._get_entity_schema("Users")
    assert json.loads(out)["type"] == "User"


async def test_get_entity_schema_unknown_returns_error_text(monkeypatch):
    _patch(monkeypatch, FakeClient())
    out = await server._get_entity_schema("Nope")
    assert out.startswith("Error:")


async def test_missing_token_returns_error_text(monkeypatch):
    monkeypatch.delenv("TIMETTA_API_TOKEN", raising=False)
    out = await server._list_entities()
    assert out.startswith("Error:")
    assert "TIMETTA_API_TOKEN" in out
