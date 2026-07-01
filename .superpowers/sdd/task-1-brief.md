# Task 1: Implement change_issue_status

**Files:**
- Modify: `src/timetta_mcp/server.py` (after line 306, before `_resolve_project_task_id`)
- Modify: `src/timetta_mcp/server.py` (after `_attach_file`, before `@mcp.tool()` section)
- Modify: `src/timetta_mcp/server.py` (after `attach_file` tool)
- Test: `tests/test_server.py`
- Modify: `tests/test_server.py` (extend `CompositeFakeClient`)

**Interfaces:**
- Consumes: `_get_issue_by_key(client, issue_key) -> dict`, `client.query_all("DirectoryEntries", ...)`, `client.update("Issues", id, data)`
- Produces: `_resolve_status_code(client, status_code) -> dict`, `_change_issue_status(issue_key, status_code) -> str`, `change_issue_status(issue_key, status_code) -> str` tool

### Step 1: Extend CompositeFakeClient with status entries

In `tests/test_server.py`, add status entries to `CompositeFakeClient.__init__` so that `DirectoryEntries` returns both priorities and statuses:

```python
def __init__(self, *, issue_types=None, priorities=None, link_types=None, tasks=None):
    super().__init__()
    self._issue_types = issue_types if issue_types is not None else [
        {"projectId": "p1", "issueType": {"id": "t-task", "name": "Task", "code": "TASK"}},
        {"projectId": "p1", "issueType": {"id": "t-bug", "name": "Bug", "code": "BUG"}},
    ]
    self._priorities = priorities if priorities is not None else [
        {"id": "pr-normal", "name": "Normal", "code": "NORMAL", "isDefault": True},
        {"id": "st-inprogress", "name": "In Progress", "code": "IN_PROGRESS", "isDefault": False},
        {"id": "st-done", "name": "Done", "code": "DONE", "isDefault": False},
    ]
```

### Step 2: Write the failing tests

Add at the end of `tests/test_server.py`:

```python
async def test_change_issue_status_resolves_status(monkeypatch):
    fake = CompositeFakeClient()
    _patch(monkeypatch, fake)
    out = await server._change_issue_status("ISSUE-1", "IN_PROGRESS")
    data = json.loads(out)
    assert data["changed"] is True
    assert data["key"] == "ISSUE-1"
    assert data["statusCode"] == "IN_PROGRESS"
    assert fake.last_write == ("update", "Issues", "i-1", {"statusId": "st-inprogress"})
    assert fake.closed is True


async def test_change_issue_status_unknown_key_returns_error(monkeypatch):
    fake = CompositeFakeClient()
    orig = fake.query

    async def query(entity, **kwargs):
        if entity == "Issues":
            return []
        return await orig(entity, **kwargs)

    fake.query = query
    _patch(monkeypatch, fake)
    out = await server._change_issue_status("NOPE", "IN_PROGRESS")
    assert out.startswith("Error:")
    assert "NOPE" in out


async def test_change_issue_status_unknown_status_returns_error(monkeypatch):
    fake = CompositeFakeClient()
    _patch(monkeypatch, fake)
    out = await server._change_issue_status("ISSUE-1", "VOID")
    assert out.startswith("Error:")
    assert "VOID" in out
    assert "IN_PROGRESS" in out or "DONE" in out


async def test_change_issue_status_numeric_id_fallback(monkeypatch):
    empty = CompositeFakeClient()
    orig_query = empty.query

    async def query(entity, **kwargs):
        if entity == "Issues":
            return []
        return await orig_query(entity, **kwargs)

    empty.query = query
    _patch(monkeypatch, empty)
    out = await server._change_issue_status("999", "IN_PROGRESS")
    data = json.loads(out)
    assert data["changed"] is True
```

### Step 3: Run tests to verify they fail

Run: `uv run pytest tests/test_server.py -k "change_issue_status" -v`
Expected: FAIL with function not defined

### Step 4: Implement `_resolve_status_code`

Add in `src/timetta_mcp/server.py` after `_resolve_priority` (after line ~306):

```python
async def _resolve_status_code(client: TimettaClient, status_code: str) -> dict[str, str]:
    rows = await client.query_all(
        "DirectoryEntries", select="id,name,code"
    )
    for item in rows:
        if (item.get("code") or "").upper() == status_code.upper():
            return {"id": str(item["id"]), "code": item.get("code", ""), "name": item.get("name", "")}
    available = ", ".join(
        sorted(filter(None, {(i.get("code") or "").upper() for i in rows}))
    )
    raise TimettaError(
        f"Timetta status code '{status_code}' not found. Available: {available}"
    )
```

### Step 5: Implement `_change_issue_status`

Add in `src/timetta_mcp/server.py` after `_attach_file` (after line ~469, before the `@mcp.tool()` section):

```python
async def _change_issue_status(issue_key: str, status_code: str) -> str:
    try:
        client = get_client()
    except TimettaError as exc:
        return f"Error: {exc}"
    try:
        issue = await _get_issue_by_key(client, issue_key)
        issue_id = str(issue.get("id") or "")
        if not issue_id:
            raise TimettaError(f"Timetta issue has empty id: {issue_key}")
        resolved = await _resolve_status_code(client, status_code)
        await client.update("Issues", issue_id, {"statusId": resolved["id"]})
        return _dumps(
            {
                "changed": True,
                "id": issue_id,
                "key": issue.get("code", ""),
                "statusCode": resolved["code"],
                "statusName": resolved.get("name", ""),
            }
        )
    except Exception as exc:
        return f"Error: {exc}"
    finally:
        await client.aclose()
```

### Step 6: Add the `@mcp.tool()` decorator

Add at the end of the composite tools section, after `attach_file`:

```python
@mcp.tool()
async def change_issue_status(issue_key: str, status_code: str) -> str:
    """Change the status of a Timetta issue in one call.

    Resolves the issue by its key (or numeric id) and the status code against
    the DirectoryEntries catalog. Returns compact JSON {changed, id, key,
    statusCode, statusName}, or 'Error: ...' on failure."""
    return await _change_issue_status(issue_key, status_code)
```

### Step 7: Run all tests

Run: `uv run pytest -v`
Expected: all tests pass

### Step 8: Commit

```bash
git add -A
git commit -m "feat: add change_issue_status composite tool"
```
