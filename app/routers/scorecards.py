import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path

from ..auth import require_bearer
from ..clients import AuthorNotFound, ClientNotFound, get_author
from ..config import Settings, get_settings
from ..lib.aggregate import aggregate
from ..lib.match_pieces import match
from ..lib.parse_linkedin import parse as parse_linkedin
from ..lib.parse_scrape import normalize as normalize_scrape
from ..models import ScorecardRequest, ScorecardResponse
from ..notion import NotionAPIError, NotionClient
from ..storage import load_latest_scrape, save_scrape

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", dependencies=[Depends(require_bearer)])


@router.post(
    "/scorecards/{client_slug}/{author_slug}",
    response_model=ScorecardResponse,
)
def create_scorecard(
    body: ScorecardRequest,
    client_slug: str = Path(..., min_length=1),
    author_slug: str = Path(..., min_length=1),
    settings: Settings = Depends(get_settings),
) -> ScorecardResponse:
    request_id = uuid.uuid4().hex
    logger.info(
        "scorecard request client=%s author=%s req=%s",
        client_slug,
        author_slug,
        request_id,
    )

    try:
        client_cfg, author_cfg = get_author(client_slug, author_slug)
    except ClientNotFound:
        raise HTTPException(
            status_code=404, detail=f"Unknown client_slug: {client_slug}"
        )
    except AuthorNotFound:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown author_slug: {client_slug}/{author_slug}",
        )

    today = datetime.now(timezone.utc).date()
    end_date = body.end_date or today
    start_date = body.start_date or (end_date - timedelta(days=30))
    if start_date > end_date:
        raise HTTPException(
            status_code=422, detail="start_date must be on or before end_date"
        )

    if body.scrape_paste:
        try:
            parsed = parse_linkedin(
                body.scrape_paste, author_name=author_cfg.get("name")
            )
        except Exception as exc:  # noqa: BLE001 — convert any parse error to 422
            logger.exception("parse_linkedin failed req=%s", request_id)
            raise HTTPException(
                status_code=422,
                detail=f"Could not parse scrape paste: {exc}",
            )
        if not parsed:
            raise HTTPException(
                status_code=422,
                detail="scrape_paste did not contain any recognizable posts",
            )
        scrape = normalize_scrape(
            parsed,
            reference_date=today,
            start_date=start_date,
            end_date=end_date,
        )
        save_scrape(client_slug, author_slug, body.scrape_paste, scrape)
    else:
        latest = load_latest_scrape(client_slug, author_slug)
        if latest is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "No scrape_paste provided and no prior scrape found in "
                    f"storage for {client_slug}/{author_slug}"
                ),
            )
        scrape = latest

    notion_cfg = client_cfg.get("notion", {})
    if not notion_cfg.get("content_db_id") or not notion_cfg.get("resources_db_id"):
        raise HTTPException(
            status_code=503,
            detail=(
                f"client '{client_slug}' is missing notion.content_db_id or "
                "notion.resources_db_id in clients.json"
            ),
        )

    try:
        client = NotionClient(settings)
        pieces = _fetch_pieces(client, notion_cfg, author_cfg)
        sessions = _fetch_sessions(client, notion_cfg, author_cfg)
    except NotionAPIError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "Notion API error fetching inputs",
                "message": exc.message,
                "notion_status": exc.status,
                "notion_request_id": exc.request_id,
                "request_id": request_id,
            },
        )

    matched = match(pieces, scrape)

    scorecard = aggregate(
        client_slug=client_slug,
        author_slug=author_slug,
        author_name=author_cfg.get("name", author_slug),
        start_date=start_date,
        end_date=end_date,
        matched_pieces=matched,
        sessions=sessions,
        scrape=scrape,
    )

    notion_url: str | None = None
    try:
        page = _create_scorecard_page(
            client, notion_cfg, author_cfg, scorecard, start_date, end_date
        )
        notion_url = page.get("url")
    except NotionAPIError as exc:
        logger.error(
            "Failed to create Notion scorecard page req=%s: %s",
            request_id,
            exc.message,
        )

    return ScorecardResponse(
        request_id=request_id, scorecard=scorecard, notion_url=notion_url
    )


