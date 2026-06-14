# timetta-mcp

MCP server exposing the [Timetta](https://timetta.com) main OData API to MCP
clients (Claude Desktop, Claude Code, Codex, Gemini CLI, etc.) as a universal
query gateway.

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
