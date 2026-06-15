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
        self.last_write = None

    async def query(self, entity, **kwargs):
        self.last_query = (entity, kwargs)
        return [{"id": "1", "entity": entity}]

    async def create(self, entity, data):
        self.last_write = ("create", entity, data)
        return {"id": "new", "entity": entity, **data}

    async def update(self, entity, id, data):
        self.last_write = ("update", entity, id, data)
        return {"id": id, **data}

    async def delete(self, entity, id):
        self.last_write = ("delete", entity, id)
        return None

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


async def test_create_entity_returns_json_and_closes(monkeypatch):
    fake = FakeClient()
    _patch(monkeypatch, fake)
    out = await server._create_entity("Issues", {"name": "T"})
    data = json.loads(out)
    assert data == {"id": "new", "entity": "Issues", "name": "T"}
    assert fake.last_write == ("create", "Issues", {"name": "T"})
    assert fake.closed is True


async def test_update_entity_returns_json(monkeypatch):
    fake = FakeClient()
    _patch(monkeypatch, fake)
    out = await server._update_entity("Issues", "abc", {"name": "T2"})
    assert json.loads(out) == {"id": "abc", "name": "T2"}
    assert fake.last_write == ("update", "Issues", "abc", {"name": "T2"})


async def test_delete_entity_returns_deleted_id(monkeypatch):
    fake = FakeClient()
    _patch(monkeypatch, fake)
    out = await server._delete_entity("Issues", "abc")
    assert json.loads(out) == {"deleted": "abc"}
    assert fake.last_write == ("delete", "Issues", "abc")


async def test_create_entity_missing_token_returns_error(monkeypatch):
    monkeypatch.delenv("TIMETTA_API_TOKEN", raising=False)
    monkeypatch.setattr(server, "get_client", server.get_client)
    out = await server._create_entity("Issues", {"name": "T"})
    assert out.startswith("Error:")
    assert "TIMETTA_API_TOKEN" in out
