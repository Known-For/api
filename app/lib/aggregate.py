"""Compute the scorecard summary from matched pieces, sessions, and scrape."""
from collections import Counter
from datetime import date
from typing import Any


def aggregate(
    *,
    client_slug: str,
    author_slug: str,
    author_name: str,
    start_date: date,
    end_date: date,
    matched_pieces: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    scrape: list[dict[str, Any]],
) -> dict[str, Any]:
    """Produce the final scorecard JSON."""
    in_range = [p for p in matched_pieces if _piece_in_range(p, start_date, end_date)]

    stage_counts = Counter(p.get("stage") or "(unset)" for p in in_range)
    matched_count = sum(1 for p in in_range if p.get("match", {}).get("matched"))

    pieces_view: list[dict[str, Any]] = []
    for p in in_range:
        m = p.get("match", {})
        post = m.get("post") or {}
        pieces_view.append(
            {
                "id": p.get("id"),
                "title": p.get("title"),
                "stage": p.get("stage"),
                "url": p.get("url"),
                "matched": m.get("matched", False),
                "match_score": m.get("score"),
                "match_method": m.get("method"),
                "engagement": post.get("engagement", 0),
                "post_date": post.get("date"),
                "post_url": post.get("url"),
            }
        )

    pieces_view.sort(key=lambda p: p["engagement"], reverse=True)

    return {
        "client": client_slug,
        "author": {"slug": author_slug, "name": author_name},
        "date_range": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
        "totals": {
            "pieces": len(in_range),
            "pieces_matched": matched_count,
            "pieces_unmatched": len(in_range) - matched_count,
            "scrape_posts": len(scrape),
            "sessions": len(sessions),
        },
        "stages": dict(stage_counts),
        "top_pieces": pieces_view[:10],
        "pieces": pieces_view,
        "sessions": [
            {
                "id": s.get("id"),
                "title": s.get("title"),
                "date": s.get("date"),
                "url": s.get("url"),
            }
            for s in sessions
        ],
    }


def _piece_in_range(
    piece: dict[str, Any], start_date: date, end_date: date
) -> bool:
    pd = piece.get("date")
    if not pd:
        return True  # unknown-dated pieces pass through
    if isinstance(pd, str):
        try:
            pd = date.fromisoformat(pd[:10])
        except ValueError:
            return True
    return start_date <= pd <= end_date
