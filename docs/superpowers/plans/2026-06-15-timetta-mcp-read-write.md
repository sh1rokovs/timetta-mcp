# timetta-mcp read-write Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add generic create/update/delete MCP tools to timetta-mcp so clients can write to any Timetta OData entity, not just query it.

**Architecture:** Generalize the client's single GET helper into a method-agnostic `_send`, add `create`/`update`/`delete` client methods, extend error-body parsing to HTTP 400, and expose three new MCP tools (`create_entity`/`update_entity`/`delete_entity`) following the existing wrapper pattern.

**Tech Stack:** Python 3.10+, httpx (async), mcp/FastMCP, pytest + pytest-asyncio + respx.

---

## File Structure

- Modify: `src/timetta_mcp/client.py` — refactor `_get`→`_send`, add `create`/`update`/`delete`, extend `_raise_for_status` to 400.
- Modify: `src/timetta_mcp/server.py` — add `_create_entity`/`_update_entity`/`_delete_entity` helpers + `@mcp.tool()` wrappers.
- Modify: `tests/test_client.py` — write-method + 400 error tests.
- Modify: `tests/test_server.py` — extend `FakeClient`, add tool tests.
- Modify: `README.md` — read-write framing + new tools.

All work happens on branch `feat/timetta-mcp-read-write` (already created).

---

### Task 1: Refactor `_get` into method-agnostic `_send`

Pure refactor — existing tests must stay green, no new test.

**Files:**
- Modify: `src/timetta_mcp/client.py:50-68`

- [ ] **Step 1: Replace `_get` with `_send` and update its callers**

In `client.py`, change the `query` call site (line 50) from:

```python
        resp = await self._get(f"{self._base}/{entity}", params=params, what=entity)
        return resp.json().get("value", [])
```

to:

```python
        resp = await self._send("GET", f"{self._base}/{entity}", params=params, what=entity)
        return resp.json().get("value", [])
```

Change `fetch_metadata_xml` (line 54) from:

```python
        resp = await self._get(f"{self._base}/$metadata", params=None, what="$metadata")
        return resp.text
```

to:

```python
        resp = await self._send("GET", f"{self._base}/$metadata", what="$metadata")
        return resp.text
```

Replace the whole `_get` method (lines 57-68) with:

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
        try:
            resp = await self._client.request(
                method, url, params=params, json=json, headers=headers
            )
        except httpx.RequestError as exc:
            raise TimettaError(f"Network error talking to Timetta: {exc}") from exc
        self._raise_for_status(resp, what)
        return resp
```

- [ ] **Step 2: Run existing tests to verify the refactor is green**

Run: `uv run pytest tests/test_client.py -v`
Expected: PASS (all existing client tests, including 401/404/500 mapping).

- [ ] **Step 3: Commit**

```bash
git add src/timetta_mcp/client.py
git commit -m "refactor: method-agnostic _send in client"
```

---

### Task 2: Client `create` (POST)

**Files:**
- Modify: `src/timetta_mcp/client.py` (add method after `query`)
- Test: `tests/test_client.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_client.py`:

```python
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
    assert req.read() == b'{"name": "T"}'
    await client.aclose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_client.py::test_create_posts_body_and_returns_entity -v`
Expected: FAIL with `AttributeError: 'TimettaClient' object has no attribute 'create'`.

- [ ] **Step 3: Write minimal implementation**

In `client.py`, add this method directly after `query` (before `fetch_metadata_xml`):

```python
    async def create(self, entity: str, data: dict) -> dict:
        resp = await self._send(
            "POST",
            f"{self._base}/{entity}",
            json=data,
            headers={"Prefer": "return=representation"},
            what=entity,
        )
        return resp.json()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_client.py::test_create_posts_body_and_returns_entity -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/timetta_mcp/client.py tests/test_client.py
git commit -m "feat: client.create"
```

---

### Task 3: Client `update` (PATCH, 200 body and 204 confirmation)

**Files:**
- Modify: `src/timetta_mcp/client.py` (add method after `create`)
- Test: `tests/test_client.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_client.py`:

```python
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
    assert req.read() == b'{"name": "T2"}'
    await client.aclose()


