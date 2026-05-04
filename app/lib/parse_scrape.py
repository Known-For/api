"""Normalize parse_linkedin output: resolve relative dates and filter by range."""
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from dateutil import parser as date_parser

REL_PATTERN = re.compile(r"^(\d+)\s*(s|m|h|d|w|mo|yr)$", re.IGNORECASE)


def resolve_date(raw: str | None, reference: date) -> date | None:
    """Resolve a LinkedIn-style date string to an absolute date."""
    if not raw:
        return None
    raw = raw.strip()
    if raw.lower() == "now":
        return reference

    m = REL_PATTERN.match(raw)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        if unit == "s":
            delta = timedelta(seconds=n)
        elif unit == "m":
            delta = timedelta(minutes=n)
        elif unit == "h":
            delta = timedelta(hours=n)
        elif unit == "d":
            delta = timedelta(days=n)
        elif unit == "w":
            delta = timedelta(weeks=n)
        elif unit == "mo":
            delta = timedelta(days=30 * n)
        elif unit == "yr":
            delta = timedelta(days=365 * n)
        else:
            return None
        return reference - delta

    try:
        parsed = date_parser.parse(raw, default=datetime(reference.year, 1, 1))
        return parsed.date()
    except (ValueError, OverflowError):
        return None


def normalize(
    posts: list[dict[str, Any]],
    *,
    reference_date: date | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict[str, Any]]:
    """Resolve dates, dedupe, and filter posts by [start_date, end_date]."""
    if reference_date is None:
        reference_date = datetime.now(timezone.utc).date()

    out: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str]] = set()
    for p in posts:
        d = resolve_date(p.get("date_str"), reference_date)
        if start_date and d and d < start_date:
            continue
        if end_date and d and d > end_date:
            continue
        body_key = (p.get("body") or "")[:200]
        key = (p.get("url"), body_key)
        if key in seen:
            continue
        seen.add(key)
        reactions = p.get("reactions", 0)
        comments = p.get("comments", 0)
        reposts = p.get("reposts", 0)
        out.append(
            {
                "date": d.isoformat() if d else None,
                "url": p.get("url"),
                "body": p.get("body"),
                "reactions": reactions,
                "comments": comments,
                "reposts": reposts,
                "engagement": reactions + comments + reposts,
            }
        )
    return out
