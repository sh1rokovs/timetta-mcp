# change_issue_status — Composite Tool Design

## Problem

Add an MCP tool to change the status of a Timetta issue, resolving a human-readable status code to the internal status ID via the `DirectoryEntries` catalog — same pattern as the existing `create_issue` / `link_issues` / `attach_file` composite tools.

## Tool signature

```
change_issue_status(issue_key: str, status_code: str) -> str
```

- **`issue_key`** — issue code like `"ISSUE-42"`. Falls back to numeric ID lookup if the code query returns no results (same as `_get_issue_by_key`).
- **`status_code`** — case-insensitive status code resolved against `DirectoryEntries` (e.g. `"IN_PROGRESS"`, `"DONE"`, `"REVIEW"`).

Returns compact JSON:
```json
{"changed": true, "id": "guid", "key": "ISSUE-42", "statusCode": "IN_PROGRESS"}
```

On failure returns `"Error: ..."`.

## Approach

**A — Dedicated composite tool** (chosen over extending `update_entity` or a thin wrapper).

### Resolution logic (`_change_issue_status`)

1. Get client (same lifecycle as all composite tools — try/except/finally + `aclose`)
2. **Resolve issue** — call existing `_get_issue_by_key(client, issue_key)` → returns the issue dict
3. **Resolve status code** — new `_resolve_status_code(client, status_code)`:
   - Query `DirectoryEntries` with no filter (reuse `client.query_all`)
   - Iterate for matching `code` (case-insensitive)
   - If not found, raise `TimettaError` listing available codes
4. **PATCH** — `client.update("Issues", issue_id, {"statusId": resolved_status_id})`
5. Return compact response dict

### Error cases

| Scenario | Response |
|---|---|
| Issue key not found | `"Error: Timetta issue not found by key: ..."` |
| Status code not found | `"Error: Timetta status code 'X' not found. Available: A, B, C"` |
| API network/HTTP error | Surfaces through `client.update` error handling |

### Test plan

| Test | What it verifies |
|---|---|
| `test_change_issue_status_resolves_status` | Resolves issue key + status code, PATCHes `statusId`, returns compact response, closes client |
| `test_change_issue_status_unknown_key` | Unknown key → error mentioning the key |
| `test_change_issue_status_unknown_status` | Unknown code → error with available codes |
| `test_change_issue_status_numeric_id_fallback` | Numeric string resolves via `get_by_id` |

Tests use the existing `CompositeFakeClient` with status-like entries added to its `DirectoryEntries` catalog.

### Changes required

| File | What |
|---|---|
| `src/timetta_mcp/server.py` | Add `_resolve_status_code`, `_change_issue_status`, `change_issue_status` tool |
| `tests/test_server.py` | 4 new test functions, extend `CompositeFakeClient._priorities` (or add a `_statuses` field) |

No changes to `client.py`, `auth.py`, `metadata.py`, or `pyproject.toml`.
