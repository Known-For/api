"""Generic Notion REST endpoints.

Thin wrappers over the ``app.notion`` primitives, exposing deterministic
Notion querying/enumeration/writing as authenticated HTTP endpoints.

Errors from the Notion API bubble up as ``NotionAPIError`` and are handled
by the global exception handler in ``app.main`` (returns 502 with the
notion_request_id for debugging).
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi import Path as PathParam
from pydantic import BaseModel, Field

from ..auth import require_bearer
from ..config import Settings, get_settings
from ..notion import NotionClient
from ..notion import blocks as nb

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