def _fetch_pieces(
    client: NotionClient,
    notion_cfg: dict[str, Any],
    author_cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    db_id = notion_cfg["content_db_id"]
    author_prop = notion_cfg.get("author_property", "Author")
    author_value = author_cfg.get("notion_value") or author_cfg.get("name")
    filter_ = _author_filter(author_prop, author_value)
    return [
        _piece_from_notion(client, row, notion_cfg)
        for row in client.query_database_all(db_id, filter_=filter_)
    ]


def _fetch_sessions(
    client: NotionClient,
    notion_cfg: dict[str, Any],
    author_cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    db_id = notion_cfg["resources_db_id"]
    author_prop = notion_cfg.get("author_property", "Author")
    type_prop = notion_cfg.get("type_property", "Type")
    type_value = notion_cfg.get("signal_file_type_value", "Signal File")
    author_value = author_cfg.get("notion_value") or author_cfg.get("name")
    filter_ = {
        "and": [
            _author_filter(author_prop, author_value),
            {
                "or": [
                    {"property": type_prop, "select": {"equals": type_value}},
                    {"property": type_prop, "multi_select": {"contains": type_value}},
                ]
            },
        ]
    }
    return [
        _session_from_notion(row)
        for row in client.query_database_all(db_id, filter_=filter_)
    ]


def _author_filter(author_prop: str, author_value: str) -> dict[str, Any]:
    """Match Author whether stored as select, multi_select, rich_text, or title."""
    return {
        "or": [
            {"property": author_prop, "select": {"equals": author_value}},
            {"property": author_prop, "multi_select": {"contains": author_value}},
            {"property": author_prop, "rich_text": {"equals": author_value}},
            {"property": author_prop, "title": {"equals": author_value}},
        ]
    }


def _piece_from_notion(
    client: NotionClient, row: dict[str, Any], notion_cfg: dict[str, Any]
) -> dict[str, Any]:
    props = row.get("properties", {})
    stage_prop = notion_cfg.get("stage_property", "Stage")
    body = ""
    try:
        body = _assemble_body(client, row["id"])
    except NotionAPIError:
        body = ""
    return {
        "id": row.get("id"),
        "title": _read_title(props),
        "stage": _read_select(props.get(stage_prop, {})),
        "url": row.get("url"),
        "body": body,
        "date": _read_date(props.get("Date") or props.get("Published") or {}),
    }


def _session_from_notion(row: dict[str, Any]) -> dict[str, Any]:
    props = row.get("properties", {})
    return {
        "id": row.get("id"),
        "title": _read_title(props),
        "url": row.get("url"),
        "date": _read_date(props.get("Date") or props.get("Session date") or {}),
    }


def _read_title(props: dict[str, Any]) -> str:
    for v in props.values():
        if isinstance(v, dict) and v.get("type") == "title":
            return "".join(part.get("plain_text", "") for part in v.get("title", []))
    return ""


def _read_select(prop: dict[str, Any]) -> str | None:
    if not prop:
        return None
    if prop.get("type") == "select":
        sel = prop.get("select")
        return sel.get("name") if sel else None
    if prop.get("type") == "status":
        st = prop.get("status")
        return st.get("name") if st else None
    return None


def _read_date(prop: dict[str, Any]) -> str | None:
    if not prop or prop.get("type") != "date":
        return None
    d = prop.get("date")
    return d.get("start") if d else None


def _assemble_body(client: NotionClient, page_id: str) -> str:
    parts: list[str] = []
    for block in client.get_block_children_all(page_id):
        text = _block_to_text(block)
        if text:
            parts.append(text)
    return "\n".join(parts)


def _block_to_text(block: dict[str, Any]) -> str:
    btype = block.get("type")
    if not btype:
        return ""
    inner = block.get(btype, {})
    if not isinstance(inner, dict):
        return ""
    rich = inner.get("rich_text") or inner.get("text") or []
    return "".join(part.get("plain_text", "") for part in rich)


def _create_scorecard_page(
    client: NotionClient,
    notion_cfg: dict[str, Any],
    author_cfg: dict[str, Any],
    scorecard: dict[str, Any],
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    db_id = notion_cfg["resources_db_id"]
    type_prop = notion_cfg.get("type_property", "Type")
    type_value = notion_cfg.get("scorecard_type_value", "Scorecard")
    author_prop = notion_cfg.get("author_property", "Author")
    author_value = author_cfg.get("notion_value") or author_cfg.get("name")

    title = (
        f"Scorecard — {author_cfg.get('name', '')} — "
        f"{start_date.isoformat()} to {end_date.isoformat()}"
    )
    properties: dict[str, Any] = {
        "Name": {"title": [{"type": "text", "text": {"content": title}}]},
        type_prop: {"select": {"name": type_value}},
        author_prop: {"select": {"name": author_value}},
    }
    children = _scorecard_to_blocks(scorecard)
    return client.create_page(
        parent={"database_id": db_id},
        properties=properties,
        children=children,
    )


def _scorecard_to_blocks(scorecard: dict[str, Any]) -> list[dict[str, Any]]:
    totals = scorecard.get("totals", {})
    summary = (
        f"Pieces: {totals.get('pieces', 0)} "
        f"(matched {totals.get('pieces_matched', 0)}, "
        f"unmatched {totals.get('pieces_unmatched', 0)})\n"
        f"Scrape posts: {totals.get('scrape_posts', 0)}\n"
        f"Sessions: {totals.get('sessions', 0)}"
    )
    blocks: list[dict[str, Any]] = [
        _para(summary),
        _heading_2("Top pieces"),
    ]
    for p in scorecard.get("top_pieces", [])[:10]:
        line = f"{p.get('engagement', 0)} — {p.get('title') or '(untitled)'}"
        if p.get("url"):
            line += f" — {p['url']}"
        blocks.append(_bullet(line))
    return blocks


def _para(text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": text}}]
        },
    }


def _heading_2(text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "heading_2",
        "heading_2": {
            "rich_text": [{"type": "text", "text": {"content": text}}]
        },
    }


def _bullet(text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {
            "rich_text": [{"type": "text", "text": {"content": text}}]
        },
    }
