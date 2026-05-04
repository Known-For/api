#!/usr/bin/env python3
"""
Match KF-delivered pieces against scrape posts via distinctive-phrase grep.

Approach (mirrors audit-author-edits, automated):
  For each delivered piece body:
    1. Extract candidate distinctive phrases:
       - Capitalized multi-word entities (e.g., "Yum! Brands", "Sarah Elk", "Mobile World Congress")
       - Numbers with units / specific magnitudes (e.g., "700 drive-throughs", "$1 trillion", "5% to 10%")
       - 6+ word ngrams from the opening that are not stopword-heavy
    2. Score phrases by distinctiveness and keep top N (default 5)
    3. For each candidate, search every scrape post body (case-insensitive substring)
    4. Match piece -> first scrape post that hits any candidate phrase
       (with a body-overlap bias when multiple posts hit)
  A piece with zero hits is unmatched (treat as not-yet-published or heavily rewritten).

Input:
  --pieces-json  JSON: [{"id": "...", "title": "...", "body": "...", "delivered_date": "YYYY-MM-DD"}, ...]
  --scrape-json  Output of parse_scrape.py

Output:
  JSON: {"matched": [{"piece_id": ..., "post_index": ..., "matched_on": "phrase", "post_date": ..., "confidence": "high|medium|low"}],
         "unmatched_pieces": [piece_id, ...],
         "unmatched_posts": [post_index, ...],
         "phrase_pool": {piece_id: [candidate phrases]}}

Confidence:
  high   - 3+ distinct phrase hits OR one entity+number combo
  medium - 1 distinct phrase hit (named entity or unusual ngram)
  low    - 1 boilerplate-adjacent hit (e.g., common ngram)
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


STOPWORDS = set(
    """
