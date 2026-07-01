"""FastMCP server exposing Timetta OData as MCP tools."""

from __future__ import annotations

import json
import os

from mcp.server.fastmcp import FastMCP

from . import metadata
from .auth import (
    StaticCredentials,
    TokenProvider,
    TokenStore,
    credentials_path,
    get_client_id,
)
from .client import DEFAULT_BASE_URL, TimettaClient, TimettaError

mcp = FastMCP("timetta")


_token_provider: TokenProvider | None = None


def _reset_token_provider() -> None:
    """Drop the cached provider (used by tests after changing env)."""
    global _token_provider
    _token_provider = None


def _get_token_provider() -> TokenProvider:
    global _token_provider
    # Built once at first use; env vars are fixed at process start for a running server.
    if _token_provider is None:
        _token_provider = TokenProvider(TokenStore(credentials_path()), get_client_id())
    return _token_provider


def get_client() -> TimettaClient:
    base_url = os.environ.get("TIMETTA_BASE_URL", DEFAULT_BASE_URL)
    static = os.environ.get("TIMETTA_API_TOKEN")
    if static:
        return TimettaClient(token=static, base_url=base_url)
    # File-based credentials saved by `timetta-mcp login`: static Token API or OAuth.
    try:
        creds = TokenStore(credentials_path()).load_any()
    except ValueError as exc:
        raise TimettaError(
            "Timetta credentials file is corrupted — run `timetta-mcp login`"
        ) from exc
    if isinstance(creds, StaticCredentials):
        return TimettaClient(token=creds.api_token, base_url=base_url)
    return TimettaClient(token_provider=_get_token_provider(), base_url=base_url)


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


# --------------------------------------------------------------------------- #
# Composite tools                                                             #
#                                                                             #
# These wrap several OData round-trips (catalog resolution + write) into one  #
# tool call, keeping reference-data lookups out of the model's context. The   #
# resolution logic mirrors the project's TimettaAdapter.                      #
# --------------------------------------------------------------------------- #


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default) or default


def _issue_type_candidates(issue_type: str) -> tuple[str, ...]:
    """Map a coarse type hint to Timetta issue-type codes, best match first."""
    normalized = (issue_type or "task").lower()
    if normalized == "epic":
        return ("EPIC",)
    if normalized == "bug":
        return ("BUG",)
    if normalized == "hotfix":
        return ("BUG", "TASK", "ASSIGNMENT", "DEVELOPMENT")
    if normalized == "research":
        return ("TASK", "ASSIGNMENT", "DOCUMENT", "DEVELOPMENT")
    if normalized == "tech_debt":
        return ("TASK", "DEVELOPMENT", "ASSIGNMENT")
    return ("TASK", "ASSIGNMENT", "DEVELOPMENT")


async def _resolve_issue_type(
    client: TimettaClient, project_id: str, type_hint: str
) -> dict[str, str]:
    preferred = _issue_type_candidates(type_hint)
    rows = await client.query_all(
        "ProjectIssueTypes",
        select="id,projectId,issueTypeId",
        expand="issueType($select=id,name,code,isActive)",
    )
    active = []
    for row in rows:
        it = row.get("issueType") or {}
        if str(row.get("projectId") or "") == project_id and it.get("id"):
            active.append(
                {"id": str(it["id"]), "name": it.get("name", ""), "code": it.get("code", "")}
            )
    if not active:
        raise TimettaError(f"Timetta project {project_id} has no available issue types")
    for code in preferred:
        for item in active:
            if (item.get("code") or "").upper() == code:
                return item
    available = ", ".join(
        sorted(filter(None, {(i.get("code") or i.get("name") or "").upper() for i in active}))
    )
    raise TimettaError(
        f"Timetta project {project_id} does not support type candidates "
        f"[{', '.join(preferred)}]. Available: {available}"
    )


async def _resolve_priority(client: TimettaClient, preferred_code: str) -> dict[str, str]:
    rows = await client.query_all(
        "DirectoryEntries", filter="isActive eq true", select="id,name,code,isDefault"
    )
    preferred = (preferred_code.upper(), "NORMAL", "MEDIUM", "DEFAULT")
    for code in preferred:
        if not code:
            continue
        for item in rows:
            if (item.get("code") or "").upper() == code:
                return {"id": str(item["id"]), "name": item.get("name", ""), "code": item.get("code", "")}
    for item in rows:
        if item.get("isDefault") and item.get("id"):
            return {"id": str(item["id"]), "name": item.get("name", ""), "code": item.get("code", "")}
    for item in rows:
        if item.get("id"):
            return {"id": str(item["id"]), "name": item.get("name", ""), "code": item.get("code", "")}
    raise TimettaError("Timetta priority catalog is empty")


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