@respx.mock
async def test_update_204_returns_confirmation():
    respx.patch(f"{BASE}/Issues(abc)").mock(return_value=httpx.Response(204))
    client = TimettaClient(token="tok")
    updated = await client.update("Issues", "abc", {"name": "T2"})
    assert updated == {"id": "abc", "updated": True}
    await client.aclose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_client.py -k update -v`
Expected: FAIL with `AttributeError: 'TimettaClient' object has no attribute 'update'`.

- [ ] **Step 3: Write minimal implementation**

In `client.py`, add this method directly after `create`:

```python
    async def update(self, entity: str, id: str, data: dict) -> dict:
        resp = await self._send(
            "PATCH",
            f"{self._base}/{entity}({id})",
            json=data,
            headers={"Prefer": "return=representation"},
            what=entity,
        )
        if resp.status_code == 204 or not resp.content:
            return {"id": id, "updated": True}
        return resp.json()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_client.py -k update -v`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add src/timetta_mcp/client.py tests/test_client.py
git commit -m "feat: client.update"
```

---

### Task 4: Client `delete` (DELETE)

**Files:**
- Modify: `src/timetta_mcp/client.py` (add method after `update`)
- Test: `tests/test_client.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_client.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_client.py::test_delete_calls_delete_by_id -v`
Expected: FAIL with `AttributeError: 'TimettaClient' object has no attribute 'delete'`.

- [ ] **Step 3: Write minimal implementation**

In `client.py`, add this method directly after `update`:

```python
    async def delete(self, entity: str, id: str) -> None:
        await self._send(
            "DELETE",
            f"{self._base}/{entity}({id})",
            what=entity,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_client.py::test_delete_calls_delete_by_id -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/timetta_mcp/client.py tests/test_client.py
git commit -m "feat: client.delete"
```

---

### Task 5: Surface HTTP 400 validation messages

**Files:**
- Modify: `src/timetta_mcp/client.py:81` (the `if code == 500:` branch)
- Test: `tests/test_client.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_client.py`:

```python
@respx.mock
async def test_400_surfaces_validation_message():
    respx.post(f"{BASE}/Issues").mock(
        return_value=httpx.Response(400, json={"code": "X", "message": "name required"})
    )
    client = TimettaClient(token="t")
    with pytest.raises(TimettaError, match="name required"):
        await client.create("Issues", {})
    await client.aclose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_client.py::test_400_surfaces_validation_message -v`
Expected: FAIL — raised message is "Timetta returned HTTP 400" (the catch-all), not "name required".

- [ ] **Step 3: Write minimal implementation**

In `client.py`, change the business-error branch from:

```python
        if code == 500:
```

to:

```python
        if code in (400, 500):
```

