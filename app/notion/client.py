"""Thin synchronous wrapper around the Notion REST API.

Direct `requests` — no SDK, no MCP, no bridges.
"""
import logging
from typing import Any, Iterator

import requests

from ..config import Settings

logger = logging.getLogger(__name__)

NOTION_BASE = "https://api.notion.com/v1"
DEFAULT_TIMEOUT = 30


class NotionAPIError(Exception):
    def __init__(
        self,
        status: int,
        message: str,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.request_id = request_id


class NotionClient:
    """Minimal Notion REST client. Auto-paginates list endpoints."""

    def __init__(
        self,
        settings: Settings,
        session: requests.Session | None = None,
    ) -> None:
        if not settings.notion_token:
            raise RuntimeError("NOTION_API_TOKEN is not configured")
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {settings.notion_token}",
                "Notion-Version": settings.notion_api_version,
                "Content-Type": "application/json",
            }
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{NOTION_BASE}{path}"
        resp = self.session.request(method, url, timeout=DEFAULT_TIMEOUT, **kwargs)
        request_id = (
            resp.headers.get("x-request-id")
            or resp.headers.get("Notion-Request-Id")
        )
        if resp.status_code >= 400:
            try:
                body = resp.json()
                message = body.get("message") or resp.text
            except ValueError:
                message = resp.text
            logger.error(
                "Notion %s %s -> %s req=%s: %s",
                method,
                path,
                resp.status_code,
                request_id,
                message,
            )
            raise NotionAPIError(resp.status_code, message, request_id=request_id)
        return resp.json()

    def query_database_all(
        self,
        database_id: str,
        filter_: dict[str, Any] | None = None,
        sorts: list[dict[str, Any]] | None = None,
    ) -> Iterator[dict[str, Any]]:
        cursor: str | None = None
        while True:
            payload: dict[str, Any] = {"page_size": 100}
            if filter_ is not None:
                payload["filter"] = filter_
            if sorts is not None:
                payload["sorts"] = sorts
            if cursor:
                payload["start_cursor"] = cursor
            data = self._request(
                "POST", f"/databases/{database_id}/query", json=payload
            )
            yield from data.get("results", [])
            if not data.get("has_more"):
                return
            cursor = data.get("next_cursor")

    def get_block_children_all(self, block_id: str) -> Iterator[dict[str, Any]]:
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            data = self._request(
                "GET", f"/blocks/{block_id}/children", params=params
            )
            yield from data.get("results", [])
            if not data.get("has_more"):
                return
            cursor = data.get("next_cursor")

    def list_block_children(
        self,
        block_id: str,
        start_cursor: str | None = None,
        page_size: int = 100,
    ) -> dict[str, Any]:
        """A single page of a block's children (raw Notion response).

        Use ``get_block_children_all`` when you want every child auto-paginated;
        use this when the caller wants explicit cursor control.
        """
        params: dict[str, Any] = {"page_size": page_size}
        if start_cursor:
            params["start_cursor"] = start_cursor
        return self._request(
            "GET", f"/blocks/{block_id}/children", params=params
        )

    def append_block_children(
        self,
        block_id: str,
        children: list[dict[str, Any]],
        after: str | None = None,
    ) -> dict[str, Any]:
        """Append child blocks. Pages are blocks, so block_id may be a page id."""
        payload: dict[str, Any] = {"children": children}
        if after:
            payload["after"] = after
        return self._request(
            "PATCH", f"/blocks/{block_id}/children", json=payload
        )

    def retrieve_block(self, block_id: str) -> dict[str, Any]:
        return self._request("GET", f"/blocks/{block_id}")

    def update_block(
        self, block_id: str, block_payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Update one block. ``block_payload`` is the type-keyed content dict,
        e.g. ``{"paragraph": {"rich_text": [...]}}``."""
        return self._request("PATCH", f"/blocks/{block_id}", json=block_payload)

    def retrieve_database(self, database_id: str) -> dict[str, Any]:
        return self._request("GET", f"/databases/{database_id}")

    def get_property_schema(
        self, database_id: str, property_name: str
    ) -> dict[str, Any] | None:
        """Return ``{"type": ..., "options": [...]}`` for a DB property, or None.

        For ``select`` / ``multi_select`` / ``status`` properties, ``options``
        is a list of the available option names. For other property types,
        ``options`` is omitted (the key isn't present).

        Notion validates filter values against the list of available options
        for select-typed properties — sending an unknown option name causes
        a 400. Workflows that build filters from caller-supplied values
        should intersect those values with this list before constructing
        the filter.
        """
        db = self.retrieve_database(database_id)
        prop = db.get("properties", {}).get(property_name)
        if not isinstance(prop, dict):
            return None
        ptype = prop.get("type")
        schema: dict[str, Any] = {"type": ptype}
        if ptype in ("select", "multi_select", "status"):
            inner = prop.get(ptype, {})
            options = inner.get("options", []) if isinstance(inner, dict) else []
            schema["options"] = [
                o.get("name") for o in options if isinstance(o, dict) and o.get("name")
            ]
        return schema

    def get_property_type(
        self, database_id: str, property_name: str
    ) -> str | None:
        """Convenience: return only the property type. See ``get_property_schema``."""
        schema = self.get_property_schema(database_id, property_name)
        return schema["type"] if schema else None

    def retrieve_page(self, page_id: str) -> dict[str, Any]:
        return self._request("GET", f"/pages/{page_id}")

    def create_page(
        self,
        parent: dict[str, Any],
        properties: dict[str, Any],
        children: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"parent": parent, "properties": properties}
        if children:
            payload["children"] = children
        return self._request("POST", "/pages", json=payload)

    def update_page(
        self,
        page_id: str,
        properties: dict[str, Any] | None = None,
        archived: bool | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if properties is not None:
            payload["properties"] = properties
        if archived is not None:
            payload["archived"] = archived
        return self._request("PATCH", f"/pages/{page_id}", json=payload)
