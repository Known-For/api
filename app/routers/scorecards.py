import json
import logging
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi import Path as PathParam

from ..auth import require_bearer
from ..clients import ClientNotFound, get_client, get_schema_hints
from ..config import Settings, get_settings
from ..lib.match_pieces import match as match_pieces
from ..models import ScorecardRequest, ScorecardResponse
from ..notion import NotionAPIError, NotionClient

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", dependencies=[Depends(require_bearer)])

LIB_DIR = Path(__file__).resolve().parent.parent / "lib"


def _slug_to_display(slug: str) -> str:
    return " ".join(p.capitalize() for p in slug.split("-"))


@router.post(
    "/scorecards/{client_slug}/{author_slug}",
    response_model=ScorecardResponse,
)
def create_scorecard(
    body: ScorecardRequest,
    client_slug: str = PathParam(..., min_length=1),
    author_slug: str = PathParam(..., min_length=1),
    settings: Settings = Depends(get_settings),
) -> ScorecardResponse:
    request_id = uuid.uuid4().hex
    logger.info(
        "scorecard req=%s client=%s author=%s",
        request_id,
        client_slug,
        author_slug,
    )

    try:
        client_cfg = get_client(client_slug)
    except ClientNotFound:
        raise HTTPException(404, detail=f"Unknown client_slug: {client_slug}")

    today = datetime.now(timezone.utc).date()
    end_date = body.end_date or today
    start_date = body.start_date or (end_date - timedelta(days=30))
    if start_date > end_date:
        raise HTTPException(422, detail="start_date must be on or before end_date")

    author_display = _slug_to_display(author_slug)
    schema = get_schema_hints()

    work_dir = settings.data_dir / "scrapes" / client_slug / author_slug
    work_dir.mkdir(parents=True, exist_ok=True)

    # Stage 1 — get the scrape posts file
    if body.scrape_paste:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        raw_path = work_dir / f"linkedin-scrape-raw-{ts}.md"
        raw_path.write_text(body.scrape_paste)
        try:
            posts_path = _run_parse_linkedin(raw_path)
        except subprocess.CalledProcessError as exc:
            logger.error(
                "parse_linkedin failed req=%s stderr=%s", request_id, exc.stderr
            )
            raise HTTPException(
                422,
                detail=f"parse_linkedin failed: {exc.stderr or exc.stdout}",
            )
        except RuntimeError as exc:
            raise HTTPException(422, detail=str(exc))
    else:
        posts_path = _latest_posts_file(work_dir)
        if posts_path is None:
            raise HTTPException(
                422,
                detail=(
                    "No scrape_paste provided and no existing scrape on disk for "
                    f"{client_slug}/{author_slug}"
                ),
            )

    # Stage 3 — parse_scrape -> JSON
    try:
        scrape_doc = _run_parse_scrape(posts_path)
    except subprocess.CalledProcessError as exc:
        logger.error(
            "parse_scrape failed req=%s stderr=%s", request_id, exc.stderr
        )
        raise HTTPException(
            500, detail=f"parse_scrape failed: {exc.stderr or exc.stdout}"
        )

    # Stages 4 & 5 — pull from Notion
    if not settings.notion_token:
        raise HTTPException(503, detail="NOTION_API_TOKEN is not configured")
    notion = NotionClient(settings)

    try:
        pieces = _fetch_pieces(
            notion, client_cfg, schema, author_slug, author_display
        )
        sessions = _fetch_sessions(
            notion, client_cfg, schema, author_slug, author_display
        )
    except NotionAPIError as exc:
        raise HTTPException(
            502,
            detail={
                "error": "Notion query failed",
                "message": exc.message,
                "notion_status": exc.status,
                "notion_request_id": exc.request_id,
                "request_id": request_id,
            },
        )

    # Stage 6 — match (in-process)
    match_result = match_pieces(pieces, scrape_doc["posts"])

    # Stage 7 — aggregate
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        scrape_path = td_path / "scrape.json"
        baseline_path = td_path / "baseline.json"
        match_path = td_path / "match.json"
        sessions_path = td_path / "sessions.json"
        pieces_path = td_path / "pieces.json"

        scrape_path.write_text(json.dumps(scrape_doc))
        baseline_path.write_text(json.dumps(scrape_doc))  # no baseline yet
        match_path.write_text(json.dumps(match_result))
        sessions_path.write_text(json.dumps(sessions))
        pieces_path.write_text(json.dumps(pieces))

        try:
            scorecard = _run_aggregate(
                scrape_json=scrape_path,
                baseline_json=baseline_path,
                match_json=match_path,
                sessions_json=sessions_path,
                pieces_json=pieces_path,
                start_date=start_date,
                end_date=end_date,
                author_name=author_display,
                client_name=client_cfg.get("display_name", client_slug),
            )
        except subprocess.CalledProcessError as exc:
            logger.error(
                "aggregate failed req=%s stderr=%s", request_id, exc.stderr
            )
            raise HTTPException(
                500, detail=f"aggregate failed: {exc.stderr or exc.stdout}"
            )

    # Stage 8 — write the scorecard page in the client's Resources DB
    notion_url: str | None = None
    try:
        page = _create_scorecard_page(notion, client_cfg, scorecard)
        notion_url = page.get("url")
    except NotionAPIError as exc:
        logger.error(
            "Failed to write scorecard page req=%s: %s",
            request_id,
            exc.message,
        )

    return ScorecardResponse(
        request_id=request_id, scorecard=scorecard, notion_url=notion_url
    )


