#!/usr/bin/env python3
"""
Parse a Known For LinkedIn scrape file into post records.

Scrape format (observed in chuck-whitten-posts-2026-04-15.md and others):
- File header with author name and aggregate metrics (sometimes)
- Posts separated by `---`
- Each post starts with a date marker on its own line: `[1d]`, `[3w]`, `[2mo]`, `[1yr]`, or absolute like `Apr 15, 2026`
- Body follows
- Reactions and Comments lines near the end of each block
- Optional: Impressions line (rare; usually N/A)

Usage:
    python parse_scrape.py /path/to/author-posts-YYYY-MM-DD.md > posts.json

Output: JSON array of {date_marker, absolute_date, body, first_line, reactions, comments, impressions, char_count, word_count}.

The scrape filename date is treated as the reference point for relative date markers.
"""

import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path


REL_RE = re.compile(r"^\[(\d+)([dwmy](?:o|r)?)\](?:\s+\[[^\]]+\])*\s*$")
ABS_RE = re.compile(
    r"^(?:\[)?([A-Z][a-z]{2,8})\s+(\d{1,2}),?\s+(\d{4})(?:\])?(?:\s+\[[^\]]+\])*\s*$"
)
REACTIONS_RE = re.compile(r"^Reactions:\s*([\d,]+)\s*$", re.I)
COMMENTS_RE = re.compile(r"^Comments:\s*([\d,]+)\s*$", re.I)
IMPRESSIONS_RE = re.compile(r"^Impressions:\s*([\d,]+|N/A)\s*$", re.I)

MONTHS = {
    m: i + 1
    for i, m in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ]
    )
}
MONTHS_SHORT = {m[:3]: i for m, i in MONTHS.items()}


def scrape_date_from_filename(path: Path) -> date:
    """Extract YYYY-MM-DD from filename like chuck-whitten-posts-2026-04-15.md."""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", path.name)
    if not m:
        raise ValueError(f"Cannot find YYYY-MM-DD in filename: {path.name}")
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def resolve_date_marker(marker: str, scrape_dt: date) -> str | None:
    """Convert a date marker to ISO date. Returns None if unparseable."""
    marker = marker.strip()
    rel = REL_RE.match(marker)
    if rel:
        n = int(rel.group(1))
        unit = rel.group(2).lower()
        if unit == "d":
            delta = timedelta(days=n)
        elif unit == "w":
            delta = timedelta(weeks=n)
        elif unit in ("mo", "m"):
            delta = timedelta(days=n * 30)  # approximate
        elif unit in ("yr", "y"):
            delta = timedelta(days=n * 365)  # approximate
        else:
            return None
        return (scrape_dt - delta).isoformat()

    absm = ABS_RE.match(marker)
    if absm:
        mon_str = absm.group(1)
        day = int(absm.group(2))
        year = int(absm.group(3))
        mon = MONTHS.get(mon_str) or MONTHS_SHORT.get(mon_str[:3])
        if mon:
            try:
                return date(year, mon, day).isoformat()
            except ValueError:
                return None
    return None


def is_marker(line: str) -> bool:
    line = line.strip()
    return bool(REL_RE.match(line) or ABS_RE.match(line))


def parse_scrape(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    scrape_dt = scrape_date_from_filename(path)
    blocks = re.split(r"\n---+\n", text)

    posts: list[dict] = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        lines = block.split("\n")
        # Find the date marker — first non-empty line that matches
        marker = None
        marker_idx = None
        for i, line in enumerate(lines):
            s = line.strip()
            if not s:
                continue
            if is_marker(s):
                marker = s.strip("[]").strip() if not s.startswith("[") else s
                marker_idx = i
                break
            # Header-style block (file header) — stop scanning, this isn't a post
            if s.startswith("#") or s.startswith("- "):
                break

        if marker is None or marker_idx is None:
            continue  # not a post block (e.g., file header)

        # Body = everything after marker, minus engagement metrics lines
        body_lines = []
        reactions = comments = impressions = None
        for line in lines[marker_idx + 1 :]:
            s = line.strip()
            r = REACTIONS_RE.match(s)
            c = COMMENTS_RE.match(s)
            im = IMPRESSIONS_RE.match(s)
            if r:
                reactions = int(r.group(1).replace(",", ""))
                continue
            if c:
                comments = int(c.group(1).replace(",", ""))
                continue
            if im:
                v = im.group(1)
                impressions = None if v.upper() == "N/A" else int(v.replace(",", ""))
                continue
            body_lines.append(line)

        body = "\n".join(body_lines).strip()
        if not body or len(body.split()) < 5:
            continue  # skip empty/photo-only posts (no analyzable text)

        first_line = next((l.strip() for l in body_lines if l.strip()), "")
        absolute = resolve_date_marker(marker, scrape_dt)

        posts.append(
            {
                "date_marker": marker,
                "absolute_date": absolute,
                "body": body,
                "first_line": first_line[:200],
                "reactions": reactions,
                "comments": comments,
                "impressions": impressions,
                "char_count": len(body),
                "word_count": len(body.split()),
            }
        )

    return posts


def main():
    if len(sys.argv) != 2:
        print("Usage: parse_scrape.py <scrape.md>", file=sys.stderr)
        sys.exit(2)
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(2)
    posts = parse_scrape(path)
    out = {
        "scrape_file": str(path),
        "scrape_date": scrape_date_from_filename(path).isoformat(),
        "post_count": len(posts),
        "posts": posts,
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
