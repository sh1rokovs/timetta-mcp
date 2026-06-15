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


def main() -> None:
    """Console entry point — runs the server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