async def _resolve_project_task_id(client: TimettaClient, project_id: str, hint: str) -> str:
    if not hint:
        return ""
    match_hints = [h.strip().lower() for h in hint.split("|") if h.strip()]
    if not match_hints:
        return ""
    rows = await client.query_all("ProjectTasks", select="id,name,fullPath,projectId")
    for item in rows:
        if str(item.get("projectId") or "") != project_id:
            continue
        haystack = f"{item.get('name', '')} {item.get('fullPath', '')}".lower()
        if all(h in haystack for h in match_hints):
            return str(item.get("id", ""))
    return ""


async def _resolve_issue_link_type(client: TimettaClient, preferred_name: str) -> dict:
    rows = await client.query_all("IssueLinkTypes")
    for item in rows:
        if (item.get("name") or "") == preferred_name and item.get("id"):
            return item
    for item in rows:
        if (item.get("name") or "") == "Связь" and item.get("id"):
            return item
    if rows:
        return rows[0]
    raise TimettaError("Timetta issue link types catalog is empty")


async def _get_issue_by_key(client: TimettaClient, issue_key_or_id: str) -> dict:
    if not issue_key_or_id:
        raise TimettaError("Timetta issue key is required")
    safe_key = issue_key_or_id.replace("'", "''")
    rows = await client.query("Issues", filter=f"code eq '{safe_key}'", top=2)
    if rows:
        return rows[0]
    if issue_key_or_id.isdigit():
        return await client.get_by_id("Issues", issue_key_or_id)
    raise TimettaError(f"Timetta issue not found by key: {issue_key_or_id}")


async def _create_issue(
    title: str,
    description: str,
    type_hint: str,
    project_id: str | None,
    priority_code: str | None,
    project_task_id: str | None,
    project_task_hint: str | None,
    parent_id: str | None,
    assignee_id: str | None,
) -> str:
    try:
        client = get_client()
    except TimettaError as exc:
        return f"Error: {exc}"
    try:
        resolved_project = project_id or _env("TIMETTA_DEFAULT_PROJECT_ID")
        if not resolved_project:
            raise TimettaError("project_id or TIMETTA_DEFAULT_PROJECT_ID is required")

        issue_type = await _resolve_issue_type(client, resolved_project, type_hint)
        priority = await _resolve_priority(
            client, priority_code or _env("TIMETTA_DEFAULT_PRIORITY_CODE")
        )
        body: dict = {
            "name": title,
            "description": description,
            "projectId": resolved_project,
            "typeId": issue_type["id"],
            "priorityId": priority["id"],
        }

        resolved_assignee = assignee_id or _env("TIMETTA_DEFAULT_ASSIGNEE_ID")
        if resolved_assignee:
            body["assignedId"] = resolved_assignee

        resolved_task = (
            project_task_id
            or _env("TIMETTA_DEFAULT_PROJECT_TASK_ID")
            or await _resolve_project_task_id(client, resolved_project, project_task_hint or "")
        )
        if resolved_task:
            body["projectTaskId"] = resolved_task

        resolved_parent = parent_id or ""
        if resolved_parent:
            body["parentId"] = resolved_parent

        created = await client.create("Issues", body)
        issue_id = created.get("id")
        return _dumps(
            {
                "created": True,
                "id": issue_id,
                "key": created.get("code"),
                "url": f"{client.base_url}/Issues({issue_id})" if issue_id else "",
                "typeCode": issue_type.get("code", ""),
                "priorityCode": priority.get("code", ""),
                "projectTaskId": resolved_task,
            }
        )
    except Exception as exc:
        return f"Error: {exc}"
    finally:
        await client.aclose()


