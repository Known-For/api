"""Author scorecard workflow.

Composes ``app.notion`` primitives with the verbatim author-scorecard lib
under ``app.lib`` to produce a Stage 1-4 scorecard and post it as a page in
the client's Resources DB.

What this module owns (scorecard-specific):
  * Storage layout for raw paste + parsed posts files
  * Subprocess invocation of parse_linkedin / parse_scrape / aggregate
  * Mapping from Notion rows to {pieces, sessions} JSON shapes the lib expects
  * The final scorecard page's block layout

What this module does NOT own:
  * HTTP-talking-to-Notion
  * Filter dict construction
  * Property value extraction
  * Block authoring (paragraph/heading/bullet)
These all live in ``app.notion`` and should be imported from there.
"""
import json
import logging
import subprocess
import sys
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..clients import get_schema_hints
from ..config import Settings
from ..lib.match_pieces import match as match_pieces
from ..notion import NotionAPIError, NotionClient
from ..notion import blocks as nb
from ..notion import filters as nf
from ..notion import properties as np

logger = logging.getLogger(__name__)

LIB_DIR = Path(__file__).resolve().parent.parent / "lib"


class ScorecardError(Exception):
    """Workflow-level error carrying an HTTP status hint for the router."""

    def __init__(self, status: int, detail: Any) -> None:
        super().__init__(detail if isinstance(detail, str) else str(detail))
        self.status = status
        self.detail = detail


def run(
    *,
    settings: Settings,
    client_slug: str,
    client_cfg: dict[str, Any],
    author_slug: str,
    start_date: date | None,
    end_date: date | None,
    scrape_paste: str | None,
) -> tuple[dict[str, Any], str | None, str]:
    """Run the full scorecard workflow.

    Returns ``(scorecard_dict, notion_page_url_or_None, request_id)``.
    Raises ``ScorecardError`` for expected failures so the router can map
    them to HTTP status codes.
    """
    request_id = uuid.uuid4().hex
    logger.info(
        "scorecard run req=%s client=%s author=%s",
        request_id,
        client_slug,
        author_slug,
    )

    today = datetime.now(timezone.utc).date()
    end = end_date or today
    start = start_date or (end - timedelta(days=30))
    if start > end:
        raise ScorecardError(422, "start_date must be on or before end_date")

    author_display = _slug_to_display(author_slug)
    work_dir = settings.data_dir / "scrapes" / client_slug / author_slug
    work_dir.mkdir(parents=True, exist_ok=True)

    posts_path = _resolve_posts_file(work_dir, scrape_paste, client_slug, author_slug)
    scrape_doc = _run_parse_scrape(posts_path, request_id)

    if not settings.notion_token:
        raise ScorecardError(503, "NOTION_API_TOKEN is not configured")
    notion = NotionClient(settings)
    schema = get_schema_hints()

    try:
        pieces = _fetch_pieces(notion, client_cfg, schema, author_slug, author_display)
        sessions = _fetch_sessions(notion, client_cfg, schema, author_slug, author_display)
    except NotionAPIError as exc:
        raise ScorecardError(
            502,
            {
                "error": "Notion query failed",
                "message": exc.message,
                "notion_status": exc.status,
                "notion_request_id": exc.request_id,
                "request_id": request_id,
            },
        )

    match_result = match_pieces(pieces, scrape_doc["posts"])

    scorecard = _run_aggregate(
        scrape_doc=scrape_doc,
        match_result=match_result,
        sessions=sessions,
        pieces=pieces,
        start_date=start,
        end_date=end,
        author_name=author_display,
        client_name=client_cfg.get("display_name", client_slug),
        request_id=request_id,
    )

    notion_url: str | None = None
    try:
        page = _post_scorecard_page(notion, client_cfg, scorecard)
        notion_url = page.get("url")
    except NotionAPIError as exc:
        logger.error(
            "scorecard page post failed req=%s: %s", request_id, exc.message
        )

    return scorecard, notion_url, request_id


# ---------------- Stage 1: get the scrape file ----------------


def _slug_to_display(slug: str) -> str:
    return " ".join(p.capitalize() for p in slug.split("-"))


