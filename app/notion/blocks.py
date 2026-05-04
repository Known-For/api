"""Block authoring helpers + page-body assembly.

Two flavors of helper here:
  * Authoring (``paragraph``, ``heading_2``, ``bullet`` …) — return Notion-API
    block dicts ready to pass as ``children`` on ``create_page``.
  * Reading (``block_to_text``, ``assemble_body_text``) — pull plain text out
    of an existing block tree, paginated.
"""
from typing import Any

from .client import NotionAPIError, NotionClient


# ---------------- authoring ----------------


def text_run(content: str) -> dict[str, Any]:
    """A single Notion rich_text element of type 'text'."""
    return {"type": "text", "text": {"content": content}}


def paragraph(text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [text_run(text)]},
    }


def heading_2(text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [text_run(text)]},
    }


def heading_3(text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "heading_3",
        "heading_3": {"rich_text": [text_run(text)]},
    }


def bullet(text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [text_run(text)]},
    }


def divider() -> dict[str, Any]:
    return {"object": "block", "type": "divider", "divider": {}}


# ---------------- reading ----------------


def block_to_text(block: dict[str, Any]) -> str:
    """Plain-text extraction from a single Notion block of any common type."""
    btype = block.get("type")
    if not btype:
        return ""
    inner = block.get(btype, {})
    if not isinstance(inner, dict):
        return ""
    rich = inner.get("rich_text") or inner.get("text") or []
    return "".join(part.get("plain_text", "") for part in rich)


def assemble_body_text(client: NotionClient, page_id: str) -> str:
    """Concatenate plain text across all child blocks of a page.

    Returns "" on Notion API error rather than raising — body assembly is a
    best-effort enrichment, not a pipeline-blocking step. Callers that need
    strict semantics should call ``client.get_block_children_all`` directly.
    """
    parts: list[str] = []
    try:
        for block in client.get_block_children_all(page_id):
            text = block_to_text(block)
            if text:
                parts.append(text)
    except NotionAPIError:
        return ""
    return "\n".join(parts)
