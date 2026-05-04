from datetime import date

from app.lib.parse_scrape import normalize, resolve_date


def test_resolve_relative_days():
    assert resolve_date("2d", date(2026, 5, 4)) == date(2026, 5, 2)


def test_resolve_relative_weeks():
    assert resolve_date("1w", date(2026, 5, 4)) == date(2026, 4, 27)


def test_resolve_relative_hours_returns_same_day():
    assert resolve_date("3h", date(2026, 5, 4)) == date(2026, 5, 4)


def test_resolve_absolute_date():
    assert resolve_date("April 3, 2026", date(2026, 5, 4)) == date(2026, 4, 3)


def test_resolve_now():
    assert resolve_date("now", date(2026, 5, 4)) == date(2026, 5, 4)


def test_resolve_unknown_returns_none():
    assert resolve_date(None, date(2026, 5, 4)) is None
    assert resolve_date("garbage", date(2026, 5, 4)) is None


def test_normalize_filters_outside_range():
    posts = [
        {"date_str": "1d", "body": "recent", "url": "u1", "reactions": 1, "comments": 0, "reposts": 0},
        {"date_str": "60d", "body": "old", "url": "u2", "reactions": 1, "comments": 0, "reposts": 0},
    ]
    out = normalize(
        posts,
        reference_date=date(2026, 5, 4),
        start_date=date(2026, 4, 1),
        end_date=date(2026, 5, 4),
    )
    assert len(out) == 1
    assert out[0]["url"] == "u1"
    assert out[0]["engagement"] == 1


def test_normalize_dedupes_same_url_and_body():
    posts = [
        {"date_str": "1d", "body": "same", "url": "u", "reactions": 1, "comments": 0, "reposts": 0},
        {"date_str": "1d", "body": "same", "url": "u", "reactions": 1, "comments": 0, "reposts": 0},
    ]
    out = normalize(posts, reference_date=date(2026, 5, 4))
    assert len(out) == 1


def test_normalize_keeps_distinct_urls():
    posts = [
        {"date_str": "1d", "body": "a", "url": "u1", "reactions": 1, "comments": 0, "reposts": 0},
        {"date_str": "1d", "body": "b", "url": "u2", "reactions": 1, "comments": 0, "reposts": 0},
    ]
    out = normalize(posts, reference_date=date(2026, 5, 4))
    assert len(out) == 2
