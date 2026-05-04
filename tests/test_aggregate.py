from datetime import date

from app.lib.aggregate import aggregate


def _piece(id_, title, stage, matched, engagement, piece_date=None):
    return {
        "id": id_,
        "title": title,
        "stage": stage,
        "url": None,
        "body": "",
        "date": piece_date,
        "match": {
            "matched": matched,
            "score": 0.9 if matched else 0.1,
            "method": "url" if matched else "none",
            "post": (
                {
                    "engagement": engagement,
                    "url": f"https://l/{id_}",
                    "date": piece_date,
                }
                if matched
                else None
            ),
        },
    }


def test_aggregate_basic_totals():
    pieces = [
        _piece("p1", "A", "Published", True, 50, "2026-04-15"),
        _piece("p2", "B", "Draft", False, 0, "2026-04-20"),
    ]
    s = aggregate(
        client_slug="bain",
        author_slug="chuck-whitten",
        author_name="Chuck Whitten",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 5, 1),
        matched_pieces=pieces,
        sessions=[{"id": "s1", "title": "Session 1", "date": "2026-04-10"}],
        scrape=[{"url": "u1", "engagement": 50}],
    )
    assert s["totals"]["pieces"] == 2
    assert s["totals"]["pieces_matched"] == 1
    assert s["totals"]["pieces_unmatched"] == 1
    assert s["totals"]["sessions"] == 1
    assert s["stages"] == {"Published": 1, "Draft": 1}
    assert s["top_pieces"][0]["title"] == "A"
    assert s["author"]["name"] == "Chuck Whitten"
    assert s["date_range"] == {"start": "2026-04-01", "end": "2026-05-01"}


def test_aggregate_filters_pieces_outside_range():
    pieces = [
        _piece("p1", "A", "Published", True, 50, "2026-04-15"),
        _piece("p2", "B", "Published", True, 99, "2026-01-01"),
    ]
    s = aggregate(
        client_slug="bain",
        author_slug="chuck-whitten",
        author_name="Chuck Whitten",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 5, 1),
        matched_pieces=pieces,
        sessions=[],
        scrape=[],
    )
    assert s["totals"]["pieces"] == 1
    assert s["pieces"][0]["id"] == "p1"


def test_aggregate_undated_piece_passes_through():
    pieces = [_piece("p1", "A", "Published", False, 0, None)]
    s = aggregate(
        client_slug="bain",
        author_slug="chuck-whitten",
        author_name="Chuck Whitten",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 5, 1),
        matched_pieces=pieces,
        sessions=[],
        scrape=[],
    )
    assert s["totals"]["pieces"] == 1
