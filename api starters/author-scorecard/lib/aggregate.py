#!/usr/bin/env python3
"""
Compute Stage 1-4 scorecard metrics + diagnosis from parsed inputs.

Inputs (all JSON files):
  --scrape-json     output of parse_scrape.py for the LATEST scrape
  --baseline-json   output of parse_scrape.py for the BASELINE (earliest) scrape
                    Pass the same file as scrape-json if only one scrape exists.
  --match-json      output of match_pieces.py
  --sessions-json   [{"id": "...", "session_date": "YYYY-MM-DD", "title": "..."}]
  --pieces-json     same input that was given to match_pieces.py (so we can join)
  --start-date      YYYY-MM-DD inclusive
  --end-date        YYYY-MM-DD inclusive
  --author-name     display name (for output)
  --client-name     display name (for output)

Output: JSON with sections ready for Notion page assembly.

Diagnosis thresholds (from build brief):
  Publishing rate: <50% Watch; <25% Trouble  (only computed if delivered_count >= 3)
  Days since last session: >21 Watch; >35 Trouble
  Stalled pieces: >2 Watch; >5 Trouble
  Aggregate = worst stage diagnosis
"""

import argparse
import json
import statistics
from datetime import date, datetime
from pathlib import Path


def parse_iso(s):
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        return None


def in_range(d: date | None, start: date, end: date) -> bool:
    return d is not None and start <= d <= end


def compute_baseline_averages(baseline_posts: list[dict]) -> dict:
    """Average reactions/comments/impressions across all posts in baseline scrape."""
    reactions = [p["reactions"] for p in baseline_posts if p.get("reactions") is not None]
    comments = [p["comments"] for p in baseline_posts if p.get("comments") is not None]
    impressions = [
        p["impressions"] for p in baseline_posts if p.get("impressions") is not None
    ]
    return {
        "post_count": len(baseline_posts),
        "avg_reactions": statistics.mean(reactions) if reactions else None,
        "avg_comments": statistics.mean(comments) if comments else None,
        "avg_impressions": statistics.mean(impressions) if impressions else None,
    }


def pct_delta(current, baseline):
    if current is None or baseline is None or baseline == 0:
        return None
    return round((current - baseline) / baseline * 100, 1)


def diagnose_publishing_rate(rate, delivered_count):
    if delivered_count < 3:
        return ("insufficient", "Fewer than 3 deliveries in range — rate not meaningful")
    if rate is None:
        return ("unknown", "Rate not computed")
    if rate < 0.25:
        return ("trouble", f"Publishing rate {rate*100:.0f}% (threshold <25%)")
    if rate < 0.50:
        return ("watch", f"Publishing rate {rate*100:.0f}% (threshold <50%)")
    return ("healthy", f"Publishing rate {rate*100:.0f}%")


def diagnose_days_since_session(days):
    if days is None:
        return ("unknown", "No sessions on file")
    if days > 35:
        return ("trouble", f"{days} days since last session (threshold >35)")
    if days > 21:
        return ("watch", f"{days} days since last session (threshold >21)")
    return ("healthy", f"{days} days since last session")


def diagnose_stalled(count):
    if count > 5:
        return ("trouble", f"{count} stalled pieces (threshold >5)")
    if count > 2:
        return ("watch", f"{count} stalled pieces (threshold >2)")
    return ("healthy", f"{count} stalled pieces")


