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


# --------------------------------------------------------------------------- #
# Composite tools                                                             #
# --------------------------------------------------------------------------- #


class CompositeFakeClient(FakeClient):
    """FakeClient with catalog + upload behavior for composite tool tests."""

    base_url = "https://example.test/odata"

    def __init__(self, *, issue_types=None, priorities=None, link_types=None, tasks=None):
        super().__init__()
        self._issue_types = issue_types if issue_types is not None else [
            {"projectId": "p1", "issueType": {"id": "t-task", "name": "Task", "code": "TASK"}},
            {"projectId": "p1", "issueType": {"id": "t-bug", "name": "Bug", "code": "BUG"}},
        ]
        self._priorities = priorities if priorities is not None else [
            {"id": "pr-normal", "name": "Normal", "code": "NORMAL", "isDefault": True},
        ]
        self._link_types = link_types if link_types is not None else [
            {"id": "lt-impl", "name": "Реализация"},
        ]
        self._tasks = tasks if tasks is not None else [
            {"id": "task-be", "name": "Backend", "fullPath": "Root/Backend", "projectId": "p1"},
        ]
        self.uploaded = None

    async def query_all(self, entity, **kwargs):
        self.last_query = (entity, kwargs)
        return {
            "ProjectIssueTypes": self._issue_types,
            "DirectoryEntries": self._priorities,
            "IssueLinkTypes": self._link_types,
            "ProjectTasks": self._tasks,
        }.get(entity, [])

    async def query(self, entity, **kwargs):
        self.last_query = (entity, kwargs)
        if entity == "Issues":
            return [{"id": "i-1", "code": "ISSUE-1"}]
        return []

    async def get_by_id(self, entity, id):
        return {"id": id, "code": f"{entity}-{id}"}

    async def create(self, entity, data):
        self.last_write = ("create", entity, data)
        if entity == "Issues":
            return {"id": "i-new", "code": "NEW-1", **data}
        if entity == "IssueLinks":
            return {"id": "link-1", **data}
        return {"id": "x", **data}

    async def upload_file(self, path, *, entity_type, entity_id, file_field="attachment", filename=None):
        self.uploaded = {"path": path, "entity_type": entity_type, "entity_id": entity_id, "filename": filename}
        return {"id": "file-1", "name": filename or "f.txt"}


async def test_create_issue_resolves_type_and_priority(monkeypatch):
    fake = CompositeFakeClient()
    _patch(monkeypatch, fake)
    out = await server._create_issue(
        "Fix login", "desc", "bug", "p1", None, None, None, None, None
    )
    data = json.loads(out)
    assert data["created"] is True
    assert data["id"] == "i-new"
    assert data["key"] == "NEW-1"
    assert data["url"] == "https://example.test/odata/Issues(i-new)"
    assert data["typeCode"] == "BUG"
    assert data["priorityCode"] == "NORMAL"
    body = fake.last_write[2]
    assert body["typeId"] == "t-bug"
    assert body["priorityId"] == "pr-normal"
    assert body["projectId"] == "p1"
    assert fake.closed is True


async def test_create_issue_uses_env_defaults(monkeypatch):
    fake = CompositeFakeClient()
    _patch(monkeypatch, fake)
    monkeypatch.setenv("TIMETTA_DEFAULT_PROJECT_ID", "p1")
    monkeypatch.setenv("TIMETTA_DEFAULT_ASSIGNEE_ID", "user-7")
    out = await server._create_issue(
        "Task", "", "task", None, None, None, None, None, None
    )
    assert json.loads(out)["created"] is True
    assert fake.last_write[2]["assignedId"] == "user-7"


async def test_create_issue_project_task_hint(monkeypatch):
    fake = CompositeFakeClient()
    _patch(monkeypatch, fake)
    out = await server._create_issue(
        "Task", "", "task", "p1", None, None, "backend|root", None, None
    )
    data = json.loads(out)
    assert data["projectTaskId"] == "task-be"
    assert fake.last_write[2]["projectTaskId"] == "task-be"


async def test_create_issue_missing_project_returns_error(monkeypatch):
    fake = CompositeFakeClient()
    _patch(monkeypatch, fake)
    monkeypatch.delenv("TIMETTA_DEFAULT_PROJECT_ID", raising=False)
    out = await server._create_issue("T", "", "task", None, None, None, None, None, None)
    assert out.startswith("Error:")
    assert "project_id" in out


async def test_create_issue_unsupported_type_returns_error(monkeypatch):
    fake = CompositeFakeClient()  # catalog has only TASK/BUG
    _patch(monkeypatch, fake)
    out = await server._create_issue("T", "", "epic", "p1", None, None, None, None, None)
    assert out.startswith("Error:")
    assert "EPIC" in out


async def test_link_issues_resolves_link_type(monkeypatch):
    fake = CompositeFakeClient()
    _patch(monkeypatch, fake)
    out = await server._link_issues("s-1", "d-1", "Реализация")
    data = json.loads(out)
    assert data["linked"] is True
    assert data["linkTypeId"] == "lt-impl"
    assert fake.last_write == ("create", "IssueLinks", {
        "sourceId": "s-1", "destinationId": "d-1", "linkTypeId": "lt-impl",
    })
    assert fake.closed is True


async def test_attach_file_resolves_issue_and_uploads(monkeypatch):
    fake = CompositeFakeClient()
    _patch(monkeypatch, fake)
    out = await server._attach_file("ISSUE-1", "/tmp/report.pdf", "report.pdf")
    data = json.loads(out)
    assert data["attached"] is True
    assert data["issueId"] == "i-1"
    assert data["file"]["id"] == "file-1"
    assert fake.uploaded["entity_type"] == "Issue"
    assert fake.uploaded["entity_id"] == "i-1"
    assert fake.closed is True