a an the and or but if then so as at by for from in into of on or to with up down out over under
is are was were be been being am have has had do does did doing
i you he she it we they me him her us them my your his their our its
this that these those there here what which who whom when where why how
not no nor very just only also too more most much many some any all every each
""".split()
)

# Common KF-author boilerplate to avoid as a "distinctive" phrase
BOILERPLATE_TERMS = {
    "my colleague",
    "my colleagues",
    "in our work",
    "i think",
    "i believe",
    "i wrote",
    "thrilled to announce",
    "excited to share",
    "ai transformation",
    "leadership teams",
    "competitive advantage",
    "strategic priority",
    "change management",
    "digital transformation",
    "operating model",
    "value creation",
    "go to market",
    "go-to-market",
}

# Topic-vocabulary terms that look like named entities (capitalized) but are
# not distinctive — they appear across many posts and authors. Reject as entity matches.
TOPIC_VOCAB_ENTITIES = {
    "agentic ai",
    "generative ai",
    "artificial intelligence",
    "machine learning",
    "deep learning",
    "ai transformation",
    "digital transformation",
    "cloud computing",
    "data science",
    "the ai era",
    "the ai wave",
    "ai era",
    "ai wave",
    "post-quantum cryptography",
    "private equity",
    "venture capital",
    "operating model",
    "operating models",
    "value creation",
    "talent strategy",
    "supply chain",
    "supply chains",
    "the cloud",
    "the internet",
    "open ai",
    "winning with ai",  # common Bain podcast title — not distinctive across pieces
}


def is_topic_vocab(phrase: str) -> bool:
    return phrase.lower().strip() in TOPIC_VOCAB_ENTITIES


# ---------- Phrase extractors ----------

ENTITY_RE = re.compile(
    r"([A-Z][a-zA-Z0-9&'\-]*[!?]?(?:\s+[A-Z][a-zA-Z0-9&'\-]*[!?]?){1,4})"
)
NUMBER_PHRASE_RE = re.compile(
    r"(?:\$|€|£)?\s*\d[\d,.]*\s*(?:%|\+|-|–|to|million|billion|trillion|thousand|drive-throughs?|drive\s+throughs?|customers?|employees?|companies|companies?|years?|weeks?|days?|months?|x|cents?|basis\s+points|bps|MW|GW|hours?|seconds?|minutes?)\b",
    re.IGNORECASE,
)
QUOTED_RE = re.compile(r'"([^"]{8,80})"')
NUMBER_DASH_NUMBER_RE = re.compile(r"\b\d+\s*[-–]\s*\d+\s*%?\b")


def is_stopword_heavy(phrase: str) -> bool:
    words = [w.lower() for w in re.findall(r"\b\w+\b", phrase)]
    if not words:
        return True
    stop_count = sum(1 for w in words if w in STOPWORDS)
    return stop_count / len(words) > 0.55


def is_boilerplate(phrase: str) -> bool:
    pl = phrase.lower().strip()
    return any(b in pl for b in BOILERPLATE_TERMS)


SENT_START_SKIP = {
    "the", "a", "an", "and", "but", "or", "so", "this", "that", "these",
    "those", "it", "they", "we", "i", "he", "she", "what", "who", "why",
    "how", "when", "where", "as", "if", "now", "yet", "still", "more",
    "most", "consider", "imagine", "think", "remember", "look", "see",
}


def extract_named_entities(text: str) -> list[str]:
    """Multi-word capitalized entities. Skip leading sentence-start filler words."""
    candidates = []
    for m in ENTITY_RE.finditer(text):
        phrase = m.group(1).strip()
        words = phrase.split()
        if len(words) < 2:
            continue
        # Trim leading sentence-start filler word if present
        while words and words[0].lower() in SENT_START_SKIP:
            words.pop(0)
        if len(words) < 2:
            continue
        phrase = " ".join(words)
        if is_boilerplate(phrase):
            continue
        if is_topic_vocab(phrase):
            continue
        candidates.append(phrase)
    seen = set()
    deduped = []
    for c in candidates:
        key = c.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(c)
    return deduped


def extract_number_phrases(text: str) -> list[str]:
    candidates = []
    for m in NUMBER_PHRASE_RE.finditer(text):
        phrase = m.group(0).strip().rstrip(".,")
        candidates.append(phrase)
    for m in NUMBER_DASH_NUMBER_RE.finditer(text):
        candidates.append(m.group(0).strip())
    seen = set()
    return [c for c in candidates if not (c.lower() in seen or seen.add(c.lower()))]


def extract_quoted(text: str) -> list[str]:
    return [m.group(1).strip() for m in QUOTED_RE.finditer(text)]


def extract_distinctive_ngrams(text: str, n: int = 6, max_take: int = 4) -> list[str]:
    """6-word ngrams with at most 2 stopwords, drawn from the opening 1/3 of the body."""
    cleaned = re.sub(r"https?://\S+", " ", text)
    words = re.findall(r"\b[\w'\-]+\b", cleaned)
    cap = max(40, len(words) // 3)
    words = words[:cap]
    out = []
    seen_keys = set()
    for i in range(len(words) - n + 1):
        gram = words[i : i + n]
        gram_lower = [w.lower() for w in gram]
        stop_ratio = sum(1 for w in gram_lower if w in STOPWORDS) / n
        if stop_ratio > 0.4:
            continue
        joined = " ".join(gram)
        if is_boilerplate(joined):
            continue
        # Penalize all-lowercase common-vocab grams
        has_signal = any(
            re.search(r"\d", w) or w[0].isupper() or len(w) >= 9 for w in gram
        )
        if not has_signal:
            continue
        key = " ".join(gram_lower)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        out.append(joined)
        if len(out) >= max_take:
            break
    return out


def extract_phrases(body: str, max_phrases: int = 6) -> list[dict]:
    """Return a ranked list of {phrase, kind, weight}."""
    phrases: list[dict] = []
    seen = set()

    def add(phrase, kind, weight):
        key = phrase.lower().strip()
        if key in seen or len(key) < 4:
            return
        if is_stopword_heavy(phrase):
            return
        seen.add(key)
        phrases.append({"phrase": phrase, "kind": kind, "weight": weight})

    for e in extract_named_entities(body):
        add(e, "entity", 3)
    for n in extract_number_phrases(body):
        add(n, "number", 4)
    for q in extract_quoted(body):
        add(q, "quote", 4)
    for g in extract_distinctive_ngrams(body):
        add(g, "ngram", 2)

    phrases.sort(key=lambda p: -p["weight"])
    return phrases[:max_phrases]


# ---------- Matcher ----------


def find_hits(phrase: str, post_bodies: list[str]) -> list[int]:
    needle = phrase.lower()
    hits = []
    for idx, body in enumerate(post_bodies):
        if needle in body.lower():
            hits.append(idx)
    return hits


def confidence_label(hit_count: int, hit_kinds: list[str]) -> str:
    distinct_kinds = set(hit_kinds)
    if hit_count >= 3:
        return "high"
    if {"entity", "number"} <= distinct_kinds or {"entity", "quote"} <= distinct_kinds:
        return "high"
    if hit_count == 2:
        return "medium"
    if hit_count == 1:
        return "medium" if distinct_kinds & {"entity", "number", "quote"} else "low"
    return "low"


def match(pieces: list[dict], scrape_posts: list[dict]) -> dict:
    post_bodies = [p["body"] for p in scrape_posts]

    matched: list[dict] = []
    unmatched_pieces: list[str] = []
    matched_post_indices: set[int] = set()
    phrase_pool: dict[str, list[dict]] = {}

    # Score each piece against posts; prefer closer date / longer body overlap on ties
    for piece in pieces:
        pid = piece["id"]
        body = piece.get("body") or ""
        if len(body.split()) < 30:
            unmatched_pieces.append(pid)
            phrase_pool[pid] = []
            continue

        candidates = extract_phrases(body)
        phrase_pool[pid] = candidates
        if not candidates:
            unmatched_pieces.append(pid)
            continue

        # Tally hits per post
        post_hits: dict[int, list[dict]] = defaultdict(list)
        for cand in candidates:
            for idx in find_hits(cand["phrase"], post_bodies):
                post_hits[idx].append(cand)

        if not post_hits:
            unmatched_pieces.append(pid)
            continue

        # Pick the post with the most hits; tie-break by: not-already-matched > closer date
        def post_score(idx: int) -> tuple:
            hits = post_hits[idx]
            distinct_kinds = len({h["kind"] for h in hits})
            available = 0 if idx in matched_post_indices else 1
            # Date proximity bonus
            date_bonus = 0
            ppd = piece.get("delivered_date")
            spd = scrape_posts[idx].get("absolute_date")
            if ppd and spd:
                from datetime import date

                try:
                    pd_d = date.fromisoformat(ppd)
                    sp_d = date.fromisoformat(spd)
                    # Pieces typically publish within ~30 days of delivery.
                    days = abs((sp_d - pd_d).days)
                    if days <= 30:
                        date_bonus = 2
                    elif days <= 60:
                        date_bonus = 1
                    elif days > 180:
                        date_bonus = -1
                except Exception:
                    pass
            return (len(hits), distinct_kinds, available, date_bonus)

        best_idx = max(post_hits.keys(), key=post_score)
        hits = post_hits[best_idx]
        matched.append(
            {
                "piece_id": pid,
                "piece_title": piece.get("title"),
                "delivered_date": piece.get("delivered_date"),
                "post_index": best_idx,
                "post_date": scrape_posts[best_idx].get("absolute_date"),
                "post_first_line": scrape_posts[best_idx].get("first_line"),
                "matched_phrases": [h["phrase"] for h in hits],
                "matched_kinds": [h["kind"] for h in hits],
                "confidence": confidence_label(
                    len(hits), [h["kind"] for h in hits]
                ),
            }
        )
        matched_post_indices.add(best_idx)

    unmatched_posts = [
        i for i in range(len(scrape_posts)) if i not in matched_post_indices
    ]

    return {
        "matched": matched,
        "unmatched_pieces": unmatched_pieces,
        "unmatched_posts": unmatched_posts,
        "phrase_pool": {pid: [c["phrase"] for c in cands] for pid, cands in phrase_pool.items()},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pieces-json", required=True)
    ap.add_argument("--scrape-json", required=True)
    args = ap.parse_args()

    pieces = json.loads(Path(args.pieces_json).read_text())
    scrape = json.loads(Path(args.scrape_json).read_text())
    result = match(pieces, scrape["posts"])
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
