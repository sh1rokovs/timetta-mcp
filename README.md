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
| `TIMETTA_CLIENT_ID` | for OAuth | `external` | Public OAuth client id (PKCE, loopback redirect) used by `timetta-mcp login`. |
| `TIMETTA_AUTH_URL` | no | `https://auth.timetta.com` | OAuth auth server. |
| `TIMETTA_CREDENTIALS_PATH` | no | platform default | Where OAuth tokens are stored. Default: `%APPDATA%\timetta-mcp\credentials.json` (Windows), `~/.config/timetta-mcp/credentials.json` (POSIX). |
| `TIMETTA_BASE_URL` | no | `https://api.timetta.com/odata` | OData base URL. |

The server can create, update and delete records. Effective permissions are
governed entirely by the token — use a read-only Timetta token if write access
is not needed.

## Authentication

Three modes:

1. **Static token (CI / automation).** Set `TIMETTA_API_TOKEN`. Used whenever present.
2. **OAuth browser login (stdio).** Leave `TIMETTA_API_TOKEN` unset, set `TIMETTA_CLIENT_ID`,
   then run a one-time login:

   ```bash
   timetta-mcp login
   ```

   This opens your browser to Timetta, you authorize, and the refresh token is
   saved to `TIMETTA_CREDENTIALS_PATH`. The server then refreshes the access
   token automatically (no further interaction). Re-run `timetta-mcp login` if
   the refresh token expires (Timetta refresh tokens last roughly 15 days of
   inactivity — see Timetta's API docs for the exact lifetime).
3. **In-client OAuth (Streamable HTTP).** Run the server over HTTP and let the
   MCP client drive the login from its own UI (e.g. the `Authenticate` entry in
   Claude Code's `/mcp` menu) — no `timetta-mcp login` and no separate terminal
   for the browser step. See [Streamable HTTP transport](#streamable-http-transport).

The first two modes are chosen automatically; the third is opt-in via the HTTP
transport.

## Streamable HTTP transport

By default the server speaks stdio (the client spawns it). Run it over
Streamable HTTP instead to expose a client-driven OAuth flow:

```bash
timetta-mcp serve-http                 # or set TIMETTA_MCP_TRANSPORT=http
```

In this mode the server fronts Timetta's IdP with an OAuth **proxy**
(FastMCP `OAuthProxy`): it serves the OAuth metadata the client expects
(`/.well-known/oauth-protected-resource` and authorization-server metadata),
shims dynamic client registration, and bridges the browser authorization-code +
PKCE flow into Timetta's `external` public client. The client then sends the
upstream Timetta access token as a bearer, which the server uses for OData
calls. Tokens are verified by use (a cheap probe request) because Timetta may
issue opaque access tokens. This is the right mode when the upstream IdP does
not support DCR and does not know the client's redirect URIs — which is exactly
Timetta's case.

HTTP-mode configuration (all optional):

| Variable | Default | Notes |
|---|---|---|
| `TIMETTA_MCP_TRANSPORT` | `stdio` | Set to `http` to serve Streamable HTTP. |
| `TIMETTA_MCP_HOST` | `127.0.0.1` | Bind host. |
| `TIMETTA_MCP_PORT` | `8080` | Bind port. |
| `TIMETTA_MCP_PATH` | `/mcp` | MCP endpoint path. |
| `TIMETTA_PUBLIC_URL` | `http://<host>:<port>` | Externally reachable base URL (set when behind a proxy/tunnel). |
| `TIMETTA_OAUTH_SCOPES` | `all offline_access` | Scopes requested upstream (`offline_access` yields the refresh token). |
| `TIMETTA_CLIENT_SECRET` | — | Only for a confidential upstream client; `external` is public (PKCE). |
| `TIMETTA_AUTH_PROBE_PATH` | `Users?$top=1` | OData path used to validate a presented bearer. |

Add it to Claude Code as an HTTP server and authenticate from `/mcp`:

```bash
claude mcp add --transport http timetta http://127.0.0.1:8080/mcp
# start the server in another terminal: timetta-mcp serve-http
# then run `/mcp` in Claude Code and pick Authenticate
```

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