def _resolve_posts_file(
    work_dir: Path,
    scrape_paste: str | None,
    client_slug: str,
    author_slug: str,
) -> Path:
    if scrape_paste:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        raw_path = work_dir / f"linkedin-scrape-raw-{ts}.md"
        raw_path.write_text(scrape_paste)
        try:
            return _run_parse_linkedin(raw_path)
        except subprocess.CalledProcessError as exc:
            raise ScorecardError(
                422, f"parse_linkedin failed: {exc.stderr or exc.stdout}"
            )
        except RuntimeError as exc:
            raise ScorecardError(422, str(exc))

    candidates = list(work_dir.glob("*-posts-*.md"))
    if not candidates:
        raise ScorecardError(
            422,
            (
                "No scrape_paste provided and no existing scrape on disk for "
                f"{client_slug}/{author_slug}"
            ),
        )
    return max(candidates, key=lambda p: p.stat().st_mtime)


# ---------------- subprocess wrappers around the verbatim lib ----------------


def _run_parse_linkedin(raw_path: Path) -> Path:
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


def _run_parse_scrape(posts_path: Path, request_id: str) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [sys.executable, str(LIB_DIR / "parse_scrape.py"), str(posts_path)],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        logger.error(
            "parse_scrape failed req=%s stderr=%s", request_id, exc.stderr
        )
        raise ScorecardError(
            500, f"parse_scrape failed: {exc.stderr or exc.stdout}"
        )
    return json.loads(result.stdout)


