"""Client config endpoint — one server-side source of truth for DB IDs.

Saves callers (skills, cron jobs) from hardcoding Notion database IDs or
shipping their own copy of clients.json.
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi import Path as PathParam
from pydantic import BaseModel

from ..auth import require_bearer
from ..clients import ClientNotFound, get_client

router = APIRouter(
    prefix="/v1/clients",
    dependencies=[Depends(require_bearer)],
    tags=["clients"],
)


class ClientConfigResponse(BaseModel):
    slug: str
    display_name: str | None = None
    content_db_id: str | None = None
    resources_db_id: str | None = None
    author_property_verified: bool


@router.get("/{slug}", response_model=ClientConfigResponse)
def get_client_config(
    slug: str = PathParam(..., min_length=1),
) -> ClientConfigResponse:
    """Return the Notion DB IDs registered for a client in clients.json."""
    try:
        cfg: dict[str, Any] = get_client(slug)
    except ClientNotFound:
        raise HTTPException(404, detail=f"Unknown client slug: {slug}")
    # `_verified` is a date-stamped note string when the schema was confirmed,
    # or literal false when it wasn't. Coerce to a clean bool.
    return ClientConfigResponse(
        slug=slug,
        display_name=cfg.get("display_name"),
        content_db_id=cfg.get("content_db_id"),
        resources_db_id=cfg.get("resources_db_id"),
        author_property_verified=bool(cfg.get("_verified")),
    )
