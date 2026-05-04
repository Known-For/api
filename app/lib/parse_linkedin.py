"""Parse a raw LinkedIn 'All activity' page paste into structured posts.

LinkedIn's HTML-as-text paste is noisy and varies by browser, so this parser
is heuristic. Posts are delimited by an engagement footer line that contains
counts of reactions / comments / reposts. Date strings are extracted but
NOT resolved to absolute dates here (see parse_scrape.normalize for that).
"""
import re
from typing import Any

# Examples that should match: "2d", "1w", "3mo", "1yr", "April 3, 2026", "Apr 3 2026"
DATE_PATTERN = re.compile(
    r"\b("
    r"now|"
    r"\d+\s*(?:s|m|h|d|w|mo|yr)|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"[a-z]*\s+\d{1,2}(?:,?\s+\d{4})?"
    r")\b",
    re.IGNORECASE,
)

ENGAGEMENT_PATTERN = re.compile(
    r"(?P<reactions>\d[\d,]*)\s*(?:reactions?|likes?|impressions?)"
    r"(?:\s*[•·\-\|,]?\s*(?P<comments>\d[\d,]*)\s*comments?)?"
    r"(?:\s*[•·\-\|,]?\s*(?P<reposts>\d[\d,]*)\s*reposts?)?",
    re.IGNORECASE,
)

URL_PATTERN = re.compile(r"https?://[^\s\)]+")


def _to_int(s: str | None) -> int:
    if not s:
        return 0
    return int(s.replace(",", ""))


def _split_into_blocks(text: str) -> list[str]:
    """Split the raw paste into per-post blocks at each engagement-footer line."""
    lines = text.splitlines()
    blocks: list[list[str]] = [[]]
    for line in lines:
        blocks[-1].append(line)
        if ENGAGEMENT_PATTERN.search(line):
            blocks.append([])
    out: list[str] = []
    for chunk in blocks:
        joined = "\n".join(chunk).strip()
        if joined:
            out.append(joined)
    return out


def parse(text: str, author_name: str | None = None) -> list[dict[str, Any]]:
    """Parse a LinkedIn paste into a list of post records.

    Each record has:
      raw, date_str, body, url, reactions, comments, reposts
    """
    blocks = _split_into_blocks(text)
    results: list[dict[str, Any]] = []

    for block in blocks:
        engagement_match = ENGAGEMENT_PATTERN.search(block)
        if not engagement_match:
            continue

        date_match = DATE_PATTERN.search(block)
        date_str = date_match.group(1) if date_match else None

        body = block[: engagement_match.start()]
        if author_name:
            # If the block opens with "Author Name\nHeadline\nDate", trim
            # the first three lines down to just the body.
            lines = body.splitlines()
            if lines and author_name.lower() in lines[0].lower():
                # Drop author + headline + date lines (1-3 lines)
                drop = 1
                while drop < min(3, len(lines)) and not lines[drop].strip().startswith("http"):
                    if DATE_PATTERN.fullmatch(lines[drop].strip() or "x"):
                        drop += 1
                        break
                    drop += 1
                body = "\n".join(lines[drop:])

        body = body.strip()
        if date_str and body.endswith(date_str):
            body = body[: -len(date_str)].rstrip()

        urls = URL_PATTERN.findall(block)
        post_url = next((u for u in urls if "linkedin.com" in u), urls[0] if urls else None)

        results.append(
            {
                "raw": block,
                "date_str": date_str,
                "body": body,
                "url": post_url,
                "reactions": _to_int(engagement_match.group("reactions")),
                "comments": _to_int(engagement_match.group("comments")),
                "reposts": _to_int(engagement_match.group("reposts")),
            }
        )

    return results
