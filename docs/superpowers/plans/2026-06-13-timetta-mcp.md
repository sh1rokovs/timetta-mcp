# timetta-mcp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python MCP server that exposes Timetta's main OData API to MCP clients as a universal query gateway, launchable via `uvx`.

**Architecture:** Three layers — `@mcp.tool` wrappers (server.py) → `TimettaClient` httpx wrapper (client.py) → Timetta OData. A pure `metadata.py` parses `$metadata` XML into entity/field lists. Token comes from `TIMETTA_API_TOKEN` env; transport is stdio.

**Tech Stack:** Python 3.10+, official `mcp` SDK (FastMCP), `httpx` (async). Tests: `pytest` + `pytest-asyncio` + `respx`. Build/run via `uv`/`uvx` with hatchling.

---

## File Structure

```
timetta-mcp/
├── pyproject.toml          # metadata, deps, entry point, pytest config
├── README.md               # setup, env vars, client wiring
├── src/timetta_mcp/
│   ├── __init__.py
│   ├── metadata.py         # pure $metadata XML → entities/fields
│   ├── client.py           # TimettaClient (httpx, auth, errors)
│   └── server.py           # FastMCP tools + main()
└── tests/
    ├── test_metadata.py
    ├── test_client.py
    └── test_server.py
```

---

## Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `src/timetta_mcp/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "timetta-mcp"
version = "0.1.0"
description = "Timetta time-tracking MCP server"
readme = "README.md"
requires-python = ">=3.10"
dependencies = [
    "mcp>=1.12",
    "httpx>=0.27",
]

[project.scripts]
timetta-mcp = "timetta_mcp.server:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/timetta_mcp"]

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "respx>=0.21",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Create empty package/test init files**

`src/timetta_mcp/__init__.py`:
```python
"""Timetta MCP server."""

__version__ = "0.1.0"
```

`tests/__init__.py`:
```python
```

- [ ] **Step 3: Sync dependencies**

Run: `uv sync`
Expected: resolves and installs `mcp`, `httpx`, plus the `dev` group (`pytest`, `pytest-asyncio`, `respx`). Creates `.venv` and `uv.lock`.

- [ ] **Step 4: Verify the package imports**

Run: `uv run python -c "import timetta_mcp; print(timetta_mcp.__version__)"`
Expected: prints `0.1.0`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock src/timetta_mcp/__init__.py tests/__init__.py
git commit -m "scaffold timetta-mcp package"
```

---

## Task 2: Metadata parser (`metadata.py`)

Pure functions that parse an OData v4 `$metadata` EDMX XML string. No network, no MCP — trivially testable.

**Files:**
- Create: `src/timetta_mcp/metadata.py`
- Test: `tests/test_metadata.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_metadata.py`:
```python
import pytest

from timetta_mcp import metadata

SAMPLE_XML = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx" Version="4.0">
  <edmx:DataServices>
    <Schema xmlns="http://docs.oasis-open.org/odata/ns/edm" Namespace="Default">
      <EntityType Name="User">
        <Key><PropertyRef Name="id"/></Key>
        <Property Name="id" Type="Edm.Guid" Nullable="false"/>
        <Property Name="name" Type="Edm.String"/>
        <NavigationProperty Name="TimeEntries" Type="Collection(Default.TimeEntry)"/>
      </EntityType>
      <EntityType Name="TimeEntry">
        <Property Name="id" Type="Edm.Guid" Nullable="false"/>
        <Property Name="hours" Type="Edm.Double"/>
      </EntityType>
      <EntityContainer Name="Container">
        <EntitySet Name="Users" EntityType="Default.User"/>
        <EntitySet Name="TimeEntries" EntityType="Default.TimeEntry"/>
      </EntityContainer>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>
