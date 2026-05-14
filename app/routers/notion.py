"""Generic Notion REST endpoints.

Thin wrappers over the ``app.notion`` primitives, exposing deterministic
Notion querying/enumeration/writing as authenticated HTTP endpoints.

Errors from the Notion API bubble up as ``NotionAPIError`` and are handled
by the global exception handler in ``app.main`` (returns 502 with the
notion_request_id for debugging).
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import Path as PathParam
from pydantic import BaseModel, Field

from ..auth import require_bearer
from ..config import Settings, get_settings
from ..notion import NotionClient
from ..notion import blocks as nb
from ..workflows.notion_edit import NotionEditError, run_update_content

router = APIRouter(
    prefix="/v1/notion",
    dependencies=[Depends(require_bearer)],
    tags=["notion"],
)

# Cap how many rows a single /databases/{id}/query response can return.
# Notion's own page_size max is 100; this cap is for total rows across
# auto-pagination. Callers can raise it up to MAX_RESULTS_HARD_CAP.
DEFAULT_MAX_RESULTS = 1000
MAX_RESULTS_HARD_CAP = 10000


def _client(settings: Settings) -> NotionClient:
    if not settings.notion_token:
        raise HTTPException(503, detail="NOTION_API_TOKEN is not configured")
    return NotionClient(settings)


# ---------------- databases ----------------


class DatabaseQueryRequest(BaseModel):
    filter: dict[str, Any] | None = None
    sorts: list[dict[str, Any]] | None = None
    max_results: int = Field(DEFAULT_MAX_RESULTS, ge=1, le=MAX_RESULTS_HARD_CAP)


class DatabaseQueryResponse(BaseModel):
    results: list[dict[str, Any]]
    count: int
    truncated: bool


@router.post(
    "/databases/{db_id}/query",
    response_model=DatabaseQueryResponse,
    summary="Query a Notion database with auto-pagination",
)
def query_database(
    body: DatabaseQueryRequest,
    db_id: str = PathParam(..., min_length=1),
    settings: Settings = Depends(get_settings),
) -> DatabaseQueryResponse:
    """Run a Notion ``databases/{id}/query`` and auto-paginate.

    The Notion filter/sorts dicts pass through verbatim — use
    ``app.notion.filters`` (or compose your own) on the caller side.
    Stops after ``max_results`` rows (default 1000, hard cap 10000).
    """
    client = _client(settings)
    results: list[dict[str, Any]] = []
    for row in client.query_database_all(
        db_id, filter_=body.filter, sorts=body.sorts
    ):
        results.append(row)
        if len(results) >= body.max_results:
            break
    truncated = len(results) >= body.max_results
    return DatabaseQueryResponse(
        results=results, count=len(results), truncated=truncated
    )


class PropertySchema(BaseModel):
    type: str | None = None
    options: list[str] | None = None


class DatabaseSchemaResponse(BaseModel):
    id: str
    title: str | None = None
    properties: dict[str, PropertySchema]


@router.get(
    "/databases/{db_id}/schema",
    response_model=DatabaseSchemaResponse,
    summary="Return simplified schema (property types + select options)",
)
def get_database_schema(
    db_id: str = PathParam(..., min_length=1),
    settings: Settings = Depends(get_settings),
) -> DatabaseSchemaResponse:
    """Strip Notion's verbose database response down to what callers
    actually need to build correctly-typed filters: each property's type,
    and for select/multi_select/status, the list of valid option names.
    """
    client = _client(settings)
    db = client.retrieve_database(db_id)

    title: str | None = None
    title_field = db.get("title", [])
    if isinstance(title_field, list) and title_field:
        title = "".join(
            part.get("plain_text", "")
            for part in title_field
            if isinstance(part, dict)
        )

    simplified: dict[str, PropertySchema] = {}
    for name, prop in db.get("properties", {}).items():
        if not isinstance(prop, dict):
            continue
        ptype = prop.get("type")
        entry = PropertySchema(type=ptype)
        if ptype in ("select", "multi_select", "status"):
            inner = prop.get(ptype, {})
            options = inner.get("options", []) if isinstance(inner, dict) else []
            entry.options = [
                o.get("name")
                for o in options
                if isinstance(o, dict) and o.get("name")
            ]
        simplified[name] = entry

    return DatabaseSchemaResponse(
        id=db.get("id", db_id), title=title, properties=simplified
    )


# ---------------- pages ----------------


@router.get(
    "/pages/{page_id}",
    summary="Retrieve a single Notion page (full property payload)",
)
def get_page(
    page_id: str = PathParam(..., min_length=1),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return _client(settings).retrieve_page(page_id)


class PageBodyResponse(BaseModel):
    page_id: str
    plain_text: str
    length: int


@router.get(
    "/pages/{page_id}/body",
    response_model=PageBodyResponse,
    summary="Assemble a page's body as plain text (paginated block_children)",
)
def get_page_body(
    page_id: str = PathParam(..., min_length=1),
    settings: Settings = Depends(get_settings),
) -> PageBodyResponse:
    client = _client(settings)
    text = nb.assemble_body_text(client, page_id)
    return PageBodyResponse(page_id=page_id, plain_text=text, length=len(text))


class CreatePageRequest(BaseModel):
    parent: dict[str, Any]
    properties: dict[str, Any]
    children: list[dict[str, Any]] | None = None


@router.post("/pages", summary="Create a Notion page")
def create_page(
    body: CreatePageRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return _client(settings).create_page(
        parent=body.parent, properties=body.properties, children=body.children
    )


class UpdatePageRequest(BaseModel):
    properties: dict[str, Any] | None = None
    archived: bool | None = None


@router.patch("/pages/{page_id}", summary="Update a Notion page")
def update_page(
    body: UpdatePageRequest,
    page_id: str = PathParam(..., min_length=1),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return _client(settings).update_page(
        page_id, properties=body.properties, archived=body.archived
    )


# ---------------- blocks ----------------


class BlockChild(BaseModel):
    id: str | None = None
    type: str | None = None
    has_children: bool = False
    text: str = ""
    raw: dict[str, Any]


class BlockChildrenResponse(BaseModel):
    results: list[BlockChild]
    next_cursor: str | None = None
    has_more: bool = False


@router.get(
    "/blocks/{block_id}/children",
    response_model=BlockChildrenResponse,
    summary="List a block's (or page's) child blocks with stable IDs + text",
)
def list_block_children(
    block_id: str = PathParam(..., min_length=1),
    recursive: bool = Query(False),
    page_size: int = Query(100, ge=1, le=100),
    cursor: str | None = Query(None),
    settings: Settings = Depends(get_settings),
) -> BlockChildrenResponse:
    """Enumerate child blocks. ``recursive=true`` walks the tree (depth 2)
    and returns every descendant flattened, with no pagination. Otherwise
    returns one Notion page of direct children, honoring ``cursor``.
    """
    client = _client(settings)
    if recursive:
        rows = nb.iter_block_tree(client, block_id, max_depth=2)
        return BlockChildrenResponse(
            results=[BlockChild(**r) for r in rows],
            next_cursor=None,
            has_more=False,
        )
    data = client.list_block_children(
        block_id, start_cursor=cursor, page_size=page_size
    )
    rows = [nb.normalize_block(b) for b in data.get("results", [])]
    return BlockChildrenResponse(
        results=[BlockChild(**r) for r in rows],
        next_cursor=data.get("next_cursor"),
        has_more=bool(data.get("has_more", False)),
    )


class AppendBlocksRequest(BaseModel):
    children: list[dict[str, Any]]
    after: str | None = None


class AppendBlocksResponse(BaseModel):
    results: list[dict[str, Any]]
    count: int


@router.post(
    "/blocks/{block_id}/children",
    response_model=AppendBlocksResponse,
    summary="Append child blocks to a block (or page)",
)
def append_block_children(
    body: AppendBlocksRequest,
    block_id: str = PathParam(..., min_length=1),
    settings: Settings = Depends(get_settings),
) -> AppendBlocksResponse:
    """Append blocks. Pages are blocks, so ``block_id`` may be a page id.
    ``children`` and ``after`` pass through verbatim to Notion."""
    result = _client(settings).append_block_children(
        block_id, body.children, after=body.after
    )
    appended = result.get("results", [])
    return AppendBlocksResponse(results=appended, count=len(appended))


class UpdateBlockResponse(BaseModel):
    id: str | None = None
    type: str | None = None
    text: str = ""
    raw: dict[str, Any]


@router.patch(
    "/blocks/{block_id}",
    response_model=UpdateBlockResponse,
    summary="Update a single block's content",
)
def update_block(
    body: dict[str, Any],
    block_id: str = PathParam(..., min_length=1),
    settings: Settings = Depends(get_settings),
) -> UpdateBlockResponse:
    """Update one block. Body is the type-keyed content dict, passed through
    verbatim to Notion, e.g.
    ``{"paragraph": {"rich_text": [{"type": "text", "text": {"content": "..."}}]}}``.
    """
    updated = _client(settings).update_block(block_id, body)
    return UpdateBlockResponse(
        id=updated.get("id"),
        type=updated.get("type"),
        text=nb.block_to_text(updated),
        raw=updated,
    )


# ---------------- surgical page edits ----------------


class UpdateContentRequest(BaseModel):
    operations: list[dict[str, Any]]
    dry_run: bool = False


@router.post(
    "/pages/{page_id}/update_content",
    summary="Surgically edit a page: old_str->new_str replaces + block appends",
)
def update_page_content(
    body: UpdateContentRequest,
    page_id: str = PathParam(..., min_length=1),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Server-side orchestration of surgical page edits.

    ``operations`` is an ordered list of either:
      - ``{"old_str": "...", "new_str": "..."}`` — replace the matched
        block's text. Must match exactly one block on the page or the whole
        request fails 422 (``no_match`` / ``ambiguous_match``).
      - ``{"append": {"children": [<block>...], "after_block_id": "..."}}``
        — append blocks (``after_block_id`` optional).

    ``dry_run: true`` returns the resolved plan without writing. v1 replaces
    drop inline annotations inside the edited block — see API.md.
    """
    try:
        return run_update_content(
            settings, page_id, body.operations, body.dry_run
        )
    except NotionEditError as exc:
        raise HTTPException(exc.status, detail=exc.detail)