# ---------------------- subprocess helpers ----------------------


def _run_parse_linkedin(raw_path: Path) -> Path:
    """Run parse_linkedin.py and return the produced posts file."""
    subprocess.run(
        [sys.executable, str(LIB_DIR / "parse_linkedin.py"), str(raw_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    today = datetime.now().strftime("%Y-%m-%d")
    candidates = sorted(raw_path.parent.glob(f"*-posts-{today}.md"))
    if not candidates:
        raise RuntimeError(
            "parse_linkedin did not produce a posts file — paste may be malformed"
        )
    return candidates[-1]


def _run_parse_scrape(posts_path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, str(LIB_DIR / "parse_scrape.py"), str(posts_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def _run_aggregate(
    *,
    scrape_json: Path,
    baseline_json: Path,
    match_json: Path,
    sessions_json: Path,
    pieces_json: Path,
    start_date: date,
    end_date: date,
    author_name: str,
    client_name: str,
) -> dict[str, Any]:
    result = subprocess.run(
        [
            sys.executable,
            str(LIB_DIR / "aggregate.py"),
            "--scrape-json", str(scrape_json),
            "--baseline-json", str(baseline_json),
            "--match-json", str(match_json),
            "--sessions-json", str(sessions_json),
            "--pieces-json", str(pieces_json),
            "--start-date", start_date.isoformat(),
            "--end-date", end_date.isoformat(),
            "--author-name", author_name,
            "--client-name", client_name,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def _latest_posts_file(work_dir: Path) -> Path | None:
    candidates = list(work_dir.glob("*-posts-*.md"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


# ---------------------- Notion query helpers ----------------------


def _author_filter(prop: str, slug: str, display: str) -> dict[str, Any]:
    """OR across the common author-property shapes and both slug/display values."""
    return {
        "or": [
            {"property": prop, "select": {"equals": slug}},
            {"property": prop, "select": {"equals": display}},
            {"property": prop, "multi_select": {"contains": slug}},
            {"property": prop, "multi_select": {"contains": display}},
            {"property": prop, "rich_text": {"equals": slug}},
            {"property": prop, "rich_text": {"equals": display}},
            {"property": prop, "title": {"equals": slug}},
            {"property": prop, "title": {"equals": display}},
        ]
    }


def _read_property_value(prop: dict[str, Any] | None) -> str | None:
    if not isinstance(prop, dict):
        return None
    t = prop.get("type")
    if t == "title":
        return "".join(part.get("plain_text", "") for part in prop.get("title", []))
    if t == "rich_text":
        return "".join(
            part.get("plain_text", "") for part in prop.get("rich_text", [])
        )
    if t == "select":
        sel = prop.get("select")
        return sel.get("name") if sel else None
    if t == "status":
        st = prop.get("status")
        return st.get("name") if st else None
    if t == "multi_select":
        return ", ".join(s.get("name", "") for s in prop.get("multi_select", []))
    if t == "date":
        d = prop.get("date")
        return d.get("start") if d else None
    return None


def _first_present(
    props: dict[str, Any], candidates: list[str]
) -> dict[str, Any] | None:
    for c in candidates:
        if c in props:
            return props[c]
    return None


def _assemble_body(notion: NotionClient, page_id: str) -> str:
    parts: list[str] = []
    try:
        for block in notion.get_block_children_all(page_id):
            t = block.get("type")
            if not t:
                continue
            inner = block.get(t, {})
            if not isinstance(inner, dict):
                continue
            rich = inner.get("rich_text") or inner.get("text") or []
            text = "".join(p.get("plain_text", "") for p in rich)
            if text:
                parts.append(text)
    except NotionAPIError:
        return ""
    return "\n".join(parts)


def _fetch_pieces(
    notion: NotionClient,
    client_cfg: dict[str, Any],
    schema: dict[str, Any],
    author_slug: str,
    author_display: str,
) -> list[dict[str, Any]]:
    db_id = client_cfg["content_db_id"]
    title_cands = schema.get(
        "deliverable_title_field_candidates", ["Name", "Title"]
    )
    delivery_cands = schema.get(
        "deliverable_delivery_field_candidates", ["Delivery", "Date"]
    )
    author_cands = schema.get("deliverable_author_field_candidates", ["Author"])
    status_cands = schema.get("deliverable_status_field_candidates", ["Status"])

    author_prop = author_cands[0]
    filter_ = _author_filter(author_prop, author_slug, author_display)

    pieces: list[dict[str, Any]] = []
    for row in notion.query_database_all(db_id, filter_=filter_):
        props = row.get("properties", {})
        title = _read_property_value(_first_present(props, title_cands)) or ""
        delivered_date = _read_property_value(
            _first_present(props, delivery_cands)
        )
        status = _read_property_value(_first_present(props, status_cands))
        body = _assemble_body(notion, row["id"])

        pieces.append(
            {
                "id": row["id"],
                "title": title,
                "body": body,
                "delivered_date": delivered_date,
                "status": status,
            }
        )
    return pieces


def _fetch_sessions(
    notion: NotionClient,
    client_cfg: dict[str, Any],
    schema: dict[str, Any],
    author_slug: str,
    author_display: str,
) -> list[dict[str, Any]]:
    db_id = client_cfg["resources_db_id"]
    session_type_value = schema.get("session_type_value", "Signal File")
    date_cands = schema.get(
        "session_date_field_candidates", ["Session Date", "Date"]
    )
    author_cands = schema.get("deliverable_author_field_candidates", ["Author"])
    author_prop = author_cands[0]

    filter_ = {
        "and": [
            _author_filter(author_prop, author_slug, author_display),
            {
                "or": [
                    {"property": "Type", "select": {"equals": session_type_value}},
                    {
                        "property": "Type",
                        "multi_select": {"contains": session_type_value},
                    },
                ]
            },
        ]
    }

    real_date_cands = [c for c in date_cands if c != "createdTime"]

    sessions: list[dict[str, Any]] = []
    for row in notion.query_database_all(db_id, filter_=filter_):
        props = row.get("properties", {})
        title = _read_property_value(_first_present(props, ["Name", "Title"])) or ""
        session_date = _read_property_value(_first_present(props, real_date_cands))
        if not session_date and "createdTime" in date_cands:
            ct = row.get("created_time")
            session_date = ct[:10] if ct else None
        sessions.append(
            {"id": row["id"], "title": title, "session_date": session_date}
        )
    return sessions


# ---------------------- Notion page assembly ----------------------


def _create_scorecard_page(
    notion: NotionClient,
    client_cfg: dict[str, Any],
    scorecard: dict[str, Any],
) -> dict[str, Any]:
    db_id = client_cfg["resources_db_id"]
    meta = scorecard.get("meta", {})
    title = (
        f"{meta.get('author_name', '')} Scorecard — "
        f"{meta.get('start_date', '')} to {meta.get('end_date', '')}"
    )
    properties = {
        "Name": {"title": [{"type": "text", "text": {"content": title}}]},
        "Type": {"select": {"name": "Scorecard"}},
    }
    children = _scorecard_to_blocks(scorecard)
    return notion.create_page(
        parent={"database_id": db_id},
        properties=properties,
        children=children,
    )


def _scorecard_to_blocks(s: dict[str, Any]) -> list[dict[str, Any]]:
    meta = s.get("meta", {}) or {}
    header = s.get("header", {}) or {}
    s1 = s.get("stage1", {}) or {}
    s2 = s.get("stage2", {}) or {}
    s3 = s.get("stage3", {}) or {}
    s4 = s.get("stage4", {}) or {}
    md = s.get("match_diagnostics", {}) or {}
    gaps = s.get("data_gaps", []) or []

    blocks: list[dict[str, Any]] = []

    for line in [
        f"Author: {meta.get('author_name', '')}",
        f"Client: {meta.get('client_name', '')}",
        f"Date range: {meta.get('start_date', '')} to {meta.get('end_date', '')}",
        f"Generated: {meta.get('generated_date', '')}",
        f"Diagnosis: {header.get('diagnosis_label', '')}",
        f"Top issue: {header.get('top_issue', '')}",
        f"Most leveraged action: {header.get('leveraged_action', '')}",
    ]:
        blocks.append(_para(line))

    blocks.append(_heading2("Stage 1 — Session quality"))
    blocks.append(
        _para(f"Sessions held in range: {s1.get('sessions_held_in_range', 0)}")
    )
    blocks.append(
        _para(
            f"Last session: {s1.get('last_session_date') or '—'} "
            f"({_fmt(s1.get('days_since_last_session'))} days ago)"
        )
    )
    blocks.append(_para(f"Diagnosis: {s1.get('diagnosis', '—')}"))

    blocks.append(_heading2("Stage 2 — Deliverable volume"))
    blocks.append(
        _para(
            f"Pieces delivered in range: {s2.get('pieces_delivered_in_range', 0)}"
        )
    )
    for t in s2.get("piece_titles_in_range", []) or []:
        blocks.append(_bullet(t or "(untitled)"))

    blocks.append(_heading2("Stage 3 — Publishing rate"))
    pct = s3.get("pct_delivered_published")
    blocks.append(
        _para(
            f"Of pieces delivered in range: "
            f"{s3.get('delivered_in_range_published_count', 0)} of "
            f"{s3.get('delivered_in_range_count', 0)} published "
            f"({_fmt(pct)}%)"
        )
    )
    blocks.append(
        _para(
            f"Avg days delivery → publish (matched pairs): "
            f"{_fmt(s3.get('avg_days_delivery_to_publish'))}"
        )
    )
    blocks.append(
        _para(
            f"Stalled pieces (delivered >30d, unmatched): "
            f"{s3.get('stalled_count', 0)}"
        )
    )
    if s3.get("stalled_pieces"):
        blocks.append(_para("Stalled list:"))
        for st in s3["stalled_pieces"]:
            blocks.append(
                _bullet(
                    f"{st.get('title') or '(untitled)'} — delivered "
                    f"{st.get('delivered_date', '?')} "
                    f"({st.get('days_since_delivery')} days ago)"
                )
            )

    blocks.append(_heading2("Stage 4 — Platform performance"))
    blocks.append(
        _para(
            f"KF-matched posts in range: {s4.get('kf_matched_posts_in_range', 0)}"
        )
    )
    delta_r = s4.get("delta_pct_reactions")
    delta_c = s4.get("delta_pct_comments")
    blocks.append(
        _para(
            f"Avg reactions: {_fmt(s4.get('avg_reactions'))}"
            + (
                f" (baseline {s4.get('baseline_avg_reactions')}, Δ {delta_r:+.1f}%)"
                if delta_r is not None
                else ""
            )
        )
    )
    blocks.append(
        _para(
            f"Avg comments: {_fmt(s4.get('avg_comments'))}"
            + (
                f" (baseline {s4.get('baseline_avg_comments')}, Δ {delta_c:+.1f}%)"
                if delta_c is not None
                else ""
            )
        )
    )
    blocks.append(
        _para(
            "Avg impressions: Data unavailable (LinkedIn does not expose "
            "impressions in scrapes)"
        )
    )

    blocks.append(_heading2("Match diagnostics"))
    cb = md.get("confidence_breakdown", {}) or {}
    blocks.append(
        _para(
            f"Total pieces: {md.get('total_pieces', 0)} • "
            f"Matched: {md.get('matched', 0)} • "
            f"Unmatched: {md.get('unmatched', 0)} • "
            f"Confidence: high={cb.get('high', 0)} "
            f"medium={cb.get('medium', 0)} low={cb.get('low', 0)}"
        )
    )

    if gaps:
        blocks.append(_heading2("Data gaps"))
        for g in gaps:
            blocks.append(_bullet(g))

    return blocks


def _fmt(value: Any) -> str:
    return "—" if value is None else str(value)


def _para(text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": text}}]
        },
    }


def _heading2(text: str) -> dict[str, Any]:
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