"""


def test_parse_entities_lists_entity_sets():
    assert metadata.parse_entities(SAMPLE_XML) == ["Users", "TimeEntries"]


def test_parse_entity_schema_returns_properties():
    schema = metadata.parse_entity_schema(SAMPLE_XML, "Users")
    assert schema["entity"] == "Users"
    assert schema["type"] == "User"
    assert {"name": "id", "type": "Edm.Guid", "nullable": False} in schema["properties"]
    assert {"name": "name", "type": "Edm.String", "nullable": True} in schema["properties"]
    assert {"name": "TimeEntries", "type": "Collection(Default.TimeEntry)"} in schema["navigationProperties"]


def test_parse_entity_schema_unknown_entity_raises():
    with pytest.raises(ValueError, match="Nope"):
        metadata.parse_entity_schema(SAMPLE_XML, "Nope")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_metadata.py -v`
Expected: FAIL — `AttributeError: module 'timetta_mcp.metadata' has no attribute 'parse_entities'` (module/functions don't exist yet).

- [ ] **Step 3: Write the implementation**

`src/timetta_mcp/metadata.py`:
```python
"""Parse Timetta OData $metadata (EDMX XML) into compact entity/field info."""

from __future__ import annotations

import xml.etree.ElementTree as ET

# ElementTree "{*}Tag" matches the local tag name in any XML namespace,
# so we don't have to hard-code the OData EDMX namespaces.


def parse_entities(metadata_xml: str) -> list[str]:
    """Return the names of all queryable EntitySets (e.g. 'Users')."""
    root = ET.fromstring(metadata_xml)
    return [es.get("Name") for es in root.iter("{*}EntitySet") if es.get("Name")]


def parse_entity_schema(metadata_xml: str, entity: str) -> dict:
    """Return properties and navigation properties for one EntitySet.

    Raises ValueError if the entity set is not found.
    """
    root = ET.fromstring(metadata_xml)

    type_ref = None
    for es in root.iter("{*}EntitySet"):
        if es.get("Name") == entity:
            type_ref = es.get("EntityType")
            break
    if type_ref is None:
        raise ValueError(f"Unknown entity: {entity}")

    type_name = type_ref.split(".")[-1]
    for et in root.iter("{*}EntityType"):
        if et.get("Name") == type_name:
            properties = [
                {
                    "name": p.get("Name"),
                    "type": p.get("Type"),
                    "nullable": p.get("Nullable", "true") != "false",
                }
                for p in et.iter("{*}Property")
            ]
            navigation = [
                {"name": n.get("Name"), "type": n.get("Type")}
                for n in et.iter("{*}NavigationProperty")
            ]
            return {
                "entity": entity,
                "type": type_name,
                "properties": properties,
                "navigationProperties": navigation,
            }

    raise ValueError(f"Unknown entity type for: {entity}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_metadata.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/timetta_mcp/metadata.py tests/test_metadata.py
git commit -m "metadata parser for odata $metadata"
```

---

## Task 3: Timetta client (`client.py`)

`TimettaClient` wraps `httpx.AsyncClient`: builds OData query params, sets the Bearer header, maps HTTP errors to a single `TimettaError` with model-safe messages, and never leaks the token.

**Files:**
- Create: `src/timetta_mcp/client.py`
- Test: `tests/test_client.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_client.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'timetta_mcp.client'`.

- [ ] **Step 3: Write the implementation**

`src/timetta_mcp/client.py`:
```python
"""HTTP client for the Timetta main OData API (Bearer Token API auth)."""

from __future__ import annotations

import httpx

DEFAULT_BASE_URL = "https://api.timetta.com/odata"
MAX_TOP = 200


class TimettaError(Exception):
    """Timetta API error with a message safe to show the model (no token)."""


class TimettaClient:
    def __init__(self, token: str, base_url: str = DEFAULT_BASE_URL) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )

    def __repr__(self) -> str:  # never leak the token
        return f"TimettaClient(base_url={self._base!r})"

    async def query(
        self,
        entity: str,
        *,
        filter: str | None = None,
        select: str | None = None,
        expand: str | None = None,
        orderby: str | None = None,
        top: int = 50,
        skip: int | None = None,
    ) -> list[dict]:
        params: dict[str, str | int] = {}
        if filter:
            params["$filter"] = filter
        if select:
            params["$select"] = select
        if expand:
            params["$expand"] = expand
        if orderby:
            params["$orderby"] = orderby
        params["$top"] = min(int(top), MAX_TOP)
        if skip is not None:
            params["$skip"] = int(skip)

        resp = await self._get(f"{self._base}/{entity}", params=params, what=entity)
        return resp.json().get("value", [])

    async def fetch_metadata_xml(self) -> str:
        resp = await self._get(f"{self._base}/$metadata", params=None, what="$metadata")
        return resp.text

    async def _get(self, url, params, what) -> httpx.Response:
        try:
            resp = await self._client.get(url, params=params)
        except httpx.RequestError as exc:
            raise TimettaError(f"Network error talking to Timetta: {exc}") from exc
        self._raise_for_status(resp, what)
        return resp

    @staticmethod
    def _raise_for_status(resp: httpx.Response, what: str) -> None:
        code = resp.status_code
        if code < 400:
            return
        if code == 401:
            raise TimettaError(
                "Unauthorized — check TIMETTA_API_TOKEN (invalid or expired)"
            )
        if code in (403, 404):
            raise TimettaError(f"No access or not found: {what}")
        if code == 500:
            try:
                message = resp.json().get("message") or resp.text
            except Exception:
                message = resp.text
            raise TimettaError(f"Timetta business error: {message}")
        raise TimettaError(f"Timetta returned HTTP {code}")

    async def aclose(self) -> None:
        await self._client.aclose()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_client.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add src/timetta_mcp/client.py tests/test_client.py
git commit -m "timetta odata http client with error mapping"
```

---

## Task 4: MCP server & tools (`server.py`)

FastMCP server exposing three tools. Tool bodies delegate to plain helper coroutines (`_list_entities`, `_get_entity_schema`, `_query_odata`) so they are testable without going through the decorator. `get_client()` reads env and is monkeypatched in tests. Every helper catches errors and returns them as text, and always closes the client.

**Files:**
- Create: `src/timetta_mcp/server.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_server.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'timetta_mcp.server'`.

- [ ] **Step 3: Write the implementation**

`src/timetta_mcp/server.py`:
```python
"""FastMCP server exposing Timetta OData as MCP tools."""

from __future__ import annotations

import json
import os

from mcp.server.fastmcp import FastMCP

from . import metadata
from .client import DEFAULT_BASE_URL, TimettaClient, TimettaError

mcp = FastMCP("timetta")


def get_client() -> TimettaClient:
    token = os.environ.get("TIMETTA_API_TOKEN")
    if not token:
        raise TimettaError("TIMETTA_API_TOKEN environment variable is not set")
    base_url = os.environ.get("TIMETTA_BASE_URL", DEFAULT_BASE_URL)
    return TimettaClient(token=token, base_url=base_url)


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


async def _list_entities() -> str:
    try:
        client = get_client()
    except TimettaError as exc:
        return f"Error: {exc}"
    try:
        xml = await client.fetch_metadata_xml()
        return _dumps(metadata.parse_entities(xml))
    except Exception as exc:
        return f"Error: {exc}"
    finally:
        await client.aclose()


async def _get_entity_schema(entity: str) -> str:
    try:
        client = get_client()
    except TimettaError as exc:
        return f"Error: {exc}"
    try:
        xml = await client.fetch_metadata_xml()
        return _dumps(metadata.parse_entity_schema(xml, entity))
    except Exception as exc:
        return f"Error: {exc}"
    finally:
        await client.aclose()


async def _query_odata(
    entity: str,
    filter: str | None = None,
    select: str | None = None,
    expand: str | None = None,
    orderby: str | None = None,
    top: int = 50,
    skip: int | None = None,
) -> str:
    try:
        client = get_client()
    except TimettaError as exc:
        return f"Error: {exc}"
    try:
        rows = await client.query(
            entity,
            filter=filter,
            select=select,
            expand=expand,
            orderby=orderby,
            top=top,
            skip=skip,
        )
        return _dumps(rows)
    except Exception as exc:
        return f"Error: {exc}"
    finally:
        await client.aclose()


@mcp.tool()
async def list_entities() -> str:
    """List the queryable Timetta OData entities (EntitySet names)."""
    return await _list_entities()


@mcp.tool()
async def get_entity_schema(entity: str) -> str:
    """Get fields, types and navigation properties for one Timetta entity
    (e.g. 'Users', 'TimeEntries'). Call this before query_odata to learn real
    field names."""
    return await _get_entity_schema(entity)


@mcp.tool()
async def query_odata(
    entity: str,
    filter: str | None = None,
    select: str | None = None,
    expand: str | None = None,
    orderby: str | None = None,
    top: int = 50,
    skip: int | None = None,
) -> str:
    """Query a Timetta OData entity.

    Args use OData semantics ($filter, $select, $expand, $orderby, $top, $skip)
    without the leading '$'. Example: entity='TimeEntries',
    filter='Date ge 2024-01-01', expand='Project,User', select='Date,Hours'.
    top defaults to 50 and is capped at 200; use skip to paginate.
    Returns a JSON array of rows, or 'Error: ...' on failure."""
    return await _query_odata(
        entity,
        filter=filter,
        select=select,
        expand=expand,
        orderby=orderby,
        top=top,
        skip=skip,
    )


def main() -> None:
    """Console entry point — runs the server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_server.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: PASS (all 15 tests)

