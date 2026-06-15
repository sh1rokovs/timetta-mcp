# timetta-mcp read-write — design

Date: 2026-06-15

## Goal

Extend timetta-mcp from a read-only OData gateway to a read-write one, so MCP
clients can create, update and delete Timetta entities in addition to querying
them. Keep the universal-gateway philosophy: generic CRUD tools that work for
any of the ~200 OData entities, not entity-specific helpers.

## Scope

In scope:

- Create (POST), Update (PATCH), Delete (DELETE) for arbitrary OData entities.
- Generic interface symmetric with the existing `query_odata` tool.
- Error-body parsing for validation failures (HTTP 400).

Out of scope (YAGNI):

- Env-flag gating of writes (e.g. `TIMETTA_ENABLE_WRITES`).
- Entity allowlist.
- OData actions/functions (submit, approve, etc.).
- Entity-specific tools (`create_issue`, `log_time`, …).
- Batch / `$batch` operations.

Safety relies on the token's own permissions and the MCP client's per-tool-call
confirmation prompt. If write access is not desired, use a read-only Timetta
token.

## Components

### 1. Client (`src/timetta_mcp/client.py`)

Generalize the existing HTTP plumbing and add three write methods.

- Refactor `_get(url, params, what)` into `_send(method, url, *, params=None,
  json=None, what)`. `query()` and `fetch_metadata_xml()` call `_send("GET", …)`.
- `create(entity, data: dict) -> dict`
  - `POST {base}/{entity}` with JSON body `data`.
  - Header `Prefer: return=representation` to get the created entity back.
  - Returns the response JSON (the created entity).
- `update(entity, id: str, data: dict) -> dict`
  - `PATCH {base}/{entity}({id})` with JSON body `data`.
  - Header `Prefer: return=representation`.
  - Returns the response JSON when the server replies 200 with a body, or
    `{"id": id, "updated": True}` when the server replies 204 No Content.
- `delete(entity, id: str) -> None`
  - `DELETE {base}/{entity}({id})`. Expects 204; no body returned.

OData v4 key syntax in the URL is `Entity(<guid>)` with the GUID unquoted,
consistent with the filters already used in this project (`id eq <guid>`).

POST/PATCH set `Content-Type: application/json` automatically via httpx `json=`.

### 2. Error mapping (`_raise_for_status`)

Currently only HTTP 500 parses the OData error body for a `message`. Writes make
HTTP 400 (validation) common, so extend body parsing to **both 400 and 500** and
surface the OData `message` to the model. 401/403/404 handling is unchanged.

The token is never included in any error string (existing invariant preserved).

### 3. Server (`src/timetta_mcp/server.py`)

Three new MCP tools mirroring the existing wrapper pattern (`get_client()` →
`try/except TimettaError` → call client → `_dumps(...)` on success or
`f"Error: {exc}"` on failure → `finally: await client.aclose()`):

- `create_entity(entity: str, data: dict) -> str` — returns the created entity
  as JSON.
- `update_entity(entity: str, id: str, data: dict) -> str` — returns the updated
  entity (or update confirmation) as JSON.
- `delete_entity(entity: str, id: str) -> str` — returns `{"deleted": "<id>"}`
  as JSON.

Docstrings instruct the model to call `get_entity_schema(entity)` first to learn
real field names before writing.

### 4. Tests (`tests/`, respx)

- `test_client.py`: add cases for
  - `create` success (HTTP 201 with entity body),
  - `update` success (HTTP 200 with body; and HTTP 204 → confirmation dict),
  - `delete` success (HTTP 204),
  - error mapping for HTTP 400 with an OData error body (message surfaced).
- `test_server.py`: add cases for each of the three tools — success path and the
  `"Error: …"` branch (e.g. missing token or client error).

### 5. Documentation (`README.md`)

- Replace read-only framing with read-write.
- Add the three new tools to the Tools list.
- Note that effective permissions are governed by the token; recommend a
  read-only token when write access is not needed.

## Data flow

Unchanged from the read path: each tool call constructs a short-lived
`TimettaClient`, issues a single HTTP request, and closes the client in a
`finally` block. No shared state between calls.

## Error handling

- Network errors → `TimettaError("Network error talking to Timetta: …")`.
- 400/500 → `TimettaError("Timetta business error: <message>")` (body parsed).
- 401 → unauthorized/token hint. 403/404 → no access or not found.
- All `TimettaError`s are caught in the tool wrapper and returned to the model as
  `"Error: …"` strings, never raised through the MCP boundary.

## Testing strategy

respx-mocked httpx requests, asserting method, URL (including the
`Entity(<guid>)` key form), request body, and the mapped result/error for each
new client method and server tool. Mirrors the existing test structure.