def worst(*labels):
    rank = {"healthy": 0, "insufficient": 1, "unknown": 1, "watch": 2, "trouble": 3}
    return max(labels, key=lambda x: rank.get(x, 0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scrape-json", required=True)
    ap.add_argument("--baseline-json", required=True)
    ap.add_argument("--match-json", required=True)
    ap.add_argument("--sessions-json", required=True)
    ap.add_argument("--pieces-json", required=True)
    ap.add_argument("--start-date", required=True)
    ap.add_argument("--end-date", required=True)
    ap.add_argument("--author-name", required=True)
    ap.add_argument("--client-name", required=True)
    args = ap.parse_args()

    scrape = json.loads(Path(args.scrape_json).read_text())
    baseline = json.loads(Path(args.baseline_json).read_text())
    match = json.loads(Path(args.match_json).read_text())
    sessions = json.loads(Path(args.sessions_json).read_text())
    pieces = json.loads(Path(args.pieces_json).read_text())

    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)
    today = date.today()

    pieces_by_id = {p["id"]: p for p in pieces}
    posts = scrape["posts"]

    gaps: list[str] = []

    # ---------- Stage 1: Session quality ----------
    if not sessions:
        gaps.append(
            f"Stage 1: No sessions found in {args.client_name} Resources for {args.author_name}."
        )
    sessions_in_range = [
        s for s in sessions if in_range(parse_iso(s.get("session_date")), start, end)
    ]
    all_session_dates = [parse_iso(s.get("session_date")) for s in sessions]
    all_session_dates = [d for d in all_session_dates if d is not None]
    last_session = max(all_session_dates) if all_session_dates else None
    days_since = (today - last_session).days if last_session else None

    s1_diag, s1_reason = diagnose_days_since_session(days_since)

    stage1 = {
        "sessions_held_in_range": len(sessions_in_range),
        "session_dates_in_range": [
            s.get("session_date") for s in sessions_in_range
        ],
        "last_session_date": last_session.isoformat() if last_session else None,
        "days_since_last_session": days_since,
        "diagnosis": s1_diag,
        "reason": s1_reason,
    }

    # ---------- Stage 2: Deliverable volume ----------
    pieces_in_range = [
        p
        for p in pieces
        if in_range(parse_iso(p.get("delivered_date")), start, end)
    ]
    stage2 = {
        "pieces_delivered_in_range": len(pieces_in_range),
        "piece_titles_in_range": [p.get("title") for p in pieces_in_range],
    }

    # ---------- Stage 3: Publishing rate ----------
    matched = match.get("matched", [])
    matched_ids = {m["piece_id"] for m in matched}

    # Of pieces delivered in range, how many published in scrape?
    delivered_ids_in_range = {p["id"] for p in pieces_in_range}
    delivered_in_range_matched = matched_ids & delivered_ids_in_range
    pub_rate = (
        len(delivered_in_range_matched) / len(pieces_in_range)
        if pieces_in_range
        else None
    )

    # Days delivery -> publish for matched pairs (regardless of delivery range; informative)
    deltas = []
    for m in matched:
        pid = m["piece_id"]
        piece = pieces_by_id.get(pid)
        if not piece:
            continue
        dd = parse_iso(piece.get("delivered_date"))
        pd = parse_iso(m.get("post_date"))
        if dd and pd and pd >= dd:
            deltas.append((pd - dd).days)
    avg_delivery_to_publish = (
        round(statistics.mean(deltas), 1) if deltas else None
    )

    # Stalled: delivered >30 days ago, not matched in latest scrape
    stalled = []
    for p in pieces:
        dd = parse_iso(p.get("delivered_date"))
        if dd and (today - dd).days > 30 and p["id"] not in matched_ids:
            stalled.append(
                {
                    "id": p["id"],
                    "title": p.get("title"),
                    "delivered_date": p.get("delivered_date"),
                    "days_since_delivery": (today - dd).days,
                }
            )
    stalled.sort(key=lambda s: s["days_since_delivery"], reverse=True)

    s3a_diag, s3a_reason = diagnose_publishing_rate(
        pub_rate, len(pieces_in_range)
    )
    s3b_diag, s3b_reason = diagnose_stalled(len(stalled))
    s3_diag = worst(s3a_diag, s3b_diag)

    stage3 = {
        "pct_delivered_published": round(pub_rate * 100, 1) if pub_rate is not None else None,
        "delivered_in_range_count": len(pieces_in_range),
        "delivered_in_range_published_count": len(delivered_in_range_matched),
        "avg_days_delivery_to_publish": avg_delivery_to_publish,
        "stalled_pieces": stalled,
        "stalled_count": len(stalled),
        "diagnosis": s3_diag,
        "reasons": [s3a_reason, s3b_reason],
    }

    # ---------- Stage 4: Platform performance ----------
    # KF-matched posts published in date range, by post_index
    matched_post_indices_in_range = []
    for m in matched:
        idx = m.get("post_index")
        if idx is None:
            continue
        post = posts[idx] if 0 <= idx < len(posts) else None
        if not post:
            continue
        pd = parse_iso(post.get("absolute_date"))
        if pd and start <= pd <= end:
            matched_post_indices_in_range.append(idx)

    in_range_posts = [posts[i] for i in matched_post_indices_in_range]

    def avg(field):
        vals = [p[field] for p in in_range_posts if p.get(field) is not None]
        return round(statistics.mean(vals), 1) if vals else None

    in_range_avg_reactions = avg("reactions")
    in_range_avg_comments = avg("comments")
    in_range_avg_impressions = avg("impressions")

    baseline_stats = compute_baseline_averages(baseline["posts"])
    baseline_same_as_latest = baseline.get("scrape_file") == scrape.get("scrape_file")

    if baseline_same_as_latest:
        gaps.append(
            "Stage 4: Baseline comparison unavailable — only one scrape exists for this author."
        )

    stage4 = {
        "kf_matched_posts_in_range": len(in_range_posts),
        "avg_reactions": in_range_avg_reactions,
        "avg_comments": in_range_avg_comments,
        "avg_impressions": in_range_avg_impressions,
        "baseline_scrape_date": baseline.get("scrape_date"),
        "baseline_avg_reactions": (
            None if baseline_same_as_latest else round(baseline_stats["avg_reactions"], 1)
            if baseline_stats["avg_reactions"] is not None
            else None
        ),
        "baseline_avg_comments": (
            None if baseline_same_as_latest else round(baseline_stats["avg_comments"], 1)
            if baseline_stats["avg_comments"] is not None
            else None
        ),
        "baseline_avg_impressions": (
            None if baseline_same_as_latest else baseline_stats["avg_impressions"]
        ),
        "baseline_unavailable": baseline_same_as_latest,
        "delta_pct_reactions": (
            None
            if baseline_same_as_latest
            else pct_delta(in_range_avg_reactions, baseline_stats["avg_reactions"])
        ),
        "delta_pct_comments": (
            None
            if baseline_same_as_latest
            else pct_delta(in_range_avg_comments, baseline_stats["avg_comments"])
        ),
        "delta_pct_impressions": (
            None
            if baseline_same_as_latest
            else pct_delta(in_range_avg_impressions, baseline_stats["avg_impressions"])
        ),
    }

    if all(p.get("impressions") is None for p in posts):
        gaps.append(
            "Stage 4: Impressions absent from scrape (LinkedIn does not expose them publicly). Reactions/comments only."
        )

    if not in_range_posts:
        gaps.append(
            "Stage 4: Zero KF-matched posts published in the date range — averages cannot be computed."
        )
    elif len(in_range_posts) < 3:
        gaps.append(
            f"Stage 4: Only {len(in_range_posts)} KF-matched post(s) in range — deltas vs baseline reflect a single-post sample with high variance, not a stable trend."
        )

    # Scrape staleness — the date range extends past the latest scrape, so
    # Stage 3 (publishing rate) and Stage 4 (in-range performance) are both
    # underrepresented for any deliveries or posts after scrape_date.
    scrape_date = parse_iso(scrape.get("scrape_date"))
    if scrape_date and end > scrape_date:
        gap_days = (end - scrape_date).days
        gaps.append(
            f"Scrape is {gap_days} days older than end_date. Stage 3 publishing rate and stalled count are biased low; Stage 4 in-range performance excludes posts from the last {gap_days} days. Run a fresh scrape and regenerate for an accurate picture."
        )

    # Pieces delivered after the latest scrape can't possibly be matched —
    # surface them so the publishing rate isn't misread.
    after_scrape_in_range = [
        p for p in pieces_in_range
        if scrape_date and parse_iso(p.get("delivered_date")) and parse_iso(p["delivered_date"]) > scrape_date
    ]
    if after_scrape_in_range:
        titles = ", ".join(f'"{p.get("title")}"' for p in after_scrape_in_range[:5])
        gaps.append(
            f"Stage 3: {len(after_scrape_in_range)} of {len(pieces_in_range)} pieces delivered in range were delivered AFTER the scrape ({scrape.get('scrape_date')}) and cannot be in it. They count as 'unpublished' in the rate but may be published. Affected: {titles}."
        )

    # Match-confidence breakdown
    confidence_breakdown = {"high": 0, "medium": 0, "low": 0}
    for m in matched:
        confidence_breakdown[m.get("confidence", "low")] = (
            confidence_breakdown.get(m.get("confidence", "low"), 0) + 1
        )

    # ---------- Aggregate diagnosis ----------
    aggregate = worst(s1_diag, s3_diag)

    diag_label_map = {
        "healthy": "Healthy",
        "watch": "Watch",
        "trouble": "Trouble",
        "insufficient": "Watch",  # treat insufficient data as Watch overall
        "unknown": "Watch",
    }

    # Top issue + most leveraged action
    top_issue = "No issues surfaced in this window"
    leveraged_action = "Continue current cadence"
    if aggregate in ("trouble", "watch"):
        # Pick the worst signal
        if s1_diag in ("trouble", "watch"):
            top_issue = s1_reason
            if s1_diag == "trouble":
                leveraged_action = (
                    "Re-engage author on session cadence — gap is past intervention threshold"
                )
            else:
                leveraged_action = "Schedule the next Signal Session this week"
        elif s3_diag in ("trouble", "watch"):
            if s3a_diag in ("trouble", "watch"):
                top_issue = s3a_reason
                leveraged_action = (
                    "Investigate publishing bottleneck — drafts delivered but not appearing on LinkedIn"
                )
            elif s3b_diag in ("trouble", "watch"):
                top_issue = s3b_reason
                leveraged_action = (
                    "Review the stalled list with the author — kill, revise, or unblock each"
                )

    return_obj = {
        "meta": {
            "author_name": args.author_name,
            "client_name": args.client_name,
            "start_date": args.start_date,
            "end_date": args.end_date,
            "generated_date": today.isoformat(),
            "scrape_file": scrape.get("scrape_file"),
            "scrape_date": scrape.get("scrape_date"),
            "baseline_scrape_date": baseline.get("scrape_date"),
            "baseline_unavailable": baseline_same_as_latest,
        },
        "header": {
            "diagnosis_label": diag_label_map[aggregate],
            "diagnosis_raw": aggregate,
            "top_issue": top_issue,
            "leveraged_action": leveraged_action,
        },
        "stage1": stage1,
        "stage2": stage2,
        "stage3": stage3,
        "stage4": stage4,
        "match_diagnostics": {
            "total_pieces": len(pieces),
            "matched": len(matched),
            "unmatched": len(match.get("unmatched_pieces", [])),
            "confidence_breakdown": confidence_breakdown,
        },
        "data_gaps": gaps,
    }

    print(json.dumps(return_obj, indent=2, default=str))


if __name__ == "__main__":
    main()
