"""Thin synchronous wrapper around the Notion REST API.

Deliberately uses `requests` directly — no SDK, no MCP, no bridges.
"""
import logging
from typing import Any, Iterator

import requests

from .config import Settings

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
    ) -> Iterator[dict[str, Any]]:
        cursor: str | None = None
        while True:
            payload: dict[str, Any] = {"page_size": 100}
            if filter_ is not None:
                payload["filter"] = filter_
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