(The body-parsing block underneath is unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_client.py::test_400_surfaces_validation_message -v`
Expected: PASS

- [ ] **Step 5: Run the full client suite**

Run: `uv run pytest tests/test_client.py -v`
Expected: PASS (all client tests)

- [ ] **Step 6: Commit**

```bash
git add src/timetta_mcp/client.py tests/test_client.py
git commit -m "feat: surface HTTP 400 validation messages"
```

---

### Task 6: Server write tools (`create_entity`/`update_entity`/`delete_entity`)

**Files:**
- Modify: `tests/test_server.py` (extend `FakeClient`, add tests)
- Modify: `src/timetta_mcp/server.py` (add helpers + tool wrappers)

- [ ] **Step 1: Extend `FakeClient` and write failing tests**

In `tests/test_server.py`, add three methods to `FakeClient` (after `query`, before `fetch_metadata_xml`):

```python
    async def create(self, entity, data):
        self.last_write = ("create", entity, data)
        return {"id": "new", "entity": entity, **data}

    async def update(self, entity, id, data):
        self.last_write = ("update", entity, id, data)
        return {"id": id, **data}

    async def delete(self, entity, id):
        self.last_write = ("delete", entity, id)
        return None
```

And initialize the attribute in `FakeClient.__init__` (add after `self.last_query = None`):

```python
        self.last_write = None
```

Then append these tests to `tests/test_server.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server.py -k "entity" -v`
Expected: FAIL with `AttributeError: module 'timetta_mcp.server' has no attribute '_create_entity'`.

- [ ] **Step 3: Write minimal implementation**

In `server.py`, add the three private helpers after `_query_odata` (before the `@mcp.tool()` definitions):

```python
async def _create_entity(entity: str, data: dict) -> str:
    try:
        client = get_client()
    except TimettaError as exc:
        return f"Error: {exc}"
    try:
        return _dumps(await client.create(entity, data))
    except Exception as exc:
        return f"Error: {exc}"
    finally:
        await client.aclose()


async def _update_entity(entity: str, id: str, data: dict) -> str:
    try:
        client = get_client()
    except TimettaError as exc:
        return f"Error: {exc}"
    try:
        return _dumps(await client.update(entity, id, data))
    except Exception as exc:
        return f"Error: {exc}"
    finally:
        await client.aclose()


async def _delete_entity(entity: str, id: str) -> str:
    try:
        client = get_client()
    except TimettaError as exc:
        return f"Error: {exc}"
    try:
        await client.delete(entity, id)
        return _dumps({"deleted": id})
    except Exception as exc:
        return f"Error: {exc}"
    finally:
        await client.aclose()
```

Then add the public tool wrappers after the existing `query_odata` tool (at the end of the tools block, before `def main`):

```python
@mcp.tool()
async def create_entity(entity: str, data: dict) -> str:
    """Create a new record in a Timetta OData entity.

    `data` is a JSON object of field name -> value. Call get_entity_schema(entity)
    first to learn the real field names. Returns the created record as JSON, or
    'Error: ...' on failure."""
    return await _create_entity(entity, data)


@mcp.tool()
async def update_entity(entity: str, id: str, data: dict) -> str:
    """Update an existing record by id in a Timetta OData entity (PATCH).

    `data` contains only the fields to change. Call get_entity_schema(entity)
    first to learn the real field names. Returns the updated record as JSON, or
    'Error: ...' on failure."""
    return await _update_entity(entity, id, data)


@mcp.tool()
async def delete_entity(entity: str, id: str) -> str:
    """Delete a record by id from a Timetta OData entity.

    Returns {"deleted": "<id>"} as JSON on success, or 'Error: ...' on failure."""
    return await _delete_entity(entity, id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_server.py -k "entity" -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: PASS (all client + server + metadata tests)

- [ ] **Step 6: Commit**

```bash
git add src/timetta_mcp/server.py tests/test_server.py
git commit -m "feat: create/update/delete MCP tools"
```

---

### Task 7: Update README to read-write

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the intro and Tools list**

Change the opening sentence (lines 3-5) from:

```markdown
MCP server exposing the [Timetta](https://timetta.com) main OData API to MCP
clients (Claude Desktop, Claude Code, Codex, Gemini CLI, etc.) as a universal
query gateway.
```

to:

```markdown
MCP server exposing the [Timetta](https://timetta.com) main OData API to MCP
clients (Claude Desktop, Claude Code, Codex, Gemini CLI, etc.) as a universal
read-write gateway.
```

In the `## Tools` list, after the `query_odata(...)` bullet, add:

```markdown
- `create_entity(entity, data)` — create a record (POST). `data` is a JSON
  object of field -> value.
- `update_entity(entity, id, data)` — update a record by id (PATCH). `data`
  holds only the fields to change.
- `delete_entity(entity, id)` — delete a record by id (DELETE).
```

- [ ] **Step 2: Add a permissions note under Configuration**

Immediately after the configuration table (after the `TIMETTA_BASE_URL` row), add:

```markdown

The server can create, update and delete records. Effective permissions are
governed entirely by the token — use a read-only Timetta token if write access
is not needed.
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: read-write tools and permissions note"
```

---

### Task 8: Final verification

- [ ] **Step 1: Run the complete test suite**

Run: `uv run pytest -v`
Expected: PASS — all tests green (existing read tests + new write tests).

- [ ] **Step 2: Confirm no read-only leftovers in README**

Run: `git grep -n "read-only" README.md`
Expected: only the recommendation line ("use a read-only Timetta token …"); no stale "read-only gateway" framing.

---

## Self-Review

**Spec coverage:**
- Client create/update/delete → Tasks 2, 3, 4. ✓
- `_get`→`_send` refactor with GET callers preserved → Task 1. ✓
- 400 error-body parsing → Task 5. ✓
- Three server tools with existing wrapper pattern → Task 6. ✓
- Tests (client write methods, 400, server tools success + error branch) → Tasks 2-6. ✓
- README read-write + permissions note → Task 7. ✓
- Out-of-scope items (env gating, allowlist, actions, entity-specific tools, batch) → not present. ✓

**Placeholder scan:** No TBD/TODO/vague steps; every code step shows full code. ✓

**Type consistency:** Method names `create`/`update`/`delete` consistent between client (Tasks 2-4), `FakeClient`, and server helpers `_create_entity`/`_update_entity`/`_delete_entity` (Task 6). `_send` signature defined in Task 1 is used unchanged by Tasks 2-4. `Prefer: return=representation` header used consistently for POST/PATCH. ✓
