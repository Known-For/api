"""Match Notion content-DB pieces to LinkedIn scrape posts."""
import re
from difflib import SequenceMatcher
from typing import Any

WHITESPACE_RE = re.compile(r"\s+")

# Tuned for LinkedIn-style copy: a piece's body and the scraped post body
# usually share a long common prefix (the post text), so a relatively low
# fuzzy-ratio threshold gives high recall without false positives.
MATCH_THRESHOLD = 0.65


def _norm(text: str | None) -> str:
    if not text:
        return ""
    return WHITESPACE_RE.sub(" ", text.lower()).strip()


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _strip_query(url: str | None) -> str | None:
    if not url:
        return None
    return url.split("?")[0].rstrip("/")


def _score(
    piece_url: str | None, piece_text: str, post_url: str | None, post_body: str
) -> tuple[float, str]:
    pu = _strip_query(piece_url)
    qu = _strip_query(post_url)
    if pu and qu and pu == qu:
        return 1.0, "url"
    pn = _norm(piece_text)[:300]
    bn = _norm(post_body)[:300]
    return _ratio(pn, bn), "fuzzy"


def match(
    pieces: list[dict[str, Any]], scrape: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Annotate each piece with its best-matching post (if any)."""
    out: list[dict[str, Any]] = []
    for piece in pieces:
        best_post: dict[str, Any] | None = None
        best_score = 0.0
        best_method = "none"
        piece_text = f"{piece.get('title', '')}\n{piece.get('body', '')}"
        for post in scrape:
            score, method = _score(
                piece.get("url"),
                piece_text,
                post.get("url"),
                post.get("body", ""),
            )
            if score > best_score:
                best_score = score
                best_post = post
                best_method = method
        matched = best_score >= MATCH_THRESHOLD
        out.append(
            {
                **piece,
                "match": {
                    "matched": matched,
                    "score": round(best_score, 3),
                    "method": best_method if matched else "none",
                    "post": best_post if matched else None,
                },
            }
        )
    return out
