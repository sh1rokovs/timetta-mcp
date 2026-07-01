# timetta-mcp

Python MCP server exposing the Timetta OData API as MCP tools.

## Commands

```bash
uv sync                 # install deps (uv, not pip)
uv run pytest           # run all tests
uv run pytest tests/test_client.py -k "test_name"  # single test
```

## Key structure

| Path | Role |
|---|---|
| `src/timetta_mcp/server.py` | FastMCP server, 6 generic + 3 composite tools, entrypoint `main()` |
| `src/timetta_mcp/client.py` | `TimettaClient` async HTTP wrapper (httpx), max `$top=200` |
| `src/timetta_mcp/auth.py` | OAuth token storage/refresh, `TokenProvider` singleton, `login` CLI |
| `src/timetta_mcp/metadata.py` | OData EDMX XML → entity/field schema |
| `timetta_adapter.py` | Legacy adapter (urllib, not httpx) — **not used by main code** |

## Testing quirks

- All tests use `pytest.mark.asyncio` (auto-enabled via `asyncio_mode=auto` in pyproject.toml).
- `TokenProvider` is a **process-level singleton** with `asyncio.Lock`. The `conftest.py` autouse fixture calls `server._reset_token_provider()` before and after every test — never skip this.
- Client tests mock HTTP with `respx` decorator on async functions. Server tests inject a `FakeClient` via `monkeypatch.setattr(server, "get_client", ...)`. Auth tests use `respx` + `monkeypatch` + `tmp_path`.
- No CI, no linting, no typechecking — just `pytest`.

## Auth flow (env var priority)

1. `TIMETTA_API_TOKEN` → static token (skip file lookup entirely)
2. `TIMETTA_CREDENTIALS_PATH` or default path (`%APPDATA%/timetta-mcp/credentials.json` / `~/.config/timetta-mcp/credentials.json`) → static or OAuth tokens

Env vars for composite tools: `TIMETTA_DEFAULT_PROJECT_ID`, `TIMETTA_DEFAULT_PRIORITY_CODE`, `TIMETTA_DEFAULT_ASSIGNEE_ID`, `TIMETTA_DEFAULT_PROJECT_TASK_ID`.

## CLI

- `timetta-mcp` — serve MCP over stdio
- `timetta-mcp login` — interactive: paste static token or email+password OAuth login
