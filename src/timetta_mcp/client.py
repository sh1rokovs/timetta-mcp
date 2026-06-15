"""HTTP client for the Timetta main OData API (Bearer Token API auth)."""

from __future__ import annotations

from typing import Protocol

import httpx

DEFAULT_BASE_URL = "https://api.timetta.com/odata"
MAX_TOP = 200


class TimettaError(Exception):
    """Timetta API error with a message safe to show the model (no token)."""


class TokenProvider(Protocol):
    """Interface a token source must implement for TimettaClient."""

    async def get_token(self) -> str: ...
    def can_refresh(self) -> bool: ...
    async def force_refresh(self) -> str: ...


class _StaticToken:
    def __init__(self, token: str) -> None:
        self._token = token

    async def get_token(self) -> str:
        return self._token

    def can_refresh(self) -> bool:
        return False

    async def force_refresh(self) -> str:  # conforms to protocol; never called (can_refresh is False)
        raise NotImplementedError


class TimettaClient:
    def __init__(
        self,
        token: str | None = None,
        *,
        token_provider: TokenProvider | None = None,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        if token_provider is None:
            if token is None:
                raise TimettaError("TimettaClient needs a token or token_provider")
            token_provider = _StaticToken(token)
        self._provider: TokenProvider = token_provider
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=30.0)

    def __repr__(self) -> str:  # never leak the token
        return f"TimettaClient(base_url={self._base!r})"

    async def query(
        self,
        entity: str,
        *,
        filter: str | None = None,
        select: str | None = None,
        expand: str | None = None,
        orderby: str | None = None,
        top: int = 50,
        skip: int | None = None,
    ) -> list[dict]:
        params: dict[str, str | int] = {}
        if filter:
            params["$filter"] = filter
        if select:
            params["$select"] = select
        if expand:
            params["$expand"] = expand
        if orderby:
            params["$orderby"] = orderby
        params["$top"] = max(0, min(int(top), MAX_TOP))
        if skip is not None:
            params["$skip"] = int(skip)

        resp = await self._send("GET", f"{self._base}/{entity}", params=params, what=entity)
        return resp.json().get("value", [])

    async def create(self, entity: str, data: dict) -> dict:
        resp = await self._send(
            "POST",
            f"{self._base}/{entity}",
            json=data,
            headers={"Prefer": "return=representation"},
            what=entity,
        )
        return resp.json()

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

    async def delete(self, entity: str, id: str) -> None:
        await self._send(
            "DELETE",
            f"{self._base}/{entity}({id})",
            what=entity,
        )

    async def fetch_metadata_xml(self) -> str:
        resp = await self._send("GET", f"{self._base}/$metadata", what="$metadata")
        return resp.text

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
        resp = await self._request(method, url, params=params, json=json, headers=headers)
        if resp.status_code == 401 and self._provider.can_refresh():
            await self._provider.force_refresh()
            resp = await self._request(
                method, url, params=params, json=json, headers=headers
            )
        self._raise_for_status(resp, what)
        return resp

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str | int] | None = None,
        json: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        token = await self._provider.get_token()
        merged = {"Authorization": f"Bearer {token}", **(headers or {})}
        try:
            return await self._client.request(
                method, url, params=params, json=json, headers=merged
            )
        except httpx.RequestError as exc:
            raise TimettaError(f"Network error talking to Timetta: {exc}") from exc

    @staticmethod
    def _raise_for_status(resp: httpx.Response, what: str) -> None:
        code = resp.status_code
        if code < 400:
            return
        if code == 401:
            raise TimettaError(
                "Unauthorized — check TIMETTA_API_TOKEN (invalid or expired)"
            )
        if code in (403, 404):
            raise TimettaError(f"No access or not found: {what}")
        if code in (400, 500):
            try:
                message = resp.json().get("message") or resp.text
            except Exception:
                message = resp.text
            raise TimettaError(f"Timetta business error: {message}")
        raise TimettaError(f"Timetta returned HTTP {code}")

    async def aclose(self) -> None:
        await self._client.aclose()
