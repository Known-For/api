"""Surgical page-edit workflow: the server side of update_content.

Given a list of operations against a Notion page — ``{old_str, new_str}``
replacements and ``{append: {...}}`` block appends — this:

  1. Snapshots the page's block tree (depth 2 covers our page shapes).
  2. Validates every replace op resolves to exactly one block BEFORE any
     write happens. 0 matches -> no_match; >1 -> ambiguous_match. Either
     fails the whole request 422, nothing written.
  3. On dry_run, returns the resolved plan without writing.
  4. Otherwise executes: PATCH each replace, APPEND each append op. If a
     write fails partway, returns what was applied so far + the failure —
     no rollback (documented behavior).
  5. Re-fetches the replaced blocks to confirm the new text landed.

v1 limitation: a replace swaps the matched block's entire rich_text array
for a single plain run, so inline bold/italic/link annotations inside that
block are lost. Acceptable for Voice Brief / Annotated Exemplars pages,
which are near-plain paragraphs. Documented in API.md.
"""
import logging
from typing import Any

from ..config import Settings
from ..notion import NotionAPIError, NotionClient
from ..notion import blocks as nb

logger = logging.getLogger(__name__)

# Block types that carry an editable rich_text array. A block that matched
# an old_str necessarily has text, hence rich_text, hence one of these.
_RICH_TEXT_BLOCK_TYPES = {
    "paragraph",
    "heading_1",
    "heading_2",
    "heading_3",
    "quote",
    "callout",
    "bulleted_list_item",
    "numbered_list_item",
    "to_do",
    "toggle",
}

# How deep to walk the page tree when resolving old_str matches.
_MAX_DEPTH = 2


class NotionEditError(Exception):
    """Workflow-level error carrying an HTTP status hint + structured detail."""

    def __init__(self, status: int, detail: Any) -> None:
        super().__init__(detail if isinstance(detail, str) else str(detail))
        self.status = status
        self.detail = detail


def run_update_content(
    settings: Settings,
    page_id: str,
    operations: list[dict[str, Any]],
    dry_run: bool,
) -> dict[str, Any]:
    if not settings.notion_token:
        raise NotionEditError(503, "NOTION_API_TOKEN is not configured")
    if not operations:
        raise NotionEditError(422, "operations must be a non-empty list")

    client = NotionClient(settings)
    tree = nb.iter_block_tree(client, page_id, max_depth=_MAX_DEPTH)

    # ---- Validation pass: resolve every op before writing anything ----
    plan: list[dict[str, Any]] = []
    for i, op in enumerate(operations):
        if "old_str" in op:
            plan.append(_resolve_replace(i, op, tree))
        elif "append" in op:
            plan.append(_resolve_append(i, op))
        else:
            raise NotionEditError(
                422,
                {
                    "op_index": i,
                    "error": "malformed_op",
                    "message": "op must contain either 'old_str' or 'append'",
                },
            )

    # ---- Dry run: report the resolved plan, write nothing ----
    if dry_run:
        return {
            "page_id": page_id,
            "applied": [_plan_entry_preview(p) for p in plan],
            "verified": False,
            "dry_run": True,
        }

    # ---- Execution pass ----
    applied: list[dict[str, Any]] = []
    for p in plan:
        try:
            if p["kind"] == "replace":
                block = p["block"]
                payload = {
                    block["type"]: {"rich_text": [nb.text_run(p["after_text"])]}
                }
                client.update_block(block["id"], payload)
                applied.append(
                    {
                        "op_index": p["op_index"],
                        "kind": "replace",
                        "block_id": block["id"],
                        "before_text": p["before_text"],
                        "after_text": p["after_text"],
                    }
                )
            else:  # append
                result = client.append_block_children(
                    page_id, p["children"], after=p["after"]
                )
                appended_ids = [
                    b.get("id") for b in result.get("results", [])
                ]
                applied.append(
                    {
                        "op_index": p["op_index"],
                        "kind": "append",
                        "appended_block_ids": appended_ids,
                    }
                )
        except NotionAPIError as exc:
            # Partial success: surface what landed + the failure. No rollback.
            logger.error(
                "update_content write failed at op %s: %s",
                p["op_index"],
                exc.message,
            )
            raise NotionEditError(
                502,
                {
                    "error": "Notion write failed mid-execution",
                    "failed_op_index": p["op_index"],
                    "notion_status": exc.status,
                    "notion_request_id": exc.request_id,
                    "message": exc.message,
                    "applied": applied,
                },
            )

    verified = _verify(client, applied)
    return {
        "page_id": page_id,
        "applied": applied,
        "verified": verified,
        "dry_run": False,
    }


def _resolve_replace(
    i: int, op: dict[str, Any], tree: list[dict[str, Any]]
) -> dict[str, Any]:
    old = op["old_str"]
    new = op.get("new_str", "")
    if not old:
        raise NotionEditError(
            422,
            {"op_index": i, "error": "malformed_op", "message": "old_str is empty"},
        )
    matches = [b for b in tree if old in b["text"]]
    if len(matches) == 0:
        raise NotionEditError(
            422,
            {
                "op_index": i,
                "error": "no_match",
                "message": f"No block on the page contains the old_str for op {i}",
            },
        )
    if len(matches) > 1:
        raise NotionEditError(
            422,
            {
                "op_index": i,
                "error": "ambiguous_match",
                "message": (
                    f"{len(matches)} blocks contain the old_str for op {i}; "
                    "exactly 1 required"
                ),
                "block_ids": [m["id"] for m in matches],
            },
        )
    block = matches[0]
    if block["type"] not in _RICH_TEXT_BLOCK_TYPES:
        raise NotionEditError(
            422,
            {
                "op_index": i,
                "error": "unsupported_block_type",
                "message": (
                    f"matched block is type '{block['type']}', which has no "
                    "editable rich_text"
                ),
            },
        )
    return {
        "op_index": i,
        "kind": "replace",
        "block": block,
        "before_text": block["text"],
        "after_text": block["text"].replace(old, new),
    }


def _resolve_append(i: int, op: dict[str, Any]) -> dict[str, Any]:
    ap = op["append"]
    if not isinstance(ap, dict) or not isinstance(ap.get("children"), list):
        raise NotionEditError(
            422,
            {
                "op_index": i,
                "error": "malformed_op",
                "message": "append op requires {children: [<block>, ...]}",
            },
        )
    return {
        "op_index": i,
        "kind": "append",
        "children": ap["children"],
        "after": ap.get("after_block_id"),
    }


def _plan_entry_preview(p: dict[str, Any]) -> dict[str, Any]:
    if p["kind"] == "replace":
        return {
            "op_index": p["op_index"],
            "kind": "replace",
            "block_id": p["block"]["id"],
            "before_text": p["before_text"],
            "after_text": p["after_text"],
        }
    return {
        "op_index": p["op_index"],
        "kind": "append",
        "appended_block_ids": [],
    }


def _verify(client: NotionClient, applied: list[dict[str, Any]]) -> bool:
    """Re-fetch each replaced block and confirm the new text is present."""
    for entry in applied:
        if entry["kind"] != "replace":
            continue
        try:
            block = client.retrieve_block(entry["block_id"])
        except NotionAPIError:
            return False
        if entry["after_text"] not in nb.block_to_text(block):
            return False
    return True