def _run_aggregate(
    *,
    scrape_doc: dict[str, Any],
    match_result: dict[str, Any],
    sessions: list[dict[str, Any]],
    pieces: list[dict[str, Any]],
    start_date: date,
    end_date: date,
    author_name: str,
    client_name: str,
    request_id: str,
) -> dict[str, Any]:
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
            result = subprocess.run(
                [
                    sys.executable,
                    str(LIB_DIR / "aggregate.py"),
                    "--scrape-json", str(scrape_path),
                    "--baseline-json", str(baseline_path),
                    "--match-json", str(match_path),
                    "--sessions-json", str(sessions_path),
                    "--pieces-json", str(pieces_path),
                    "--start-date", start_date.isoformat(),
                    "--end-date", end_date.isoformat(),
                    "--author-name", author_name,
                    "--client-name", client_name,
                ],
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            logger.error(
                "aggregate failed req=%s stderr=%s", request_id, exc.stderr
            )
            raise ScorecardError(
                500, f"aggregate failed: {exc.stderr or exc.stdout}"
            )

    return json.loads(result.stdout)


# ---------------- Stages 4 & 5: enumerate from Notion ----------------


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

    author_type = notion.get_property_type(db_id, author_prop)
    if not author_type:
        raise ScorecardError(
            503,
            f"content DB {db_id} has no property named '{author_prop}' "
            f"(checked {author_cands})",
        )
    filter_ = nf.author_match(author_prop, author_type, author_slug, author_display)

    pieces: list[dict[str, Any]] = []
    for row in notion.query_database_all(db_id, filter_=filter_):
        props = row.get("properties", {})
        pieces.append(
            {
                "id": row["id"],
                "title": np.read_first(props, title_cands) or "",
                "body": nb.assemble_body_text(notion, row["id"]),
                "delivered_date": np.read_first(props, delivery_cands),
                "status": np.read_first(props, status_cands),
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
    real_date_cands = [c for c in date_cands if c != "createdTime"]

    author_type = notion.get_property_type(db_id, author_prop)
    type_type = notion.get_property_type(db_id, "Type")
    if not author_type:
        raise ScorecardError(
            503,
            f"resources DB {db_id} has no property named '{author_prop}'",
        )
    if not type_type:
        raise ScorecardError(
            503,
            f"resources DB {db_id} has no property named 'Type'",
        )

    filter_ = nf.and_(
        nf.author_match(author_prop, author_type, author_slug, author_display),
        nf.type_match("Type", type_type, session_type_value),
    )

    sessions: list[dict[str, Any]] = []
    for row in notion.query_database_all(db_id, filter_=filter_):
        props = row.get("properties", {})
        title = np.read_first(props, ["Name", "Title"]) or ""
        session_date = np.read_first(props, real_date_cands)
        if not session_date and "createdTime" in date_cands:
            ct = row.get("created_time")
            session_date = ct[:10] if ct else None
        sessions.append(
            {"id": row["id"], "title": title, "session_date": session_date}
        )
    return sessions


# ---------------- Stage 8: post scorecard page ----------------


def _post_scorecard_page(
    notion: NotionClient,
    client_cfg: dict[str, Any],
    scorecard: dict[str, Any],
) -> dict[str, Any]:
    db_id = client_cfg["resources_db_id"]
    meta = scorecard.get("meta", {}) or {}
    title = (
        f"{meta.get('author_name', '')} Scorecard — "
        f"{meta.get('start_date', '')} to {meta.get('end_date', '')}"
    )
    properties = {
        "Name": {"title": [nb.text_run(title)]},
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
        blocks.append(nb.paragraph(line))

    blocks.append(nb.heading_2("Stage 1 — Session quality"))
    blocks.append(
        nb.paragraph(
            f"Sessions held in range: {s1.get('sessions_held_in_range', 0)}"
        )
    )
    blocks.append(
        nb.paragraph(
            f"Last session: {s1.get('last_session_date') or '—'} "
            f"({_fmt(s1.get('days_since_last_session'))} days ago)"
        )
    )
    blocks.append(nb.paragraph(f"Diagnosis: {s1.get('diagnosis', '—')}"))

    blocks.append(nb.heading_2("Stage 2 — Deliverable volume"))
    blocks.append(
        nb.paragraph(
            f"Pieces delivered in range: {s2.get('pieces_delivered_in_range', 0)}"
        )
    )
    for t in s2.get("piece_titles_in_range", []) or []:
        blocks.append(nb.bullet(t or "(untitled)"))

    blocks.append(nb.heading_2("Stage 3 — Publishing rate"))
    pct = s3.get("pct_delivered_published")
    blocks.append(
        nb.paragraph(
            f"Of pieces delivered in range: "
            f"{s3.get('delivered_in_range_published_count', 0)} of "
            f"{s3.get('delivered_in_range_count', 0)} published "
            f"({_fmt(pct)}%)"
        )
    )
    blocks.append(
        nb.paragraph(
            f"Avg days delivery → publish (matched pairs): "
            f"{_fmt(s3.get('avg_days_delivery_to_publish'))}"
        )
    )
    blocks.append(
        nb.paragraph(
            f"Stalled pieces (delivered >30d, unmatched): "
            f"{s3.get('stalled_count', 0)}"
        )
    )
    if s3.get("stalled_pieces"):
        blocks.append(nb.paragraph("Stalled list:"))
        for st in s3["stalled_pieces"]:
            blocks.append(
                nb.bullet(
                    f"{st.get('title') or '(untitled)'} — delivered "
                    f"{st.get('delivered_date', '?')} "
                    f"({st.get('days_since_delivery')} days ago)"
                )
            )

    blocks.append(nb.heading_2("Stage 4 — Platform performance"))
    blocks.append(
        nb.paragraph(
            f"KF-matched posts in range: {s4.get('kf_matched_posts_in_range', 0)}"
        )
    )
    delta_r = s4.get("delta_pct_reactions")
    delta_c = s4.get("delta_pct_comments")
    blocks.append(
        nb.paragraph(
            f"Avg reactions: {_fmt(s4.get('avg_reactions'))}"
            + (
                f" (baseline {s4.get('baseline_avg_reactions')}, Δ {delta_r:+.1f}%)"
                if delta_r is not None
                else ""
            )
        )
    )
    blocks.append(
        nb.paragraph(
            f"Avg comments: {_fmt(s4.get('avg_comments'))}"
            + (
                f" (baseline {s4.get('baseline_avg_comments')}, Δ {delta_c:+.1f}%)"
                if delta_c is not None
                else ""
            )
        )
    )
    blocks.append(
        nb.paragraph(
            "Avg impressions: Data unavailable "
            "(LinkedIn does not expose impressions in scrapes)"
        )
    )

    blocks.append(nb.heading_2("Match diagnostics"))
    cb = md.get("confidence_breakdown", {}) or {}
    blocks.append(
        nb.paragraph(
            f"Total pieces: {md.get('total_pieces', 0)} • "
            f"Matched: {md.get('matched', 0)} • "
            f"Unmatched: {md.get('unmatched', 0)} • "
            f"Confidence: high={cb.get('high', 0)} "
            f"medium={cb.get('medium', 0)} low={cb.get('low', 0)}"
        )
    )

    if gaps:
        blocks.append(nb.heading_2("Data gaps"))
        for g in gaps:
            blocks.append(nb.bullet(g))

    return blocks


def _fmt(value: Any) -> str:
    return "—" if value is None else str(value)
