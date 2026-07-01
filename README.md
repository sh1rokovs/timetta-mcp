# timetta-mcp

MCP server exposing the [Timetta](https://timetta.com) main OData API to MCP
clients (Claude Desktop, Claude Code, Codex, Gemini CLI, etc.) as a universal
read-write gateway.

## Tools

- `list_entities()` — list queryable OData entities.
- `get_entity_schema(entity)` — fields, types and navigation properties of an
  entity. Call this first to learn real field names.
- `query_odata(entity, filter?, select?, expand?, orderby?, top?, skip?)` —
  query an entity using OData semantics (without the leading `$`). `top`
  defaults to 50, capped at 200.
- `create_entity(entity, data)` — create a record (POST). `data` is a JSON
  object of field -> value.
- `update_entity(entity, id, data)` — update a record by id (PATCH). `data`
  holds only the fields to change.
- `delete_entity(entity, id)` — delete a record by id (DELETE).

## Configuration

| Variable | Required | Default | Notes |
|---|---|---|---|
| `TIMETTA_API_TOKEN` | one of the two | — | Static Token API value (Bearer), TTL 1 year. Takes priority when set. |
| `TIMETTA_CLIENT_ID` | for OAuth | `external` | Public OAuth client id used by `timetta-mcp login` (password grant). |
| `TIMETTA_AUTH_URL` | no | `https://auth.timetta.com` | OAuth auth server. |
| `TIMETTA_CREDENTIALS_PATH` | no | platform default | Where OAuth tokens are stored. Default: `%APPDATA%\timetta-mcp\credentials.json` (Windows), `~/.config/timetta-mcp/credentials.json` (POSIX). |
| `TIMETTA_BASE_URL` | no | `https://api.timetta.com/odata` | OData base URL. |

The server can create, update and delete records. Effective permissions are
governed entirely by the token — use a read-only Timetta token if write access
is not needed.

## Authentication

The server picks credentials in this order: `TIMETTA_API_TOKEN` env var →
credentials file written by `timetta-mcp login` → otherwise an error asking you
to log in.

Run a one-time login and choose a method (like the Timetta VS Code extension):

```bash
timetta-mcp login
```

1. **Token API** (recommended; works with SSO accounts). Paste a long-lived
   token generated in Timetta settings (TTL ~1 year). It is saved to
   `TIMETTA_CREDENTIALS_PATH` and sent as a Bearer token. No refresh needed.
2. **Email + password** (OAuth password grant). Exchanges your Timetta
   email/password for tokens via `grant_type=password` (client `external`) and
   saves the refresh token. The password is never stored — only the resulting
   tokens. The server refreshes the access token automatically; re-run
   `timetta-mcp login` if the refresh token expires (Timetta refresh tokens last
   roughly 15 days of inactivity).

These are the only two methods Timetta documents for integrations; it offers no
self-service OAuth client registration or browser `authorization_code` flow.

For CI / automation you can skip `login` entirely and set `TIMETTA_API_TOKEN` —
it always takes priority.

> Tip: run `timetta-mcp` from this checkout with `uv run timetta-mcp …` (or
> `uvx --no-cache --from . timetta-mcp …`). Plain `uvx --from <path>` caches the
> built environment and may run stale code after you edit the source.

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

## Claude Code

CLI command (installs the server straight from GitHub via `uvx`):

```bash
claude mcp add timetta \
  --env TIMETTA_API_TOKEN=YOUR_TOKEN \
  -- uvx --from git+https://github.com/sh1rokovs/timetta-mcp timetta-mcp
```

Scope is selected with `-s`: `local` (default, this project only), `user`
(all projects), or `project` (writes a committed `.mcp.json`). To share the
server with everyone who clones the repo, add a `.mcp.json` at the project root:

```json
{
  "mcpServers": {
    "timetta": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/sh1rokovs/timetta-mcp", "timetta-mcp"],
      "env": { "TIMETTA_API_TOKEN": "${TIMETTA_API_TOKEN}" }
    }
  }
}
```

`${TIMETTA_API_TOKEN}` is read from the environment, so the token never lands
in the repo. Verify with `claude mcp list` / `claude mcp get timetta`.

## Codex CLI

Add the server to `~/.codex/config.toml`:

```toml
[mcp_servers.timetta]
command = "uvx"
args = ["--from", "git+https://github.com/sh1rokovs/timetta-mcp", "timetta-mcp"]
env = { TIMETTA_API_TOKEN = "YOUR_TOKEN" }
```

Or via the CLI:

```bash
codex mcp add timetta \
  --env TIMETTA_API_TOKEN=YOUR_TOKEN \
  -- uvx --from git+https://github.com/sh1rokovs/timetta-mcp timetta-mcp
```

## Gemini CLI

Add the server to `~/.gemini/settings.json` (user scope) or
`.gemini/settings.json` in the project root (committed, shared scope):

```json
{
  "mcpServers": {
    "timetta": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/sh1rokovs/timetta-mcp", "timetta-mcp"],
      "env": { "TIMETTA_API_TOKEN": "$TIMETTA_API_TOKEN" }
    }
  }
}
```

List configured servers with `/mcp` inside the Gemini CLI.

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