async def _link_issues(source_id: str, destination_id: str, link_type_name: str) -> str:
    try:
        client = get_client()
    except TimettaError as exc:
        return f"Error: {exc}"
    try:
        link_type = await _resolve_issue_link_type(client, link_type_name)
        created = await client.create(
            "IssueLinks",
            {
                "sourceId": source_id,
                "destinationId": destination_id,
                "linkTypeId": link_type["id"],
            },
        )
        return _dumps(
            {
                "linked": True,
                "id": created.get("id"),
                "sourceId": source_id,
                "destinationId": destination_id,
                "linkTypeId": link_type["id"],
                "linkTypeName": link_type.get("name", ""),
            }
        )
    except Exception as exc:
        return f"Error: {exc}"
    finally:
        await client.aclose()


async def _attach_file(issue_key_or_id: str, file_path: str, filename: str | None) -> str:
    try:
        client = get_client()
    except TimettaError as exc:
        return f"Error: {exc}"
    try:
        issue = await _get_issue_by_key(client, issue_key_or_id)
        issue_id = str(issue.get("id") or "")
        if not issue_id:
            raise TimettaError(f"Timetta issue has empty id: {issue_key_or_id}")
        meta = await client.upload_file(
            file_path,
            entity_type="Issue",
            entity_id=issue_id,
            file_field="attachment",
            filename=filename,
        )
        return _dumps({"attached": True, "issueId": issue_id, "file": meta})
    except Exception as exc:
        return f"Error: {exc}"
    finally:
        await client.aclose()


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


@mcp.tool()
async def create_issue(
    title: str,
    description: str = "",
    type_hint: str = "task",
    project_id: str | None = None,
    priority_code: str | None = None,
    project_task_id: str | None = None,
    project_task_hint: str | None = None,
    parent_id: str | None = None,
    assignee_id: str | None = None,
) -> str:
    """Create a Timetta issue in one call, resolving reference data internally.

    Resolves the issue type from a coarse `type_hint` (one of: task, bug, epic,
    hotfix, research, tech_debt), the priority (by `priority_code`, then
    TIMETTA_DEFAULT_PRIORITY_CODE, then the catalog default), and optionally the
    project task (explicit `project_task_id`, env default, or `project_task_hint`
    matched by '|'-separated substrings against task name/path). `project_id`
    falls back to TIMETTA_DEFAULT_PROJECT_ID and `assignee_id` to
    TIMETTA_DEFAULT_ASSIGNEE_ID. No get_entity_schema/query_odata calls needed.

    Returns compact JSON {created, id, key, url, typeCode, priorityCode,
    projectTaskId}, or 'Error: ...' on failure."""
    return await _create_issue(
        title,
        description,
        type_hint,
        project_id,
        priority_code,
        project_task_id,
        project_task_hint,
        parent_id,
        assignee_id,
    )


@mcp.tool()
async def link_issues(
    source_id: str, destination_id: str, link_type_name: str = "Реализация"
) -> str:
    """Link two Timetta issues, resolving the link type by name in one call.

    `link_type_name` is matched against the IssueLinkTypes catalog (falls back to
    'Связь', then the first available type). Returns compact JSON {linked, id,
    sourceId, destinationId, linkTypeId, linkTypeName}, or 'Error: ...'."""
    return await _link_issues(source_id, destination_id, link_type_name)


@mcp.tool()
async def attach_file(
    issue_key_or_id: str, file_path: str, filename: str | None = None
) -> str:
    """Attach a local file to a Timetta issue in one call.

    Resolves the issue by code (or numeric id), then uploads `file_path` as
    multipart/form-data. `filename` overrides the stored name. Returns compact
    JSON {attached, issueId, file}, or 'Error: ...' on failure."""
    return await _attach_file(issue_key_or_id, file_path, filename)


@mcp.tool()
async def change_issue_status(issue_key: str, status_code: str) -> str:
    """Change the status of a Timetta issue in one call.

    Resolves the issue by its key (or numeric id) and the status code against
    the DirectoryEntries catalog. Returns compact JSON {changed, id, key,
    statusCode, statusName}, or 'Error: ...' on failure."""
    return await _change_issue_status(issue_key, status_code)


def main() -> None:
    """Console entry point — `timetta-mcp` serves over stdio; `login` runs OAuth."""
    import sys

    argv = sys.argv[1:]
    if argv and argv[0] == "login":
        from .auth import login_command

        login_command()
        return
    mcp.run()


if __name__ == "__main__":
    main()