- [ ] **Step 6: Commit**

```bash
git add src/timetta_mcp/server.py tests/test_server.py
git commit -m "fastmcp server with odata tools"
```

---

## Task 5: Launch verification & README

Confirm the server actually starts via the entry point and `uvx`, then document usage.

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Verify the entry point starts and lists tools**

Run:
```bash
TIMETTA_API_TOKEN=dummy uv run python -c "import asyncio; from timetta_mcp.server import mcp; print(sorted(t.name for t in asyncio.run(mcp.list_tools())))"
```
Expected: prints `['get_entity_schema', 'list_entities', 'query_odata']`

(On Windows PowerShell use: `$env:TIMETTA_API_TOKEN='dummy'; uv run python -c "..."`)

- [ ] **Step 2: Verify launch via uvx from the local project**

Run: `printf '' | TIMETTA_API_TOKEN=dummy uvx --from . timetta-mcp`
Expected: process starts a stdio server, reads empty stdin (no JSON-RPC), and exits cleanly with no traceback. (A clean exit / EOF is success; any Python traceback is a failure to investigate.)

- [ ] **Step 3: Write `README.md`**

Replace `README.md` with:
````markdown
# timetta-mcp

MCP server exposing the [Timetta](https://timetta.com) main OData API to MCP
clients (Claude Desktop, etc.) as a universal query gateway.

## Tools

- `list_entities()` — list queryable OData entities.
- `get_entity_schema(entity)` — fields, types and navigation properties of an
  entity. Call this first to learn real field names.
- `query_odata(entity, filter?, select?, expand?, orderby?, top?, skip?)` —
  query an entity using OData semantics (without the leading `$`). `top`
  defaults to 50, capped at 200.

## Configuration

| Env var | Required | Default | Description |
|---|---|---|---|
| `TIMETTA_API_TOKEN` | yes | — | Static Token API value (Bearer), TTL 1 year |
| `TIMETTA_BASE_URL` | no | `https://api.timetta.com/odata` | OData base URL |

## Run

Locally from a checkout:

```bash
uvx --from . timetta-mcp
```

From the repository:

```bash
uvx --from git+https://github.com/sh1rokovs/timetta-mcp timetta-mcp
```

## Claude Desktop config

```json
{
  "mcpServers": {
    "timetta": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/sh1rokovs/timetta-mcp", "timetta-mcp"],
      "env": { "TIMETTA_API_TOKEN": "YOUR_TOKEN" }
    }
  }
}
```

## Development

```bash
uv sync
uv run pytest
```

## Example

```
get_entity_schema("TimeEntries")
query_odata("TimeEntries", filter="Date ge 2024-01-01 and Date le 2024-01-31",
            expand="Project,User", select="Date,Hours,Comment")
```
````

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: usage and uvx launch instructions"
git push
```

---

## Notes for the implementer

- **DRY:** `_get` centralizes HTTP + error mapping for both `query` and `fetch_metadata_xml`.
- **YAGNI:** no Reporting API, no OAuth, no write tools, no cross-run caching — out of scope per the spec.
- **TDD:** every task writes tests first and watches them fail before implementing.
- **Security:** the token is read only from env, set only as a header, excluded from `__repr__`, and never included in error text.
- **Windows:** the dev shell is PowerShell; for env-prefixed commands use `$env:VAR='...'; <cmd>` instead of the `VAR=... cmd` POSIX form shown in Task 5.
```
